# Experiment — Context Precision vs Capability (failure-aware repair routing)

> **Status:** PLANNED, not built (designed 2026-05-24; user chose save + wait).
> **Priority:** potentially high impact / **S4 → parked**; **BLOCKED** by the
> in-flight AI-4 run (GPU contention + AI-4's r1 result feeds an arm). See
> [Prioritization](#prioritization). **Build P0 is `$0`/no-GPU and unblocked.**
> Companion design memory: `edge-cascade-context-precision-experiment`.

## 1. The design under test

A **failure-aware routing cascade**. Today's cascade (`cascade/mesh.py`) is a
linear escalation: route → NPU draft → bounded GPU repair loop → cap → cloud.
This design instead makes the capable coder the **primary**, and on failure
**diagnoses *why* it failed** and routes to a *specialized* repair tier.

```
[User Prompt / Agent Task]
            │
            ▼
   [Tier 1: Qwen 14B on dGPU]  (fast primary generation)
            │
   ┌────────┴────────┐
(Passes)         (Fails)
   ▼                 ▼
[DONE]        [Where did it fail?]   ← deterministic, from the gate
            ┌────────┴────────┐
     (Logic/Structure)   (Context Noise)
            ▼                  ▼
   [Tier 2: DeepSeek]   [Tier 2: Qwen 1.5B NPU]
   (deep logic / FIM)   (strip context / JSON)
            │                  │
   ┌────────┴───┐              ▼
(Passes)    (Fails)     [back to dGPU: clean retry]
   ▼            └──────────────┤
[DONE]                ┌────────┴────────┐
                  (Passes)          (Fails)
                     ▼                  ▼
                  [DONE]        [Tier 3: Claude — FINAL FIX ONLY]
```

Three departures from today: **14B-first** (small models become specialists,
not drafts); **failure-*classified* routing** (the heart of it); **role-based
tiers** (primary / specialist-repair / final), with **Claude demoted to
repair-the-candidate, not regenerate**.

## 2. Headline hypothesis

**User context is the most imprecise node in the pipeline.** Much of what looks
like "the model isn't good enough" is really *noisy context*, recoverable by
**cleaning the context and retrying the same 14B** — far cheaper than swapping in
a reasoner. The experiment turns every open design question into a measured
variable rather than presupposing it.

| | Question (measured, not assumed) |
|---|---|
| **H1 (headline)** | % of 14B failures rescued by cheap context-clean + retry (NPU) vs needing an expensive reasoner |
| **H2** | does the gate-derived failure-class predict the best repair arm? (class-aware routing > blind escalation) |
| **H3** | logic-fix arm: `deepseek-r1` (reasoning rewrite) vs `deepseek-coder` (FIM patch) — *FIM is a coder feature; r1 has none* |
| **H4** | Claude *final-fix-from-candidate* vs regenerate (the [tier-3 repair-from-candidate] question, subsumed here) |

## 3. The classifier is deterministic (the key de-risk)

The "where did it fail?" fork needs **no fragile LLM router** — the existing gate
(`mcp_servers/verify.py`) already yields the class:

| Gate signal | Class | Route |
|---|---|---|
| `verify_syntax.has_code = False` | **Context** (output unusable/buried) | NPU strip/reformat → 14B retry |
| `verify_functional.passed=False` + `failures[]` (ran, wrong) | **Logic** | DeepSeek deep fix |
| `verify_functional` timeout | **Logic** (infinite loop) | DeepSeek |
| `verify_syntax.passed=False` / `ran=False` (won't compile / crashes on load) | **Structure** (ambiguous) | tiebreak rule |

## 4. Experiment design

- **Independent variable:** synthetic **context-noise level `0→3`** injected
  (distractors, irrelevant prior turns, verbose framing) into base tasks — the
  clean knob for H1.
- **Arms**, raced on each 14B failure and branched by the gate-class:
  `context-clean→14B-retry` (NPU) · `r1 reasoning` · `deepseek-coder FIM` ·
  `Claude final-fix`.
- **Stats:** paired, Bayesian — per cell `Beta(1+resolved, 1+capped)`,
  `P(armA>armB)`, lift — across `(arm × failure-class × noise-level)`. Mostly
  **`$0` local**; the Claude arm is paid → credit-guarded, small N.
- **Metrics:** rescue rate; tokens (`prompt_eval_count`/`eval_count` local,
  usage for cloud); **swap-count** (the 12 GB tax — 14B↔r1/coder can't
  co-reside); cost. Headline number = the H1 fraction.

### Grader — LLM-as-judge, made rigorous

General agent tasks have no compiler, so grading is an LLM-judge — the most
general and the most demanding grader to do right. Controls:

- **Neutral judge OUTSIDE the arm roster** (e.g. `llama3.1:8b`/`mistral`) — our
  qwen/r1/coder are all arms, so an in-roster judge would self-favor its own arm.
- **Blind pairwise + position-swap**, **temp 0 + fixed seed**, judge rationale
  logged to the `.rec`.
- **Calibrate** the judge against the deterministic-gradeable (`checks.dsl`-style)
  anchor tasks — measure its TPR/FPR — and **propagate that error into the
  posteriors**. Determinism doesn't vanish; it becomes the judge's calibration
  anchor.

## 5. Scope & phases

**Scope:** general agent tasks (general-but-verifiable for the deterministic
anchor + open-ended via the judge). **Phase A** synthetic graded noise (clean
H1/H2 signal) → **Phase B** small hand-authored realistic set (external
validity).

**Phases** (paced; LOCAL evidence branch, **never merged**):

| Phase | What | GPU? | Blocked? |
|---|---|---|---|
| **P0** | build: noise-injector + neutral judge + calibration + arm runner + smoke | no | **unblocked, `$0`** |
| **PA** | synthetic graded-noise runs | yes | queued behind AI-4 |
| **PB** | small realistic-set validation | yes | after PA |
| — | findings: clean commit to `main` citing branch + sha | — | — |

Reuses the `scripts/complementarity_bench.py` harness pattern + the segregated
`make_experiment_recorder` telemetry lane.

## 6. Dependencies & relationships

- **Blocked by the in-flight AI-4 run** (`coder↔r1` complementarity): the GPU
  runs (PA/PB) wait for the card, and **AI-4's r1 result feeds H3** — if r1
  rarely rescues the coder, H3 tilts toward coder-FIM before we run.
- **Subsumes** the parked `AI-3` (coder→r1 rung) and the tier-3
  repair-from-candidate idea — both are arms here.

## 7. Prioritization

Per `docs/PRIORITIZATION.md`:

- **Impact: I4 (potentially high)** — a paradigm shift for the mesh (failure-aware
  routing + a possibly-dominant cheap context-repair lever).
- **Severity: S4 (severe)** — new classifier + new ops (NPU strip, FIM, swap
  arbiter) + core control-flow change + the 12 GB model-swap tax.
- **Zone: ⏳ parked + de-risk**, and **this experiment is the de-risk spike** —
  cheaply test H1/H2 before committing to build the topology.
- **Status: BLOCKED** by the current experiment for the GPU phases; **P0 (build)
  is unblocked and `$0`.**

## 8. Boundary

Local, single-user, `$0`-first research; the one sanctioned paid lane (the Claude
arm + any paid judge spot-check) stays credit-guarded. Evidence branch is raw
evidence — never merged; findings leave via a clean commit that cites the
branch + sha.
