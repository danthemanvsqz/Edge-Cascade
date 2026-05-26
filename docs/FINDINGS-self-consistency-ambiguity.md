# FINDINGS — self-consistency as a gate-free ambiguity detector (CP-2)

**Date:** 2026-05-26 · **Hardware:** RTX 5070 Ti Laptop, 12 GB · **Substrate:** Ollama (local, $0)

**Evidence (do not merge / do not delete):** `experiment/self-consistency-ambiguity-2026-05-25` @ `e896fa5` — harness `scripts/context_precision_selfconsistency.py` (commit `dddebb0`; CP-1 harness `context_precision_{calibrate,h1,ambiguity}.py` vendored from `b2cfbda`), detached launcher `scripts/run_cp2_detached.ps1` (`bcd57b8`), results `runs/bench/context_precision_selfconsistency_FULL.json` (`e896fa5`), telemetry `runs/experiment-self-consistency-ambiguity.rec`.

## TL;DR — the runtime detector exists, with no blind spot in this set

CP-1 (`FINDINGS-context-precision.md`) showed prompt ambiguity is **~94% recoverable** when the oracle (the precise prompt) is known. The deployable question CP-2 resolves is: can the prompt's ambiguity-sensitivity be detected **at runtime, without the oracle and without the gate**?

**Yes.** Across 6 tasks × 2 levels × 30 single-shot samples (`qwen2.5-coder:14b`), the N-sample output-divergence signal `D` tracks the gate-fail rate `F` with **Spearman ρ(D, F) = 0.84 [0.79, 0.95], P(ρ>0) = 1.00**. The no-default tasks (the CP-1 "no-default law" ones) light up on both axes; the immune tasks stay quiet on both. The predicted confident-wrong blind spot **did not materialize** — `confident_wrong_cells: []`.

**Decision the evidence supports: green-light scoping CP-5 (the disambiguation lever)** — the runtime signal is real, specific, and more robust than the plan anticipated (no documented caveat needed at this set/model).

## The decisive table

| task               | F @ L0 | D @ L0 | F @ L3   | D @ L3   |   ΔD     |
|--------------------|-------:|-------:|---------:|---------:|---------:|
| topological_sort   |  0.03  |  0.12  | **0.84** | **0.42** | **+0.31** |
| base_convert       |  0.03  |  0.00  | **0.78** | **0.30** | **+0.30** |
| roman_to_int       |  0.06  |  0.04  |  0.06    |  0.04    |  0.00    |
| merge_intervals    |  0.03  |  0.00  |  0.06    |  0.04    | +0.04    |
| lis_length         |  0.03  |  0.00  |  0.03    |  0.00    |  0.00    |
| wildcard_match     |  0.06  |  0.04  |  0.06    |  0.04    |  0.00    |
| **Spearman ρ(D,F)** |        |        |          |          | **0.84 [0.79, 0.95]**  |
| **P(ρ > 0)**       |        |        |          |          | **1.00** |
| **confident-wrong cells (high-F, low-D)** |  |  |   |          | **0**    |

`F` = `Beta(1+fail, 1+pass)` posterior mean (n=30/cell); `D` = normalized Shannon entropy of behavioural-signature distribution; ΔD = D(L3) − D(L0).

## What the signal actually shows

- **The no-default tasks light up on both axes together.** `topological_sort` (direction convention is arbitrary — *no* shared default) and `base_convert` (case has a *weak* default) both rise on `F` *and* `D`, with ΔD ≈ +0.30 in each. The detector tracks exactly the failure mode CP-1's "no-default law" identified — without the gate, without the oracle.
- **The immune tasks stay quiet on both axes.** `merge_intervals` / `lis_length` / `wildcard_match` / `roman_to_int` hold ΔD ≤ 0.04 and `F` at the L0 baseline. Specificity holds: the detector doesn't fire on tasks whose omitted conventions have universal defaults.
- **The confident-wrong blind spot did NOT materialize.** The plan anticipated that `base_convert@L3` might be high-F / low-D (the model confidently picks one default → samples agree despite failing → self-consistency blind). It isn't: `base_convert@L3` produces **3 distinct behaviours** across 30 samples (`D = 0.30`, not ~0). The 14B coder is *uncertain* on case, not *confidently-wrong*.

## Method

`qwen2.5-coder:14b` at default temperature, 6 ambiguity-graded tasks (the CP-1 `AMBIG` set), levels {0, 3} × 30 single-shot samples per cell. Per sample, paired from one generation:

- **`F` (label)** = `verify_with_dsl(text, HARDER_DSL)` — the gate's pass/fail on the *intended* interpretation. `Beta(1+fail, 1+pass)` posterior per cell.
- **`D` (signal, gate-free)** = normalized Shannon entropy over the behavioural-signature distribution. Each sample's signature is `tuple(repr(fn(*probe)) for probe in PROBES[task])` — exec the candidate against a fixed seeded probe battery of 8 inputs *with no expected outputs* (so it never touches the oracle). Normalize by `log N` so `D ∈ [0, 1]` (0 = all N behave identically, 1 = all N distinct).

ρ(D, F) is bootstrapped (2000 iters): resample samples *within* each cell to propagate per-cell sampling uncertainty, recompute D and F per draw, take Spearman across cells. The 95% CI is the sorted-draws interval.

**Why behavioral, not source-text, diversity.** The no-default law is about *interpretation*, not how the code is written. Text/AST diversity is dominated by cosmetic noise (variable names, formatting) and undercounts semantic divergence. Behavioural fingerprints capture interpretation directly.

**Confound — legitimate output multiplicity.** A DAG admits many valid topo orders, so `topological_sort` would be intrinsically divergent even when unambiguous. Two defenses: (1) the probes are unique-order chains + cycles + singletons, minimising legitimate freedom; (2) the primary per-task metric is **ΔD = D(L3) − D(L0)** — intrinsic multiplicity is present at both levels and cancels. `topological_sort`'s L0 D=0.12 is the residual baseline; the +0.31 rise is the ambiguity signal above it.

## Caveats / limits

- **One model, six tasks, two levels.** ρ=0.84 is a strong signal on this set; the no-blind-spot finding is conditional on N=30 at L3 — a model or task pair that genuinely always picks one default would falsify it. **CP-4 Phase-B** (intrinsic / realistic ambiguity, neutral LLM judge) is the generalisation check.
- **`base_convert` divergence is 3 of 8 possible at L3** — close to the predicted "weak default" extreme but not at the wall. A stronger default could still produce the blind spot.
- **N=30 sampling cost** is real at the experiment scale (10.5 GPU-hours on this hardware). Runtime CP-5 needs a separate engineering question: smallest N that still discriminates (a few samples? embedding-distance shortcut? AST clustering?).
- **Probe-battery design is task-coupled.** Building CP-5 needs either a probe-generation strategy or a probe-free divergence metric (e.g. behavioural-on-self-generated-inputs, source-AST clustering, embedding distance) as a follow-up.

## Reproduce

```pwsh
# from edge-cascade/, Ollama up, qwen2.5-coder:14b pulled, GPU free
git checkout experiment/self-consistency-ambiguity-2026-05-25      # local evidence branch
uv run python scripts/context_precision_selfconsistency.py --trials 30 --levels 0,3
# or detached + sleep-safe (the harness already holds the wake-lock):
pwsh -File scripts/run_cp2_detached.ps1
```

Result rewritten on each per-task checkpoint to `runs/bench/context_precision_selfconsistency_FULL.json` (`partial=true` until the run ends), telemetry appended to `runs/experiment-self-consistency-ambiguity.rec`.

## What this unlocks

- **CP-5 (disambiguation lever) — scope it.** Detect no-default conventions at runtime → surface the assumption / **ask** before guess-or-escalate. The signal exists; the engineering question is now N, latency, and the probe/probe-free trade-off.
- **CP-4 (Phase-B) — generalisation.** Repeat the discrimination test on intrinsic (not injected) ambiguity with the neutral LLM judge.
- **CP-6 (cloud-cost) — now motivated.** A runtime ambiguity detector localises *exactly where* pre-conditioning the prompt (clarity-rewrite, ambiguity-detect-and-ask) saves cloud tokens — the local-recovery lever (this finding) and the cloud-cost lever share one detector.
- **CP-3** — backfill `keep_awake()` into the legacy `context_precision_{calibrate,h1,ambiguity}.py` scripts (`context_precision_selfconsistency.py` already has it).
