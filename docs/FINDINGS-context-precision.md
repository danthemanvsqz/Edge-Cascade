# FINDINGS — context precision vs. distraction (the real failure lever)

**Date:** 2026-05-25 · **Hardware:** RTX 5070 Ti Laptop, 12 GB · **Substrate:** Ollama (local, $0)

**Evidence (do not merge / do not delete):** `experiment/context-precision-2026-05-25` @ `b2cfbda` — harness `scripts/context_precision_{calibrate,h1,ambiguity}.py`, results `runs/bench/context_precision_{h1,ambiguity}.json`. Distraction result @ `86e9214`, ambiguity result @ `b2cfbda`.

## TL;DR — imprecision, not noise, is the lever

A capable local coder is **robust to irrelevant context (distraction)** but
**brittle to under-specification (ambiguity)** — and the ambiguity failures are
**~94% recoverable just by restoring precision**. So the high-value context
intervention is **adding the missing precision, not stripping clutter.**

| noise level | distraction (irrelevant junk) | **ambiguity (stripped precision)** |
|---|---:|---:|
| L0 (clean) | 0.98 | 0.98 |
| L1 | 0.95 | **0.76** |
| L2 | 0.98 | **0.81** |
| L3 (heavy) | 0.93 | **0.64** |
| **P(noise hurts: L0 > L3)** | 0.88 (negligible) | **1.00** |
| **oracle recovery of failures** | 3/3 | **94% (29/30)** |

## Method

`qwen2.5-coder:14b`, 4 clean-baseline tasks (coder ~100% at L0), noise levels
0→3 × 10 trials, **single-shot** (the failure-aware design routes on the first
failure), Bayesian (`Beta(1+pass,1+fail)` per level). On a failure, retry once on
the recovered prompt → that recovery rate is the **ceiling of the context lever**.
Two injectors:
- **distraction** (`context_precision_h1.py`): bury the task in irrelevant
  distractors / verbose preamble / red-herrings; oracle = the clean prompt.
- **ambiguity** (`context_precision_ambiguity.py`): strip the disambiguating
  details so the model must guess; oracle = the **precise** prompt.

Task set was **calibrated, not guessed** (`context_precision_calibrate.py`): the
14B aces standard data-structure/algorithm tasks; **parser/interpreter-class is
its frontier**. The clean baselines here are the 100%-at-L0 tasks, so any added
failure is attributable to the injected noise.

## The decisive result

- **Distraction is shrugged off.** 98→93% across heavy noise; `P(hurts)=0.88` but
  the effect is ~negligible. A capable model ignores obvious junk.
- **Ambiguity bites hard and is cheaply recoverable.** 98→**64%**;
  `P(hurts)=1.00`; **94% of the ambiguity failures were fixed simply by giving the
  precise prompt.** The model isn't incapable — it's under-specified, and
  precision rescues it.

## The "no-default law" (the sharp, actionable core)

Ambiguity was **task-selective**, and the pattern is the finding:

| task | behaviour under ambiguity | why |
|---|---|---|
| `topological_sort` | breaks at the **slightest** imprecision (L1 already 1/10) | direction convention is **arbitrary** — no shared default |
| `base_convert` | breaks only at L3 (dropped "lowercase") | output case has a **weak** default |
| `merge_intervals`, `lis_length` | **immune** | "merge touching" / "strictly increasing" **are** the near-universal defaults |

**Law: ambiguity only bites when the omitted convention has no default the model
AND the consumer (gate / user) agree on.** Most omitted details the model fills
correctly from priors; the failures cluster on **no-default conventions**. So the
intervention should be **surgical — pin the no-default conventions** — not blanket
prompt-rewriting.

## Implications

- **Build a disambiguation lever, not a noise-stripper.** Detect no-default
  conventions → surface assumptions / **ask** before guessing-or-escalating.
  (For genuinely arbitrary conventions like the topo direction, there is no right
  guess — asking is the only correct move.)
- **A `$0` detector may exist:** self-consistency — sample N times and watch where
  candidates **disagree** on a convention; that divergence localizes the
  no-default detail. (To test next.)
- **Dual payoff toward the cloud-budget goal:** a disambiguation step before the
  local attempt could prevent up to ~94% of imprecision-driven **escalations**,
  and any that still escalate hand the cloud a precise prompt (less hedging →
  fewer output tokens). See `EXPERIMENT-context-precision-routing.md` and the
  prompt-conditioning direction.
- **Caveat — generalization:** these are crisp coding tasks with *injected*
  ambiguity; real prompts have *intrinsic* ambiguity. The principle should hold;
  the magnitude on real prompts is the Phase-B question (realistic prompts + an
  LLM judge). The generalizable nugget is the **no-default law**, not the 64%.

## Aside — a real gate bug found & fixed en route

A candidate that `print()`s to stdout corrupted the funcverify gate's JSON result
(`JSONDecodeError`) — any LLM code with a stray `print()` would crash
`edge-verify.verify_functional`. Fixed in `mcp_servers/_funcverify_child.py`
(redirect candidate stdout during exec; PR #49 `8e12318`, regression test).

## Reproduce

```powershell
# Ollama up; qwen2.5-coder:14b pulled
uv run python scripts/context_precision_calibrate.py --trials 10        # bucket difficulty
uv run python scripts/context_precision_h1.py --trials 10 --tasks merge_intervals,base_convert,lis_length,topological_sort
uv run python scripts/context_precision_ambiguity.py --trials 10 --tasks merge_intervals,base_convert,lis_length,topological_sort
```
