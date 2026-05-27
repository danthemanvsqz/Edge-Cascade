# FINDINGS — PD-1 v2 warn-prompt empirical validation: uninformative at N=2

**Date:** 2026-05-27 · **Hardware:** Intel AI Boost NPU + RTX 5070 Ti Laptop · **Substrate:** local cascade (in-process workers), $0

**Evidence (do not merge / do not delete):** `experiment/warn-prompt-validation-2026-05-27` @ `5b51422` — script `scripts/warn_prompt_validation.py`, subjects `runs/warn-prompt-validation/subjects.jsonl`, per-subject trial checkpoints `runs/warn-prompt-validation/subject-00.json` / `subject-01.json`, posterior summary `runs/warn-prompt-validation/summary.json`. Trial-level recording lane `runs/experiment-warn-prompt-validation.rec` (also on the evidence branch).

## TL;DR — the experiment ran cleanly but yielded no informative subjects

PR #67 (`7b7270d`) shipped warn-prompt as the first PD-1 v2 action lever, threading text-degeneration reasons into the GPU repair prompt. This experiment was designed to A/B that lever vs the no-degen control on gate-FAIL'd degraded NPU drafts. **The experiment cannot answer whether warn-prompt improves repair quality.** Two of four candidate subjects turned out to be false-degen-positives (v2 detector tripped on correct code) and the two surviving subjects sat at the GPU repair ceiling (100% pass rate on both arms), leaving zero subjects in the failure-rich band (20-80% pass) where an effect could be measured. Verdict: **MARGINAL_INVESTIGATE** — warn-prompt effect remains unvalidated; ship-next-lever decision must wait on a v2 of this experiment with a redesigned subject battery.

The script, gates, recorders, and Bayesian-MC analysis worked correctly end-to-end. The failure mode was in **subject selection**, not in the harness.

## The decisive table

| metric | value | interpretation |
|---|---:|---|
| Subjects passed Phase 1 filter | **4** | gate-FAIL + text_reasons non-empty |
| ... false-degen-positives (dropped) | **2** | rotate_image, lru_cache — NPU drafts actually correct |
| ... survived to Phase 2 | **2** | count_islands, binary_search_rotated |
| ... in the failure-rich band (20-80%) | **0** | both at GPU repair ceiling (100%) |
| Pooled `ctrl_pass / (pass+fail)` | 60/60 | 100% |
| Pooled `trt_pass / (pass+fail)` | 60/60 | 100% |
| Pooled `P(trt > ctrl)` (10k MC draws, seed=27) | 0.79 | sampling noise on tied posteriors |
| Pooled `effect_pp` | **+0.0pp** | identical pass counts; no signal |
| Verdict | **MARGINAL_INVESTIGATE** | per the pre-registered decision rule |

The pooled `P(trt > ctrl) = 0.79` is a Monte Carlo artifact: with both arms at 60/60, the posteriors are `Beta(61, 1)` for both, and the MC sample comparison just happens to fall on this side of 0.5. The effect-size column is the honest summary: **+0.0 percentage points.**

## Why the experiment couldn't measure the effect

Phase 1 ran 52 NPU drafts across the 10-task battery. The yield per task:

| task | attempts | gate-pass | gate-fail | degraded | subject yield |
|---|---:|---:|---:|---:|---:|
| dijkstra | 8 | 0 | 8 | 0 | 0 — fail-not-degen |
| bfs_grid | 8 | 0 | 8 | 0 | 0 — fail-not-degen |
| count_islands | 1 | 0 | 1 | 1 | **1** ✓ |
| merge_intervals | 8 | 8 | 0 | 8 | 0 — **pass-and-degen** (anomaly) |
| topological_sort | 8 | 0 | 8 | 0 | 0 — fail-not-degen |
| longest_substring | 8 | 8 | 0 | 0 | 0 — pass-not-degen |
| word_break | 8 | 8 | 0 | 0 | 0 — pass-not-degen |
| binary_search_rotated | 1 | 0 | 1 | 1 | **1** ✓ |
| lru_cache | 1 | 0 | 1 | 1 | (1 → 0 after re-gate) |
| rotate_image | 1 | 0 | 1 | 1 | (1 → 0 after re-gate) |

Two failure modes collapsed the subject pool:

1. **False-degen-positives.** Three tasks (`merge_intervals`, `lru_cache`, `rotate_image`) produced NPU drafts where v2 detected degeneration on syntactically AND semantically correct code. `merge_intervals` was caught at Phase 1 (gate-pass + degraded → not a candidate). `lru_cache` and `rotate_image` initially survived because the experiment's hand-written DSL had bugs (a non-`assert` line that crashed `validate_log.parse_dsl`, and a logically-wrong LRU eviction check); re-gating with corrected DSLs revealed the NPU drafts actually passed. **Implication:** the v2 calibration finding (`#66`) reported 0% FP on the v2 corpus, but at least 3 verbose-but-correct code patterns OUTSIDE that corpus still trip the detector. v3 calibration with a wider corpus is the natural follow-up — already tracked in #66.

2. **Failure-rich band miss.** Of the 7 tasks where NPU genuinely failed, 4 produced non-degenerate failures (just-wrong code with normal trigram/TTR profiles) and 3 produced degenerate failures that the GPU repair model solves 100% of the time. The intersection — "NPU degenerate-fails AND GPU repair sometimes-fails" — was empty in our 60 trials per surviving subject. The skill warned about this: only the 20-80% failure-rich band can measure repair-lever effects.

## Secondary findings (non-primary but worth recording)

These observations came out of the run even though the primary hypothesis remained untested.

### 1. v2 thresholds still over-trip on at least 3 code patterns

The detector fires `text_reasons` on `merge_intervals`, `lru_cache`, and `rotate_image` outputs that are gate-passing correct code. The dominant tripped metrics were `trigram_repeat` and `ttr` — the same two that drove v2 calibration. Hypothesis: NPU's coding style on these tasks is verbose with high identifier repetition (e.g. `matrix[i][j]` patterns in rotate_image, `self.queue` in lru_cache, list comprehensions in merge_intervals), which suppresses TTR and inflates trigram repetition without the code actually looping. A v3 calibration with these patterns explicitly in the negative corpus would tighten the bound.

### 2. Tier-1's failure-rich band on standard algorithm tasks is narrower than expected

The skill's calibration band guidance assumes ~20-80% pass rate at the tier-of-interest. On `qwen2.5-coder-1.5B sym-INT4`, our battery clustered at the extremes: 3 tasks at near-100% pass, 3 tasks at near-0% pass (with no degenerate signature), and only 4 tasks producing the desired fail+degen combination. **A v2 experiment should pivot to parser/interpreter-class tasks** — what the v2 calibration doc identified as Tier-2's frontier (where GPU repair would sometimes fail) and which by extension are also Tier-1's loop-zone.

### 3. Hand-written DSL per task is brittle

Two of our four candidate subjects' DSLs had bugs that were only caught after running Phase 2. Future experiments **must** test each DSL against a known-good and known-broken implementation BEFORE running, ideally as a `pytest` cell embedded in the experiment script itself. (The `gate_functional()` helper already exists; wrap it in a 5-line assertion at module load.)

## Method

- **Subject definition:** an NPU draft text where (a) `verify_functional(text, dsl=task_dsl).passed == False` AND (b) `check_degeneration(text).text_reasons` is non-empty.
- **NPU model:** qwen2.5-coder-1.5B sym-INT4 via OpenVINO on Intel AI Boost (`NPU` device).
- **GPU model:** qwen2.5-coder:14b via Ollama on RTX 5070 Ti (12 GB VRAM).
- **Repair budget:** 768 output tokens per repair (cap; most outputs ~300-500).
- **Trials per arm:** 30 (60 per subject, interleaved control/treatment to absorb GPU drift).
- **Gate:** `mcp_servers/_funcverify_child` subprocess (custom DSL override; isolated exec).
- **Analysis:** `Beta(1+pass, 1+fail)` posteriors per arm via `random.betavariate`, 10k MC draws seeded at 27. `P(trt > ctrl)` from paired samples.
- **Decision rule (pre-registered):**
  - `P >= 0.90` AND effect `>= +10pp` → SHIP_NEXT_LEVER
  - `P <= 0.10` → REVERT
  - otherwise → MARGINAL_INVESTIGATE

## Caveats

- **N=2 is too few for a power claim.** Even if the surviving subjects hadn't been at ceiling, two paired subjects can't ground a pooled estimate. The plan targeted 8-12; the yield was 2. Future runs need either a wider task battery or a looser filter (e.g. include just-wrong-failures and report warn-prompt as ineffective on those, separately).
- **GPU ceiling is itself informative.** "On tasks where GPU repair already succeeds 100% of the time, warn-prompt has no headroom to help" is a real (though obvious) finding. The interesting question — "does warn-prompt help where GPU sometimes fails" — remains unanswered.
- **DSL spec bugs caused two false drops.** Subjects that LOOKED like fail+degen at Phase 1 turned out to be DSL-grammar crashes (the sandbox subprocess died at parse time). The Phase 1 filter accepted those as subjects; only the Phase 2 trial logs (uniform 0/30 with "sandbox must run" failures) surfaced the bug. A future iteration should distinguish "code crashes" from "code parses-but-fails-assertions" at Phase 1.
- **Single GPU model.** All results are for `qwen2.5-coder:14b`. The warn-prompt signal could matter more (or less) for a different repair model — e.g. Tier-4 cloud Opus, which is more verbose and could engage with the signal more directly.

## Reproduce

```powershell
cd C:\Users\danth\src\edge-cascade
git switch experiment/warn-prompt-validation-2026-05-27  # HEAD at 5b51422
uv run python scripts/warn_prompt_validation.py             # all 3 phases, ~1.5h
# Or phase-by-phase (idempotent on persisted artifacts):
uv run python scripts/warn_prompt_validation.py --phase 1
uv run python scripts/warn_prompt_validation.py --phase 2
uv run python scripts/warn_prompt_validation.py --phase 3
```

Phase 1 is NPU-temperature-stochastic (subject set may differ between runs). Phase 2 is GPU-temperature-stochastic. Phase 3 is fully deterministic given the persisted JSON (`mc_seed = 27`).

## What unblocks next

- **v2 of THIS experiment** (recommended first):
  - Wider battery aimed at the GPU failure-rich band — parser/interpreter/state-machine tasks where the 14B model has imperfect first-shot pass.
  - DSL self-tests at module load (`assert gate_functional(KNOWN_GOOD) is passed; assert gate_functional(KNOWN_BROKEN) is failed`).
  - At Phase 1, distinguish "DSL-parse-crashed" from "gate-FAIL with failures" so the subject filter doesn't accept syntactically-broken DSLs.
  - Target ≥6 subjects in the 20-80% band (re-sample more drafts per task if needed).
- **v3 PD-1 calibration** (already tracked under #66 follow-ups):
  - Wider negative corpus including verbose-but-correct patterns (the merge_intervals / lru_cache / rotate_image shape).
  - Bootstrap CIs to formalize the over-trip rate.
- **Do NOT ship the next v2 action lever** (skip-repair, hard-escalate) yet. The warn-prompt evidence base hasn't validated; stacking another lever on an unvalidated foundation risks compounding bias.
