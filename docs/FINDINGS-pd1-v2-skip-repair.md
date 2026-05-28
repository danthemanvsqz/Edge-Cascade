# FINDINGS — PD-1 v2 skip-repair: SHIP_NEXT_LEVER (treatment +22.8 pp better than control, P(trt>ctrl)=1.000)

**Date:** 2026-05-28 · **Hardware:** Intel AI Boost NPU + RTX 5070 Ti Laptop · **Substrate:** local cascade (in-process workers), $0

**Evidence (do not merge / do not delete):** `experiment/pd1-v2-skip-repair-2026-05-27` @ `691b4e2` — script `scripts/skip_repair_validation.py`, per-subject checkpoints `runs/skip-repair-validation/subject-{00,01,02}.json`, Phase-3 summary `runs/skip-repair-validation/summary.json`, Phase-2 stdout `runs/skip-repair-validation/run.log`. Subject pool reused from warn-prompt v2 (`runs/warn-prompt-validation-v2/subjects.jsonl`).

## TL;DR — skip-repair RESCUES the cascade where warn-prompt couldn't

The warn-prompt lever (`docs/FINDINGS-pd1-v2-warn-prompt.md`, #69) tried to *tell the repair model* about the prior draft's degeneration so it could avoid the failure mode. That lost decisively (-4.4 pp pooled, REVERT). The skip-repair lever takes the opposite move: when the NPU draft trips the degen detector at the floor, the prior is treated as *poisoned* — the cascade discards it and lets the GPU phase issue a **fresh `generate`** instead of repairing on the degraded prior.

**Pooled result on the same subject pool:** ctrl 62/90 (mean 0.685), trt 83/90 (mean 0.913), P(trt>ctrl) = **1.000**, effect = **+22.8 pp**. The pre-registered decision rule (ship if P ≥ 0.90 AND effect ≥ 10 pp) → **SHIP_NEXT_LEVER**. The load-bearing subject (`decode_string`, FLOOR regime) moves from 6/30 (ctrl) to 23/30 (trt), effect = **+53.0 pp**.

The mechanism story is simple and consistent with the v2 calibration finding: at our thresholds, a high-score NPU draft is a reliable *negative* prior — feeding it into the bounded repair loop makes the GPU fixate on its failure mode. Discarding it costs only the early-exit speed-up and recovers most of the trial outcomes that the repair-on-poison path was failing.

## The decisive table

| Subject     | Task                | Stored score | ctrl pass/N (mean) | trt pass/N (mean) | P(trt>ctrl) | Effect      | Regime  | Lever fired? |
|-------------|---------------------|-------------:|--------------------|--------------------|------------:|------------:|---------|-------------:|
| subject-00  | `decode_string`     | 0.350        | 6/30 (0.220)       | 23/30 (0.750)      | 1.000       | **+53.0 pp** | FLOOR   | 30/30        |
| subject-01  | `calculator_basic`  | 0.350        | 26/30 (0.843)      | 30/30 (0.969)      | 1.000       | **+12.6 pp** | MID     | 30/30        |
| subject-02  | `evaluate_postfix`  | 0.175        | 30/30 (0.969)      | 30/30 (0.969)      | 0.182       | -0.04 pp    | NULL    | 0/30         |
| **POOLED**  | —                   | —            | 62/90 (0.685)      | 83/90 (0.913)      | **1.000**   | **+22.8 pp** | —       | —            |

95% CIs (Beta(1+pass, 1+fail), 10 000 MC draws, seed=27): pooled ctrl [0.588, 0.774]; pooled trt [0.847, 0.961]. Per-subject CIs in `summary.json`.

### Sub-pool split — the load-bearing invariant

| Sub-pool | Definition                                                     | ctrl pass/N (mean) | trt pass/N (mean) | P(trt>ctrl) | Effect      |
|----------|----------------------------------------------------------------|--------------------|--------------------|------------:|------------:|
| FIRED    | Subjects above floor (`decode_string`, `calculator_basic`)     | 32/60 (0.533)      | 53/60 (0.870)      | **1.000**   | **+33.7 pp** |
| NULL     | Subjects below floor (`evaluate_postfix`)                      | 30/30 (0.969)      | 30/30 (0.969)      | 0.148       | -0.03 pp    |

The NULL sub-pool is the load-bearing safety check: on subjects where the trigger never fires (degen score 0.175 < 0.30 floor), control and treatment are structurally identical and the data agrees — 30/30 on both arms, posterior effect = -0.03 pp. The lever does not damage the cascade on inputs it isn't supposed to touch.

## Method

- **Subject pool reused (no new Phase 1).** Same `runs/warn-prompt-validation-v2/subjects.jsonl` as the warn-prompt v2 study. That keeps the two studies apples-to-apples comparable on the same parser/interpreter battery: floor (`decode_string`), mid (`calculator_basic`), and a deliberate null below the floor (`evaluate_postfix`).
- **Treatment vs control happens INSIDE `cascade.mesh.solve`.** The lever is `CONFIG.skip_repair_on_degen` (default `False`). When true, the NPU draft's observation is checked against `CONFIG.skip_repair_score_floor=0.30`; if degen score is at or above the floor, `prior`, `failures`, and `prior_degen` are all set to `None` / `()` before the GPU phase, which then takes the `prior is None` branch and issues a fresh `generate(query)` instead of running the bounded repair loop. Trace line `skip-repair: <tier> degen score=X.XX >= 0.30 -> discard prior, fresh GPU` is the per-trial marker.
- **Functional gate (not syntax-only).** Each subject has its own DSL (already self-tested at module load in the warn-prompt v2 work, PR #69). The experiment Ops bundle replaces the production syntax gate with a per-subject `verify_functional` call so a "pass" means the candidate's outputs match the contract, not that it merely parses.
- **Phase 2 (A/B sweep):** per subject, 30 control + 30 treatment full-`mesh.solve` trials interleaved (ctrl, trt, ctrl, trt, …). Tier-2 model: `qwen2.5-coder:14b` via Ollama, default token budget. Per-subject JSON checkpoint after every trial → idempotent re-entry (the run actually resumed once at subject-02 trial 3 after a stall, which `_subject_checkpoint_path`'s `completed = len(ckpt["trials"])` handled cleanly). Windows `keep_awake` wake-lock held for the whole sweep.
- **Mean latency per trial:** ~30 s on the FIRED subjects (NPU draft + GPU generate, no repair shortcut), ~20 s on the NULL subject (lever doesn't fire, lands the GPU-repair path as before). Treatment trims ~1.4 s/trial on FIRED subjects vs control: a fresh generate is comparable to a repair turn on this model.
- **Phase 3 (analysis):** Beta(1+pass, 1+fail) posterior per arm × 10 000 Monte Carlo draws (seed=27) → per-subject, pooled, and the FIRED/NULL sub-pool slices. Same seed as warn-prompt v2 so the two studies are directly comparable.

## What this finding does and doesn't say

- **Says:** discarding a poisoned NPU prior + issuing a fresh GPU generate beats repairing on the prior, on this parser/interpreter battery, at the v2-calibrated 0.30 floor. The effect is large, the FIRED-only effect is larger, and the NULL invariant is clean.
- **Does NOT say:** that we should escalate poisoned drafts to Tier 3 instead of GPU (that's the separate `hard-escalate` lever; this experiment doesn't address it). The skip-repair lever stays inside Tier 2 — it just changes *how* Tier 2 is invoked.
- **Does NOT say:** that warn-prompt and skip-repair are alternatives that should be re-evaluated together. They make incompatible bets and we already have the warn-prompt verdict (REVERT). The two are sequenced, not paired.
- **Comparison to warn-prompt v2 (same pool, same seed):** warn-prompt pooled effect = **-4.4 pp** (REVERT); skip-repair pooled effect = **+22.8 pp** (SHIP). On `calculator_basic` specifically: warn-prompt = -6.2 pp, skip-repair = +12.6 pp. The two levers move the same subject in opposite directions.

## Caveats / limits

- **Tight N at the subject layer.** 3 subjects (1 FLOOR + 1 MID + 1 NULL) is small. Pooled P=1.000 comes mostly from the per-trial sample size (90 ctrl + 90 trt). A wider Phase-1 yield (more drafts per task, or expanding the candidate battery) would tighten the conclusion. The directional consistency across both FIRED subjects (`+53 pp` and `+12.6 pp`, same sign) plus the clean NULL (-0.03 pp) is the strongest part of the evidence; the absolute pooled +22.8 pp is the weakest because it's a single pool average.
- **Effect is regime-dependent.** The `decode_string` +53 pp gain is on a low-baseline subject (ctrl 0.220) — there's a lot of headroom for *any* recovery move to look big. `calculator_basic` at +12.6 pp on a high baseline (ctrl 0.843) is the more conservative read of the lever's value. The NULL `evaluate_postfix` (ctrl 0.969) has no headroom and rightly produces no effect. Production gain will track whichever regime the live cascade actually visits — i.e., depends on what fraction of NPU drafts trip the 0.30 floor and what their gate-fail rate is on the repair loop today.
- **Score floor is fixed at 0.30.** The v2 calibration (`docs/FINDINGS-pd1-v2-calibration.md`) achieves 0% FP on 27 correct-code negatives at this floor. A different floor would change which drafts trigger the lever and could shift the FIRED/NULL split. The lever is `CONFIG.skip_repair_score_floor`, so it can be tuned without code changes if a future regression shows ambient over-trip.
- **GPU "fresh generate" is not free.** On FIRED subjects, treatment costs roughly one full GPU generate where control would have run one repair turn before deciding. We measured a small *positive* time delta (~1.4 s/trial faster for trt) because the cap-2 repair loop sometimes spent its second round on a drift-fixated prior — but a deeper repair loop or a different model could flip the latency sign. Quality remains the primary metric per [[metric-priorities-quality-cost-over-latency]]; latency reported as a tiebreaker, not a goal.
- **Phase-3 RNG sharing.** `_beta_ci` consumes the same `random.Random(MC_SEED)` across all calls, so per-subject and pooled posteriors share an RNG stream. Same shape as the warn-prompt v2 doc; doesn't affect the verdict (pooled P=1.000 is robust to seed).

## Reproduce

```powershell
cd C:\Users\danth\src\edge-cascade
git switch experiment/pd1-v2-skip-repair-2026-05-27   # HEAD at 691b4e2
uv run python scripts/skip_repair_validation.py --phase 2   # idempotent; skips done subjects
uv run python scripts/skip_repair_validation.py --phase 3   # writes summary.json
```

Expected: `summary.json` reports `pooled.p_trt_gt_ctrl = 1.0`, `pooled.effect_pp ≈ +22.8`, `verdict = "SHIP_NEXT_LEVER"`. Phase 2 is deterministic up to GPU sampling noise; Phase 3 is fully deterministic given the persisted checkpoints (Monte Carlo seed is `27`).

For a fast smoke (N=2/arm, separate output dir so it doesn't contaminate the full run's resume checkpoints):

```powershell
uv run python scripts/skip_repair_validation.py --smoke
```

## What unblocks next

- **Turn skip-repair ON by default in the cascade.** The lever already lives in `cascade/mesh.py:179-193` behind `CONFIG.skip_repair_on_degen` (default `False`). The action commit flips the default to `True` and updates `tests/test_mesh.py`'s default-off pin to a default-on pin. Keep the env-var opt-out (`CASCADE_SKIP_REPAIR_ON_DEGEN=0`) so production can roll back without a code change if a regression surfaces.
- **Telemetry is already in place.** The `skip-repair: <tier> degen score=X.XX >= 0.30 -> discard prior, fresh GPU` trace line fires whenever the lever discards, and `SD-4` (mesh effectiveness panel, PR #71) already exposes `resolvedGpu` and `capped` rates — the production cascade rate change will show up there. No new dashboard work needed for the rollout.
- **PD-1 v2 `hard-escalate` is now the open lever.** Promote a degenerate draft straight to Tier 3 instead of GPU. This finding doesn't validate or invalidate it; the natural next experiment is an A/B with skip-repair as the new baseline. Per the prior open-threads note, it needs an ADR first to define what "promote to Tier 3" means in the in-process cascade vs the agent loop.
- **Re-calibration of the 0.30 floor remains scoped but not blocking.** The 0% FP property is what makes acting on the signal safe today. If a future Phase-1 yield from a broader battery shifts the FIRED/NULL split, revisit the floor before turning on more action levers.
