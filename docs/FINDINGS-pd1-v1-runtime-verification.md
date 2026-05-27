# FINDINGS — PD-1 v1 runtime verification: prose-calibrated thresholds over-trip on code

**Date:** 2026-05-27 · **Hardware:** Intel AI Boost NPU + RTX 5070 Ti Laptop · **Substrate:** local cascade (`cli.py`), $0

**Evidence (live):** main @ `4e5b055`. Detector + thresholds at `cascade/degeneration.py` and `cascade/degeneration_thresholds.json`. Trace lines persisted to `runs/cascade.log` (4 cli.py invocations, 5 `degen[…]:` emissions) under PD-1 v1 (#62). Re-run with the reproduce command below; the trace format is the contract.

## TL;DR — PD-1 v2 must not act on these thresholds; re-calibrate `trigram_repeat` on code first

PD-1 v1 (#62) shipped as a telemetry-only passive observer in `cascade.mesh.solve`, with a `degeneration_thresholds.json` that warned the prose calibration "may over-trip on code outputs." This runtime check confirms the warning empirically — and identifies the single lever that does it.

- **3 of 3 gate-passing NPU code drafts over-tripped.** Score 0.17 each, all on the same reason: `looping: trigram_repeat > 0.0367`.
- **The GPU repair output for the one fenced-block failure also over-tripped** (score 0.17, same lever) despite producing the answer the gate accepted.
- **No other criterion fired.** `ttr_min=0.411`, `distinct_sent_ratio_min=0.9055`, `max_sent_repeat_max=1.0` were all clean on every sample. The over-trip is **single-lever**, not diffuse.

**Decision the evidence supports:** the prose threshold `trigram_repeat_max = 0.0367` is structurally too low for code. Legit Python code has higher trigram repetition than prose because identifiers reappear (`state[current_node]`, `in_degree[neighbor]`). Observed values on correct code were 0.06–0.10. PD-1 v2 levers that gate behavior on the v1 score (skip-repair, warn-prompt, hard-escalate) would have **falsely tripped 3 of 3 correct NPU drafts** — direct quality regression. Re-calibrate `trigram_repeat_max` on a code corpus before wiring any v2 control flow. The other three criteria likely transfer (none fired).

## The decisive table

5 emissions across 4 code tasks routed through `cascade.mesh.solve` (default topology `balanced`):

| Task              | Tier | Chars | Score | Tripped reason                            | Gate verdict |
|-------------------|:----:|------:|------:|-------------------------------------------|:-----------:|
| dijkstra          | npu  |   919 | 0.00  | —                                          | FAIL (no fence) |
| dijkstra          | gpu  |  1100 | 0.17  | `looping: trigram_repeat=0.06 > 0.04`     | **PASS** |
| merge_intervals   | npu  |   871 | 0.17  | `looping: trigram_repeat=0.07 > 0.04`     | **PASS** |
| topological_sort  | npu  |   865 | 0.17  | `looping: trigram_repeat=0.10 > 0.04`     | **PASS** |

(The trace renders the threshold as `> 0.04`; the stored value in `degeneration_thresholds.json` is `0.0367`.)

The dijkstra NPU draft scored 0.00 because the NPU truncated under `npu_max_tokens=192` and never reached the dense identifier-repetition region — so the gate caught it on a separate, structural failure (no fenced block), not on degeneration. That same task survived the cascade via a GPU repair round that produced a correct, gate-passing answer — and PD-1 v1 flagged it as "looping" anyway.

## Method

- Driver: `python cli.py "<task>"` against main @ `4e5b055`, local-only (no `--cloud`), default topology.
- Tier-1 (Intel NPU AI Boost): qwen2.5-coder-1.5B sym-INT4 via OpenVINO, available verified (`edge-npu.status` → `{available: true, device: "NPU"}`). Phase 0 NPU model-files fix (#61) confirmed end-to-end.
- Tier-2 (RTX 5070 Ti): qwen2.5-coder:14b via Ollama, available verified.
- PD-1 v1 passive observer in `cascade/mesh.py::solve.observe` fires once per NPU draft and once per GPU repair round; thresholds loaded once at module import from `cascade/degeneration_thresholds.json`.

Tasks chosen to span the cascade's expected paths: dijkstra (caps Tier-1 → exercises Tier-2 repair), merge_intervals (cheap, Tier-1-solvable per CP-2), topological_sort (Tier-1-solvable per CP-5 P0 ground truth).

## What this is not

This is a **runtime verification with N=4**, not a calibration experiment. It establishes that the over-trip warning in `degeneration_thresholds.json` is real and identifies the offending lever, but does not quantify a replacement threshold. The PD-1 v2 calibration sweep belongs in its own evidence branch following [[experiment-protocol-evidence-branches]] — likely modeled on CP-5 P0 (bootstrap ρ' across N + Youden's J), but on a code corpus with ground-truth labels for "actually degenerate" vs "correct-but-lexically-dense."

## Reproduce

```powershell
cd C:\Users\danth\src\edge-cascade
git switch main  # verify HEAD at 4e5b055
uv run python cli.py "write a python function dijkstra(graph, start) returning a dict of shortest-path costs"
uv run python cli.py "write a python function merge_intervals(intervals) that merges overlapping intervals"
uv run python cli.py "write a python function topological_sort(graph) using Kahn's algorithm"
# Inspect:
grep "degen\[" runs/cascade.log | tail
```

Expected: 4 emissions from the 3 commands above (one extra from dijkstra's GPU repair). All three NPU passes will trip `looping: trigram_repeat`; the GPU repair will trip the same; no other criterion will fire. Score values for `trigram_repeat` will vary slightly with model temperature but stay materially above 0.0367.

## What unblocks next

PD-1 v2 (open-threads item **5a**: act on the degen signal) has three sub-levers — (a) skip-repair on degen NPU draft, (b) warn-repair-prompt, (c) hard-escalate. **All three are blocked on code-corpus threshold re-calibration.** The SD-2b dashboard panel (open-threads item **5b**) is *not* blocked — it can paint the v1 telemetry as-is, with the over-trip noise visible and instructive.
