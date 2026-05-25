# FINDINGS — coder ↔ reasoner complementarity (AI-4)

**Date:** 2026-05-25 · **Hardware:** RTX 5070 Ti Laptop, 12 GB · **Substrate:** Ollama (local, $0)

**Evidence (do not merge / do not delete):** `experiment/coder-r1-complementarity-2026-05-24` @ `1eb7449` — harness `scripts/complementarity_bench.py`, result `runs/bench/complementarity_20260524T145455Z.json`, telemetry `runs/experiment-coder-r1-complementarity.rec` (240 records). Run on a labeled LOCAL evidence branch per the experiment protocol; these findings leave it via this clean commit.

## TL;DR — DO NOT build the coder→r1 fallback (AI-3)

There is **no useful complementarity** between `qwen2.5-coder:14b` (coder) and
`deepseek-r1:14b` (reasoner). Where the coder actually fails, r1 fails too; where
both can answer, r1 is no better and is sometimes worse. A gate-failure-triggered
coder→r1 fallback would add a 12 GB model-swap for **~zero** expected rescue.

## Method

Paired Bayesian trials, **30/cell**, across the 4 `checks.dsl` tasks. Each cell =
one model's full attempt (generate → functional-verify → bounded repair, cap 2).
Per cell, posterior `Beta(1+resolved, 1+capped)`; per task `P(p_r1>p_coder)`,
`lift = (1−p_coder)·p_r1`, and the **paired conditional** `r1 resolved | coder
capped` (the fallback's actual value). Generation is Ollama-stochastic; the MC
analysis is seeded. Reasoner ran at `--reasoner-max-tokens 6000`; **0 timeouts**
(no `unavailable` records), so all caps are genuine failures, not truncation.

## Results

| task | coder resolve (mean [95% CI]) | r1 resolve (mean [95% CI]) | P(r1>coder) | lift [95% CI] | **paired r1 \| coder-cap** |
|---|---|---|---|---|---|
| add_numbers | 0.97 [.89, 1.0] (30/30) | 0.97 [.89, 1.0] (30/30) | 0.50 | 0.03 [.00,.11] | — (0 caps) |
| merge_sort | 0.97 [.89, 1.0] (30/30) | 0.97 [.89, 1.0] (30/30) | 0.50 | 0.03 [.00,.11] | — (0 caps) |
| **AVLTree** | **0.06 [.01,.17] (1/30)** | **0.03 [.00,.11] (0/30)** | 0.25 | 0.03 [.00,.11] | **0 / 29** |
| **dijkstra** | **0.94 [.83,.99] (29/30)** | **0.69 [.52,.83] (21/30)** | **0.00** | 0.04 [.01,.12] | 1 / 1 |

## What it means

1. **No complementarity (the decisive datum).** AVLTree is the only task with
   substantial coder caps (29/30). On those 29, **r1 rescued 0** — it hits the
   same wall. The marginal-based `lift` (~0.03–0.04) slightly overstates value
   because it borrows r1's success on the *easy* tasks; the **paired conditional
   (0/29)** is the honest measure and it is zero.
2. **r1 is not the better model at scale.** On dijkstra the coder *beats* r1
   (29/30 vs 21/30, `P(r1>coder)=0.00`). The coder's bounded repair loop is very
   effective; r1's single-shot reasoning is less reliable across 30 trials.
3. **Bayesian correction of a small-sample claim.** `FINDINGS-llm-vram-capability.md`
   reported "r1 2/2 fresh > coder 0/3 fresh" on dijkstra and cast r1 as the
   reasoning rung. At n=30 that **reverses** — it was small-sample noise (Beta on
   2/2 spans ~[.16, .99]). This is exactly why we run many trials and report
   posteriors, not point fractions.
4. **AVLTree is a capability ceiling for both 14B models** (coder 1/30, r1 0/30).
   Tasks of this class are a **genuine cloud-escalation case** — more *local*
   reasoning does not fix them; the lever there is Tier-3 or task decomposition,
   not a second 14B.

## Decision & impact

- **AI-3 (coder→r1 fallback) is shelved** — not worth the model-swap complexity
  for ~zero rescue. The `config.gpu_reasoning_model` seam (AI-2, `#43`) stays as a
  declared, unused option; the *flow* is not built.
- **Feeds the context-precision experiment** (`docs/EXPERIMENT-context-precision-routing.md`):
  its **H3** ("logic-fix arm: r1 vs deepseek-coder FIM") tilts hard **away from r1**
  toward coder-FIM — and, since adding a reasoner doesn't help, it strengthens the
  case that the real lever is **context precision, not model capability**.

## Reproduce

```powershell
# Ollama up; qwen2.5-coder:14b + deepseek-r1:14b pulled
uv run python scripts/complementarity_bench.py --trials 30
# -> runs/bench/complementarity_<ts>.json  +  runs/experiment-coder-r1-complementarity.rec
```
