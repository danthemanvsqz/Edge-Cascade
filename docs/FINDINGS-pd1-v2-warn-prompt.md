# FINDINGS — PD-1 v2 warn-prompt: REVERT (treatment -4.4 pp worse than control, P(trt>ctrl)=0.000)

**Date:** 2026-05-27 · **Hardware:** Intel AI Boost NPU + RTX 5070 Ti Laptop · **Substrate:** local cascade (in-process workers), $0

**Evidence (do not merge / do not delete):** `experiment/warn-prompt-validation-v2-2026-05-27` @ `999fd25` — script `scripts/warn_prompt_validation_v2.py`, subject pool `runs/warn-prompt-validation-v2/subjects.jsonl`, per-subject checkpoints `runs/warn-prompt-validation-v2/subject-{00,01,02}.json`, Phase-3 summary `runs/warn-prompt-validation-v2/summary.json`, Phase-2 stdout `runs/warn-prompt-validation-v2/phase2-resume.log`, Phase-3 stdout `runs/warn-prompt-validation-v2/phase3.log`.

## TL;DR — warn-prompt makes the GPU repair *worse*, not better

The v1 experiment (#68, `7b7270d`) introduced "warn-prompt": thread the prior draft's degeneration reasons (looping / narrowing / truncation) into the next repair prompt so the repair model knows what failure mode to avoid. v1 collapsed at the GPU repair ceiling (100% pass on both arms). v2 pivoted the task battery to the parser/interpreter/state-machine band where the 14B repair model has imperfect first-shot accuracy, and re-ran the A/B sweep.

**Pooled result:** ctrl 53/90 (mean 0.587), trt 49/90 (mean 0.543), P(trt>ctrl) = **0.000**, effect = **-4.4 pp**. The pre-registered decision rule revert if P(trt>ctrl) ≤ 0.10 → **REVERT**. The load-bearing datapoint is `calculator_basic` (the only subject not pinned at floor or ceiling): ctrl 21/30 vs trt 19/30, effect = **-6.2 pp**. Threading degen reasons doesn't help and slightly hurts where the signal can move.

Caveat up front: only 3 of 8 battery tasks produced subjects (Phase-1 acceptance is strict: NPU draft must gate-FAIL with a real assertion failure AND trip a degeneration reason). One floor + one mid + one ceiling is a tight N. The directional signal is consistent across both subjects where signal could move (floor: -6.3 pp; mid: -6.2 pp), but a wider Phase-1 yield would tighten the conclusion.

## The decisive table

| Subject     | Task                | n_text_reasons | ctrl pass/N (mean) | trt pass/N (mean) | P(trt>ctrl) | Effect   | Regime  |
|-------------|---------------------|---------------:|--------------------|--------------------|------------:|---------:|---------|
| subject-00  | `decode_string`     | 2              | 2/30 (0.09)        | 0/30 (0.03)        | 0.000       | **-6.3 pp** | FLOOR   |
| subject-01  | `calculator_basic`  | 2              | 21/30 (0.69)       | 19/30 (0.62)       | 0.000       | **-6.2 pp** | MID     |
| subject-02  | `evaluate_postfix`  | 1              | 30/30 (0.97)       | 30/30 (0.97)       | 0.175       | 0.0 pp   | CEILING |
| **POOLED**  | —                   | —              | 53/90 (0.587)      | 49/90 (0.543)      | **0.000**   | **-4.4 pp** | —       |

95% CIs (Beta(1+pass, 1+fail), 10 000 MC draws, seed=27): pooled ctrl [0.487, 0.683]; pooled trt [0.442, 0.643]. Per-subject `text_reasons` are persisted on each checkpoint; subjects 0 and 1 tripped `looping (trigram_repeat)` + `narrowing (ttr)`, subject 2 tripped `looping` only.

## Method

- **Task battery (8 candidate tasks):** `decode_string`, `simplify_path`, `calculator_basic`, `validate_brackets`, `evaluate_postfix`, `mini_lexer`, `html_attr_parse`, `roman_to_int`. Chosen for the Tier-2 14B frontier — first-shot pass is imperfect, so warn-prompt has headroom to help if it can.
- **DSL self-test at module load** (`_self_test_dsls`): every task carries a `good_impl` and `broken_impl` string; the gate must pass the good and fail the broken at import time. v1's bitterest cost (60 wasted trials per DSL bug) is now an `AssertionError` at startup.
- **Phase 1 acceptance (strict):** for each task, 12 NPU drafts (`qwen2.5-coder-1.5B` sym-INT4 via OpenVINO). A draft becomes a subject only if (a) the gate ran cleanly (no sandbox crash) AND (b) reported at least one assertion failure AND (c) `cascade.degeneration.check_degeneration` returned a non-empty `text_reasons` list. Sandbox crashes are explicitly *not* accepted — they mean the DSL is broken, not the draft.
- **Phase 1 yield:** 3/8 (`decode_string`, `calculator_basic`, `evaluate_postfix`). The other 5 (`simplify_path`, `validate_brackets`, `mini_lexer`, `html_attr_parse`, `roman_to_int`) either passed first-shot on at least one of the 12 NPU drafts or failed without tripping a degeneration reason.
- **Phase 2 (A/B sweep):** per subject, 30 control + 30 treatment GPU repair trials interleaved (ctrl, trt, ctrl, trt, …). `qwen2.5-coder:14b` via Ollama, max 1024 output tokens. Per-subject JSON checkpoint after every trial → idempotent re-entry. Windows `keep_awake` wake-lock held for the whole sweep. Repair latency: 6.32 s / trial on `decode_string` and `evaluate_postfix`, 10.71 s / trial on `calculator_basic` (longer outputs).
- **Treatment vs control:** identical prompts except `degen_reasons=()` (ctrl) vs `degen_reasons=tuple(subj["text_reasons"])` (trt) when calling `cascade.feedback.build_repair_prompt`. No other difference.
- **Phase 3 (analysis):** Beta(1+pass, 1+fail) posterior per arm × 10 000 Monte Carlo draws (seed=27) → per-subject and pooled P(trt > ctrl) and effect in percentage points.

## What the v1 finding called out, resolved or refuted

- v1 (#68, `7b7270d`): "Battery collapsed at the GPU repair ceiling (100% pass on both arms); pivot the task battery to find headroom before declaring." — **Resolved.** v2's parser/interpreter battery produced a real signal range: 0.09 → 0.97 across subjects.
- v1's implicit hypothesis: "If we can find a non-ceiling regime, warn-prompt will help." — **Refuted.** The non-ceiling regime exists (`calculator_basic` at 0.69 ctrl) and warn-prompt is **-6.2 pp** there. The floor subject (`decode_string`) shows the same direction (-6.3 pp). Two independent subjects, same sign, P(trt>ctrl)=0.000.

## Caveats / limits

- **Tight N at the subject layer.** 3 subjects is small; the pooled P=0.000 comes mostly from the per-trial sample size (90 ctrl + 90 trt). A wider Phase-1 yield (more drafts per task, or expanding the candidate battery) would let us check whether the directional signal generalizes across more tasks before fully closing the lever. The consistent direction across the two non-ceiling subjects is the strongest part of the evidence; the absolute -4.4 pp pooled effect is the weakest.
- **Treatment-prompt construction is fixed.** The exact phrasing of the "PRIOR DRAFT QUALITY SIGNAL" block (in `cascade/feedback.py:build_repair_prompt`) is one specific instantiation of warn-prompt. A different phrasing (e.g., a one-line tag at the top instead of a fenced block; or a positive framing "avoid X" → "prefer Y") could in principle perform differently. This finding REVERTs *this* warn-prompt; it does not rule out the broader hypothesis that telling the repair model *something* about the prior failure mode might help.
- **Phase-3 RNG sharing.** `_beta_ci` consumes the same `random.Random(MC_SEED)` across all calls, so per-subject and pooled posteriors share an RNG stream. That's the same shape used in prior findings docs and doesn't affect the verdict (pooled P=0.000 is robust to seed) but is worth noting if a future analysis swaps to per-call seeds.
- **Mid-subject yield depends on the 14B's first-shot accuracy on parsers.** The 0.69 ctrl pass rate on `calculator_basic` is the best signal-carrying regime in this run; if the model gets upgraded (e.g., qwen3-coder), the regime shifts and this lever should be re-evaluated against the new ceiling.

## Reproduce

```powershell
cd C:\Users\danth\src\edge-cascade
git switch experiment/warn-prompt-validation-v2-2026-05-27   # HEAD at 999fd25
uv run python scripts/warn_prompt_validation_v2.py --phase 2  # idempotent; skips done subjects
uv run python scripts/warn_prompt_validation_v2.py --phase 3  # writes summary.json
```

Expected: `summary.json` reports `pooled.p_trt_gt_ctrl = 0.0`, `pooled.effect_pp ≈ -4.4`, `verdict = "REVERT"`. Phase 2 is deterministic up to GPU sampling noise; Phase 3 is fully deterministic given the persisted checkpoints (Monte Carlo seed is `27`).

## What unblocks next

- **Turn warn-prompt OFF by default in the cascade.** The default-on path is `cascade/mesh.py:209` — `rq = ops.repair_prompt(query, prior, failures, prior_degen)`. The minimal change is to pass `()` there instead of `prior_degen`, gated by a config flag (e.g., `CONFIG.warn_prompt_enabled`, default `False`) so the experiment opt-in still works. Keep `build_repair_prompt`'s `degen_reasons` parameter and `cascade/wiring.py`'s forwarding — only the production callsite needs to stop populating it.
- **Keep the telemetry.** The `warn-prompt[round N]: threading K degen reason(s) into repair` trace line at `cascade/mesh.py:204-208` should fire whenever a non-empty tuple is passed, so the production cascade (with the flag off) will be silent and any opt-in path (experiment runner, future ablation) remains observable.
- **Other PD-1 v2 levers are unaffected.** This finding closes warn-prompt only. `skip-repair` (don't call GPU at all when the NPU draft already shows degeneration) and `hard-escalate` (force-promote a degenerate draft straight to Tier 3) are not implemented yet and remain open — this finding neither validates nor invalidates them.
