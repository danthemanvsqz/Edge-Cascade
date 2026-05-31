# Findings: Git Command Generation — Model Selection

**Branch:** `experiment/git-model-selection-2026-05-30`
**Evidence sha:** see `git log experiment/git-model-selection-2026-05-30`
**Date:** 2026-05-31

---

## TL;DR / Decision

**`qwen2.5-coder:14b` is the production choice** — 97% Tier-B pass-rate, near-perfect
on simple tasks, qualifies decisively under the lower-CI > 70% decision rule.
The initial hypothesis (coder model is poor at git) was wrong.

**If a lighter model is needed:** `qwen2.5-coder:7b` qualifies on Tier B (86%, CI low
81%) and statistically ties the 14b on Tier C (P=0.61). Wire in the 7b for speed,
accept a ~11pp drop on Tier B.

**Do not use `deepseek-r1:14b` for git tasks.** Chain-of-thought reasoning is
counterproductive — the model "thinks" itself into wrong flag combinations. P(r1 >
baseline) = 0.00 on every tier.

---

## Posterior Table

20 tasks, 3 tiers (A = simple, B = medium, C = complex), 30 trials per task.
Beta(1+pass, 1+fail) posteriors; 95% credible interval; P(model > baseline) via
100k Monte-Carlo samples.

| Model | Tier A mean [95% CI] | Tier B mean [95% CI] | Tier C mean [95% CI] | P(>base) B |
|---|---|---|---|---|
| `qwen2.5-coder:14b` (baseline) | 99% [97, 100] | 97% [95, 99] | 84% [78, 89] | — |
| `qwen2.5-coder:7b` | 75% [68, 80] | 86% [81, 90] | 85% [80, 90] | 0.00 |
| `deepseek-r1:14b` | 70% [64, 76] | 61% [55, 68] | 64% [57, 71] | 0.00 |
| `deepseek-coder:6.7b` | 50% [43, 56] | 45% [38, 52] | 60% [53, 67] | 0.00 |

Note: P(qwen7b > baseline) on Tier C = **0.61** — effectively tied at that tier.

---

## Method

**Script:** `scripts/git_model_bench.py`

**Gate:** per-task structural check — `_extract_command()` strips `<think>` blocks
and code fences, returns the first `git ...` line; gate checks `must_start` and
`must_contain` tokens. `alt_pass` OR-logic covers tasks with multiple valid forms
(e.g. `git branch --show-current` vs `git rev-parse --abbrev-ref HEAD`).

**Live dashboard:** bench pushes `GIT_MODEL_SELECTION_GRAPH` topology on startup
and emits `cascade.live.nodes` `{node, state}` events per trial so the dashboard
animates the active model node and gate.

**Reproduce:**
```
git checkout experiment/git-model-selection-2026-05-30
uv run python scripts/git_model_bench.py --no-pull
```

---

## Sub-experiment B: Dashboard Arbitrary Topology Rendering

**Hypothesis:** the dashboard `buildTopologyFromGraph()` auto-layout handles novel
shapes without code changes.

**Shapes tested (all pushed live via Redis, no worker restart):**

| Shape | Nodes | Result |
|---|---|---|
| `minimal` (2 nodes, route → done) | ✅ Rendered — compact SVG, no repair row |
| `fan_out` (parallel NPU + GPU arms) | ✅ Rendered — GPU node visible, parallel columns |
| `split_merge` (diverge + repair loop + merge) | ✅ Rendered — repair node floated above |
| `chain_long` (7 linear nodes) | ✅ Rendered — SVG widened to accommodate |
| `git_model_selection` (4 parallel GPU models) | ✅ Rendered — live node pulses during bench |

**Decision:** Dashboard is genuinely topology-agnostic. New topologies require only
a `TopologyGraph` definition in `cascade/topology_graph.py` — no dashboard code changes.
The `buildTopologyFromGraph()` rank algorithm correctly handles all tested shapes.

**Fix discovered:** `dashboard/ecosystem.config.cjs` had the wrong `tsx` path for
the Windows workspace layout. Fixed in the same branch (`a94a935`).

---

## Caveats

- Gate accepts the canonical command form; `qwen2.5-coder:7b` and `r1:14b` may
  produce semantically correct but structurally different commands that fail the
  structural gate (e.g. `git add --update` instead of `git add -u`). True accuracy
  is likely higher than reported for those models.
- `deepseek-r1:14b` `<think>` blocks are stripped before gating — the 70% Tier A
  score reflects the model's accuracy after thinking, not a gate artifact.
- All models are code-specialised; a general-purpose model (`qwen2.5:14b`) was not
  pulled and may outperform all of them on git — recommended follow-up if the 7b
  is not sufficient.
