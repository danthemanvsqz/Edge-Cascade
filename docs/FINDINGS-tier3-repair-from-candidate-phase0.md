# FINDINGS — Tier-3 repair-from-candidate Phase 0: PREMISE WEAK, defer Phase 1 plumbing

**Date:** 2026-05-28 · **Hardware:** Intel AI Boost NPU + RTX 5070 Ti Laptop · **Substrate:** local cascade (in-process workers) + Ollama; $0

**Evidence (do not merge / do not delete):** `experiment/tier3-repair-from-candidate-2026-05-28` @ `ca81a5e` — harness `scripts/tier3_repair_vs_fresh.py`, Phase-1 log `runs/tier3-repair-from-candidate/phase1.log`, captured pool `runs/tier3-repair-from-candidate/subjects.jsonl` (1 subject), attempted ledger `runs/tier3-repair-from-candidate/attempted.json` (10 tasks).

## TL;DR — Phase 0 surfaced a premise problem before Phase 2 spent any wall time

The dedicated memory [[edge-cascade-tier3-repair-from-candidate]] framed Tier-3 repair-from-candidate (Mode A orchestrator passing the local cascade's capped output as `prior_attempt` to `cloud_worker.generate`) as a likely token-conservation win. Phase 0 was designed to measure that with `deepseek-r1:14b` as the Tier-3 stand-in, gating correctness via per-subject DSLs.

Phase 0 didn't reach Phase 2 (the A/B sweep) because **Phase 1 yield was 1/10 — the production cascade rarely caps**. That itself is the load-bearing finding: the repair-vs-fresh question only matters when caps happen, and on this task pool they almost never do.

**Decision:** **DEFER Phase 1 plumbing**. Keep the Tier-3 repair-from-candidate idea in the dedicated memory as PARKED rather than ACTIONABLE. Re-measure if a future cascade change (heavier task mix, model downgrade, topology change) re-introduces caps as a non-negligible event.

## The numbers

| Source                          | mesh.solve attempts | caps | cap rate |
|---------------------------------|--------------------:|-----:|---------:|
| Synthetic battery (Phase 1)     | 10 (8 v2 + 2 cap-prone) | 1 | **10%**  |
| Production `runs/cascade.rec`   | 6                   | 0    | **0%**   |
| **Combined**                    | 16                  | 1    | **6.3%** |

The single captured cap is `html_attr_parse` (regex-y HTML attribute parser; the cascade failed on quoted-value edge cases). The 9 non-caps spanned `decode_string`, `simplify_path`, `calculator_basic`, `validate_brackets`, `evaluate_postfix`, `mini_lexer`, `roman_to_int`, and the two cap-prone additions designed specifically to be hard: `avl_balance` (AVL tree with LL/RR/LR/RL rotations) and `vm_run` (8-op stack VM with DUP/SWAP). All 9 resolved within cap-2.

The production sample is tiny (n=6) but directionally consistent. The two-source agreement makes the qualitative claim ("caps are rare under default-on skip-repair") stronger than either alone.

## Method

- **Battery:** the 8 v2 parser/interpreter tasks from `scripts/warn_prompt_validation_v2.py` (verified DSLs at module load) plus 2 cap-prone additions written specifically for this experiment: `avl_balance` (returns `(in-order, height)` with strict height bound on adversarial inserts) and `vm_run` (8-op stack VM with operand-order discrimination). Each task carries a `good_impl` and `broken_impl` self-test that runs at module load — both new tasks' DSLs verified (good passes 0 failures, broken fails 2-3).
- **Cascade configuration:** `cascade.mesh.solve(prompt, "balanced", ops)` with the *current production CONFIG* — real NPU draft (qwen2.5-coder-1.5B sym-INT4 via OpenVINO), real GPU repair loop (qwen2.5-coder:14b via Ollama, cap=2), `skip_repair_on_degen=True` (production default as of #76 / `36c7c20`). The gate is the subject's per-task functional DSL, not the syntax-only verifier — closer to "did this answer actually work" than to "did it compile".
- **Phase 1 acceptance:** a task becomes a subject only if `mesh.solve` returns `out.capped=True` with non-empty captured GPU text and at least one captured gate failure (avoids cap-with-empty-output edge cases). Up to 4 attempts per task; first cap wins.
- **Sticky resume:** after every task (cap OR no-cap) the harness writes the slug to `attempted.json` so re-runs skip already-probed tasks. The 8 v2 slugs were backfilled out-of-band when the cap-prone additions landed mid-experiment.

## Why this matters more than "we ran out of subjects"

The dedicated memory's hypothesis was specifically a token-savings hypothesis: "Output/thinking tokens dominate cost; repair gives the cloud a near-correct scaffold + exact failures → targeted fix, less rederivation. Likely a net win but empirical." That hypothesis assumes caps happen often enough to make the per-cap savings move the cloud-bill needle.

The post-skip-repair cascade (default-on as of 2026-05-28) absorbs most failure cases inside Tier 2 by routing degenerate NPU drafts to a fresh GPU `generate` instead of the bounded repair loop. The skip-repair FINDINGS doc reported +22.8 pp pooled correctness — that gain shows up here as caps disappearing into resolved-by-Tier-2 outcomes. The same change that made skip-repair worth shipping made Tier-3 repair-from-candidate less urgent.

This is a useful interaction effect: the orderly sequencing of action levers matters. We measured warn-prompt → REVERT, skip-repair → SHIP, and that downstream Tier-3 repair → PREMISE WEAKENED. A different sequencing (e.g. skipping skip-repair) might have given a stronger Phase 0 yield here, but the production-relevant question is always "does the lever help the *current* cascade", and the current cascade has skip-repair on.

## What this finding does and doesn't say

- **Says:** under the current production cascade (skip-repair default-on, balanced topology, qwen2.5-coder:14b at Tier 2), caps are rare enough on parser/interpreter/state-machine tasks that wiring Tier-3 repair-from-candidate is not a load-bearing change to make right now.
- **Says:** for the rare cap case that does occur (one captured this sweep: `html_attr_parse`), the question "does repair help" was NOT measured. A future revisit with N=1 on the captured subject would yield a single-task posterior but no cross-subject generalisation.
- **DOES NOT say:** Tier-3 repair-from-candidate is a bad idea. The cloud_worker already supports `prior_attempt` (see `cascade/cloud_worker.py:_compose_user`); the plumbing change is small (~150 LOC per [[edge-cascade-tier3-repair-from-candidate]]). It's deferred on *low value at current cap frequency*, not on technical risk.
- **DOES NOT say:** production cap rate is 0%. The 6-record cascade.rec sample is way too small for that claim. The qualitative "caps are uncommon" is what's supportable from this evidence; precise frequency requires either a fuller production trace or a deliberately-targeted hard-task pool.

## Caveats / limits

- **Battery scope.** 10 tasks, all in the parser/interpreter/state-machine band. Tasks outside this band (e.g. numerical algorithms, ML, distributed systems design) could exhibit different cap rates. The 14B coder model has known weaknesses outside its training-data sweet spot; a harder task family could shift the FINDINGS.
- **Production .rec sample is tiny.** 6 records is not a basis for a precise cap-rate estimate; it's directional evidence only. If a longer-running production trace (e.g. a session-long edge-cli usage record) showed cap rate ≥ 20%, this defer would be wrong.
- **The cap that did happen was at attempt 0.** `html_attr_parse` capped on the first cascade run, with `n_failures=3`. That's evidence the failure mode is real and reproducible on this task; the rare cap case isn't fictional — it's just rare.
- **Skip-repair interaction.** This Phase 0 ran AFTER skip-repair shipped default-on. A pre-skip-repair run on the same battery would yield a different (higher) cap rate. The Tier-3 repair-from-candidate question is well-defined only relative to a fixed upstream cascade; if a future change re-introduces caps, re-measure.
- **No Phase 2 token data.** This finding makes no claim about the *direction* of the token delta (repair vs fresh) — that requires Phase 2, which was skipped on the premise check. If a future re-measurement finds caps are common again, Phase 2 still needs to run before committing to plumbing.

## Reproduce

```powershell
cd C:\Users\danth\src\edge-cascade
git switch experiment/tier3-repair-from-candidate-2026-05-28   # HEAD at ca81a5e
uv run python scripts/tier3_repair_vs_fresh.py --phase 1
```

Expected: with `attempted.json` already containing all 10 slugs (committed on the evidence branch), Phase 1 reports `nothing new to capture` and exits with 1 subject (`html_attr_parse`) in the pool.

To re-probe from scratch:

```powershell
Remove-Item runs/tier3-repair-from-candidate/attempted.json
Remove-Item runs/tier3-repair-from-candidate/subjects.jsonl
uv run python scripts/tier3_repair_vs_fresh.py --phase 1
```

The cascade is non-deterministic (real model sampling), so cap rate may shift trial-to-trial; the qualitative "rare on this battery" should be robust.

## What unblocks next

- **Update the dedicated memory** ([[edge-cascade-tier3-repair-from-candidate]]) to reflect Phase 0 outcome: keep the IDEA documented; mark Phase 0 status = PREMISE-CHECK-DEFERRED; cite this doc.
- **Update [[edge-cascade-open-threads]]** to move #6a Tier-3 repair-from-candidate from ACTIONABLE I3·S3 to PARKED-with-de-risk-spike (S4) until cap rate evidence re-supports it. The natural re-trigger: a future cascade change that lowers Tier-2 success rate (e.g. larger task mix, model downgrade, new topology).
- **#6b PD-1 v2 `hard-escalate`** (promote degenerate draft straight to Tier 3) is now the unblocked I3·S3 item. It needs an ADR first — and the cap-rate finding here is directly relevant context for that ADR (if caps are rare, "promote draft straight to Tier 3" is less impactful than the design originally assumed).

## Related work

- `docs/FINDINGS-pd1-v2-skip-repair.md` (2026-05-28, SHIP) — the upstream change that made caps rare; the same finding that motivates this defer.
- `docs/FINDINGS-pd1-v2-warn-prompt.md` (2026-05-27, REVERT) — earlier lever on the same cascade path; collected the v2 task battery this experiment reused.
- `docs/FINDINGS-coder-r1-complementarity.md` (PR #47) — the AI-4 study that picked `deepseek-r1:14b` as the Tier-3 stand-in.
- [[edge-cascade-tier3-repair-from-candidate]] — dedicated memory for this work; the IDEA stays documented, status now Phase-0-checked-and-deferred.
