# FINDINGS — CP-5 P0 detector calibration: metric and N for runtime ambiguity detection

**Date:** 2026-05-26 · **Hardware:** RTX 5070 Ti Laptop, 12 GB · **Substrate:** Ollama (local, $0)

**Evidence (do not merge / do not delete):** `experiment/cp5-detector-calibration-2026-05-26` @ `9296f93` — harness `scripts/context_precision_selfconsistency.py` vendored + persistence-extended (`973a8c0`), calibration analyzer `scripts/cp5_calibrate.py` (`4307b18`), launcher `scripts/run_cp5_p0_detached.ps1`, results `runs/bench/cp5_p0_FULL.json` + `runs/bench/cp5_p0_FULL_calibration.json` (`9296f93`), telemetry `runs/experiment-self-consistency-ambiguity.rec`.

## TL;DR — the runtime detector is buildable; behavioural at N=3 already discriminates

CP-2 (`docs/FINDINGS-self-consistency-ambiguity.md`, evidence `e896fa5`, PR #54) showed that N-sample output divergence `D` predicts ambiguity-sensitivity at offline scale (N=30, ρ=0.84). **CP-5 P0 closes the deployable question**: can the signal survive at runtime sample counts (N=3–8) and which divergence metric should ship?

- **Behavioural-D (CP-2's metric, task-coupled probes) wins decisively** at every N: bootstrap ρ' = **+0.86 at N=3** (P(ρ>0)=0.99), **+0.92 at N=5** (P=1.00), +0.90 at N=8. Youden's J at full-N=10 is a **perfect 1.00** with threshold 0.413.
- **Embedding-D (probe-free, `nomic-embed-text`) is a viable fallback**: ρ' ≈ 0.60–0.69, Youden's J = 0.83 at threshold 0.049.
- **AST-D (probe-free, stdlib `ast`) is the weakest**: ρ' ≈ 0.41–0.53, Youden's J = 0.67.

**Decision the evidence supports — green-light CP-5 P1 with this stack:** *behavioural-D at N=5* (the robust pick — N=3 meets the criterion but its CI is wide; N=5 tightens to [+0.76, +1.00]) **where task-coupled probes are available**, *embedding-D as the probe-free fallback* otherwise. AST is not on the critical path.

This raises the **probe availability** question to first-order for CP-5 P1: production prompts don't carry hand-curated probes, so P1 must commit to (a) probe auto-derivation, (b) embedding-only, or (c) a hybrid (behavioural-where-known, embedding-elsewhere).

## The decisive table

Bootstrap ρ'(D_N, F_full) across 8 cells × 30 trials × 10 samples each, 2000 iters, *without-replacement* subsampling within cells:

| metric           | N=3                       | N=5                       | N=8                       | full-N ρ | Youden J | threshold |
|------------------|---------------------------|---------------------------|---------------------------|---------:|---------:|----------:|
| **behavioural**  | **+0.86 [+0.41, +1.00]**  | **+0.92 [+0.76, +1.00]**  | +0.90 [+0.87, +1.00]      | **+0.875** | **+1.00** | 0.413 |
| embedding        | +0.60 [+0.23, +0.76]      | +0.68 [+0.48, +0.76]      | +0.69 [+0.65, +0.76]      | +0.655 | +0.83 | 0.049 |
| ast              | +0.41 [+0.22, +0.57]      | +0.52 [+0.43, +0.55]      | +0.53 [+0.53, +0.54]      | +0.531 | +0.67 | 1.000 |

P(ρ' > 0) was 0.99 (behavioural@N=3) and 1.00 for every other cell.

Per-cell ground truth at full-N=10 (8 cells = 4 tasks × {L0, L3}):

| task              | L0 F | L0 D_behav | L3 F   | L3 D_behav | ΔD_behav |
|-------------------|-----:|-----------:|-------:|-----------:|---------:|
| topological_sort  | 0.03 | 0.00       | **0.88** | **0.47**  | **+0.47** |
| base_convert      | 0.03 | 0.00       | **0.70** | **0.41**  | **+0.41** |
| merge_intervals   | 0.03 | 0.00       | 0.03   | 0.04       | +0.04    |
| lis_length        | 0.03 | 0.00       | 0.03   | 0.00       |  0.00    |

The sensitive/immune split is even sharper than CP-2 (which used the wider 6-task `AMBIG` set at N=30) — the 4 chosen tasks isolate the cleanest signal.

## What the data says about each fork the plan called out

The CP-5 plan listed four open forks. Three are now answered:

1. **Runtime N — RESOLVED: N=5.** N=3 meets the ρ' ≥ 0.6 gate (ρ' = 0.86) but its CI is wide [+0.41, +1.00]; N=5 tightens to [+0.76, +1.00] for the same metric. The 2 extra single-shot generations (5 total counting the failed first generation) cost ~1× a repair round.
2. **Probe-battery vs probe-free — RESOLVED with caveat.** Behavioural (probes) is *much* better (ρ' ≈ 0.86–0.92 vs 0.60–0.69 for embedding). But behavioural needs probes the production cascade doesn't have. **The new first-order P1 question is probe availability** (see below).
3. **ASK UX — unchanged from the plan.** Surface-and-default (modal sample + flag in repair prompt's `note=` channel) remains the recommendation; this finding doesn't bear on UX.
4. **Integration point — unchanged from the plan.** Post-first-failure inside `mesh.solve` (`cascade/mesh.py:153`) remains correct: the detector runs only when the first generation fails (~30% of prompts pay any detector cost), and N-1 = 4 extra generations are the runtime overhead.

## The probe-availability fork (NEW, must be resolved at P1)

Behavioural-D uses a fixed `PROBES[task]` battery — hand-curated input tuples per task. Production prompts have no such battery. Three viable paths for P1:

- **(a) Auto-derive probes from the prompt.** Parse the function signature from the prompt; generate seeded-random inputs matching arg shapes. Brittle for novel APIs / unusual prompts; works for code-completion-shaped requests.
- **(b) Embedding-D only.** Skip probes entirely. Use embedding-D at N=5 (ρ' ≈ 0.68, Youden's J = 0.83). Probe-free, semantics-aware, only adds an Ollama `/api/embeddings` round-trip per sample (~150–1800ms per code on this hardware; cached). Step down from behavioural but still viable.
- **(c) Hybrid.** Behavioural where the prompt matches a known task pattern (regex / heuristic identifies a `def foo(...)` signature pattern with simple arg types); embedding otherwise. Best of both at integration complexity cost.

This is the *single* P1 design decision the data hasn't resolved. P1 should pick one and measure.

## Method

`qwen2.5-coder:14b` at default temperature, 4 tasks (`topological_sort`, `base_convert`, `merge_intervals`, `lis_length` — covers the high-D / weak-default / two immune anchors), levels `{0, 3}`, **N=10** single-shot samples per cell. Harness `scripts/context_precision_selfconsistency.py` (CP-2's, vendored from `e896fa5`) extended to persist per-sample `code_text` and full `sig` in `samples_raw[]` so the offline analyzer can compute three metrics and bootstrap-subsample without re-running generations.

Per sample, three divergence fingerprints:
- **behavioural-D** = normalized Shannon entropy over CP-2's behavioural-signature strings (the gate-free `tuple(repr(fn(*probe)) ...)` fingerprints from a fixed per-task `PROBES` battery).
- **AST-D** = normalized Shannon entropy over `tuple(sorted(Counter(type(n).__name__ for n in ast.walk(parsed)).items()))` — structural node-type-count fingerprints. Stdlib `ast` only, deterministic.
- **embedding-D** = mean pairwise cosine distance over `nomic-embed-text` embeddings of the raw code text, cached per unique code.

ρ' bootstrap: for each (metric, N_sub) pair, 2000 iters; per iter, for each cell, draw N_sub samples *without replacement* from the cell's 10 samples, compute the metric, then take Spearman across cells against the cell's full-N=10 gate-fail rate. CI from sorted draws.

Youden's J: at full-N, label each cell *sensitive* iff F ≥ 0.5, sweep candidate thresholds (all distinct D values), report the maximum J = sens + spec − 1 and its threshold.

## Caveats / limits

- **Same model, same six-task ancestry as CP-2.** N=10 (vs CP-2's N=30) and the 4-task subset sharpen the signal; the *direction* of the conclusion is consistent with CP-2, but **CP-4 Phase-B** (intrinsic / realistic-prompt ambiguity, neutral LLM judge) remains the generalisation check.
- **`pos=2, neg=6` for Youden's J** — only 2 sensitive cells out of 8 means the perfect J=1.00 for behavioural is technically a 2-of-2 vs 0-of-6 split; not statistically remarkable. The bootstrap ρ' across all 8 cells is the more honest summary.
- **Embedding cost is hardware-dependent.** `nomic-embed-text` was ~1.8 s per cold call in a quick check, then fast under the run's caching. Production batching would help.
- **AST-D underperforms here** (ρ' ≈ 0.5) probably because the node-type-count fingerprint is too coarse — most function bodies have similar node-type histograms even when their behaviour differs. A finer AST fingerprint (sub-tree shapes) could plausibly do better; not a priority given embedding is already a viable probe-free fallback.

## Reproduce

```pwsh
# from edge-cascade/, Ollama up, qwen2.5-coder:14b + nomic-embed-text pulled
git checkout experiment/cp5-detector-calibration-2026-05-26     # LOCAL evidence
pwsh -File scripts/run_cp5_p0_detached.ps1                       # ~15 min, detached
# when cp5_p0_FULL.json appears, partial=false:
uv run python scripts/cp5_calibrate.py --boot 2000
# outputs runs/bench/cp5_p0_FULL_calibration.json + console summary
```

## What this unlocks

- **CP-5 P1 — green-lit and design-ready.** Pick the probe-availability path (a/b/c), build the standalone two-arm pass-rate comparison (baseline cascade vs cascade + CP-5 surface-and-default at the chosen metric/N), gate to P2 on uplift ≥ X%.
- **CP-5 P2** — wire into `cascade/mesh.solve` at the post-first-failure injection point (`cascade/mesh.py:153`), reusing `build_repair_prompt(..., note=...)` (`cascade/feedback.py:47`). Unchanged from the original plan; just plumbing the chosen detector.
- **CP-6 (cloud-cost)** — now triply motivated. A working detector at N=5 + the cleaner from CP-5 = the local-recovery lever; CP-6 measures the cloud-token reduction downstream.

## Out of scope here

- **Auto-probe-derivation** (path (a)) — its own experimental ablation; punt to P1.
- **Finer AST fingerprints** (sub-tree shapes vs node-type counts) — embedding already covers the probe-free case adequately; not on the critical path.
- **Hybrid integration (path (c))** — sensible if P1 measures the gap meaningfully; P1 picks.
