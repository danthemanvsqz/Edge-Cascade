---
name: edge-cascade
description: >-
  Route a coding/reasoning task through the local heterogeneous inference mesh
  via the Canvas pipeline (one call: NPU route → NPU/iGPU draft → deterministic
  gate → bounded GPU repair → win/lose logger). Use when the user invokes
  /edge-cascade, asks to run a task "through the cascade / mesh / pipeline /
  local tiers", or wants local-first inference with the agent (Tier 3) as the
  ceiling. The pipeline is the SINGLE inference path — you do not hand-drive
  per-tier MCP servers. Needs the Canvas substrate up (Redis broker + a Celery
  worker on npu,gpu,verify); launch with `edge-cli.ps1 -Canvas`.
---

# Edge Cascade — router skill

You are **Tier 3**: the agent, the ceiling, and the *only* tier that writes
files and runs commands. You are **not** the per-step router anymore — the
**Canvas pipeline** (`cascade.mesh.solve`, dispatched as a Celery signature) is
the router, defined ONCE in code. You invoke it as a single blocking call and
act on the one result it hands back. Do **not** hand-orchestrate `edge-npu` /
`edge-gpu` / `edge-verify` / `edge-cloud` MCP tools step by step — that path is
retired.

## The rules (non-negotiable)

**Every artifact goes through the pipeline first.** This means: code, git/CLI
commands, scripts, configs, commit messages — anything that produces a concrete
output. Route it, let the locals draft it, take over only when the pipeline
hands back `capped->tier3`. Pure conversational replies and analysis-only turns
are the only exception.

**Decompose before routing.** For any non-atomic prompt, identify independent
sub-tasks first (Tier 3 reasoning, no model needed), then route each:
- Fan-out independent sub-tasks: `--topology budget_fanout "sub1" "sub2" "sub3"`
- Iterate dependent sub-tasks: sequential `budget` calls with carried context
- Hybrid: fan-out the independent parts, iterate the dependent ones

## How to route (one call)

CLI (preferred for interactive use):

```
uv run python scripts/mesh_solve_canvas.py --topology budget "<task>"
```

For large multi-part tasks — decompose first, then fan-out:

```
uv run python scripts/mesh_solve_canvas.py --topology budget_fanout "sub1" "sub2" "sub3"
```

Programmatic (inside the repo):

```python
from cascade.canvas_client import solve_budget_canvas, solve_budget_fanout
outcome = solve_budget_canvas(query, dsl=None)          # single task -> mesh.Outcome
outcomes = solve_budget_fanout([sub1, sub2], dsl=None)  # parallel -> list[mesh.Outcome]
```

- `--topology budget` (default) — the sequential cost-ordered cascade. Use it
  for almost everything.
- `--topology budget_fanout` — agent decomposes the task into sub-tasks, each
  runs as an independent budget cascade in parallel. You (Tier 3) reason the
  sub-task list and integrate the results; the pipeline handles each sub-task.
- `--topology low_latency` — races the NPU draft against the GPU generate (chord).
  The GPU **always** runs, so it costs more than budget on easy prompts: a
  per-workload choice, **never the default** (quality + $cost rank above tok/s,
  per the metric-priorities rule). Use only when wall-latency genuinely matters.
- `--dsl "<text>"` — optional functional-gate assertions; omit for syntax-only
  gating.

## Decomposition (mandatory pre-routing step)

Before routing ANY non-trivial prompt, check: does it contain clearly independent
or sequential sub-tasks?

1. **Reason** the sub-task list yourself (Tier 3 — no model call needed)
2. **Fan-out** if independent: `--topology budget_fanout "sub1" "sub2" "sub3"`
3. **Iterate** if dependent: sequential `solve_budget_canvas` calls, carry context
4. **Hybrid**: fan-out the independent parts first, then iterate the dependent tail
5. **Merge** the sub-results yourself (you integrate, not the pipeline)

Only use a single `budget` call when the task is genuinely atomic.

## Git and CLI command generation

Route NL→git/shell tasks through the pipeline exactly like code tasks:
```
uv run python scripts/mesh_solve_canvas.py --topology budget "git command to <action>"
```
**Gate note:** raw git/shell output fails the Python syntax gate → capped→tier3.
That is correct — the pipeline is consulted and logged; Tier 3 executes the
capped result. qwen2.5-coder:14b (GPU tier) scores 97% on git tasks Tier B
(see `docs/FINDINGS-git-model-selection.md`).

## What the pipeline does (informational — you don't drive these)

`route` (NPU, returns advisory difficulty) → NPU/iGPU `draft` → deterministic
`gate` → bounded GPU repair loop (hard cap = `config.repair_cap`, default 2; a
further round is structurally impossible) → **win/lose logger last** (appends
the outcome to `runs/cascade.rec`; this is what makes the dashboard count the
run, so a routed task is self-logging — never bypass it). Cloud (Tier 4) is
**not** on the default worker's queues, so paid spend is structurally impossible
without an explicit opt-in.

## Acting on the Outcome

`mesh.Outcome` fields: `answer`, `final_tier` (`"npu"｜"gpu"｜"capped->tier3"`),
`resolved`, `capped`, `repair_rounds`, `difficulty`, `topology`, `trace`.

1. **`resolved` is true (WIN, local @ npu/gpu):** use `outcome.answer`. Report
   which tier answered and the `trace`. Integrate/review it as Tier 3 before it
   lands in the repo — "verified by the gate" ≠ "fit for the codebase."
2. **`capped` is true (`final_tier == "capped->tier3"`, LOSE):** the locals are
   exhausted and have handed the task to **you**. Author it yourself (Tier 3).
   Do **not** start another repair round — that breaches the cap. Reach for Tier
   4 (paid cloud) only on a genuine deadlock *you* can't break or an explicit
   user request, and only budget-gated.

## Rules

- **Pipeline-first, always.** Code, git/CLI commands, scripts, configs, commit
  messages — any artifact goes through the pipeline. No hand-written from-scratch
  code or commands that skip the cascade; no hand-driving the per-tier MCP tools.
  One `solve_*_canvas` call.
- **Decompose before routing.** Check for independent/dependent sub-tasks first.
  Fan-out the independent ones, iterate the dependent ones, hybrid if mixed.
- **Don't auto-skip to Tier 3 on the score.** The NPU difficulty signal is
  advisory and over-rates short / well-scoped tasks — from-scratch code still
  gets a local draft pass. Going straight to Tier 3 is for `capped` results or
  genuinely surgical edits to existing code.
- **Substrate must be up.** The pipeline needs the Redis broker + a Celery
  worker on `npu,gpu,verify`. Stand it up with
  `powershell -ExecutionPolicy Bypass -File scripts\edge-cli.ps1 -Canvas`
  (or a worker by hand:
  `uv run python -m celery -A cascade.celery_app worker -Q npu,gpu,verify --pool=solo -l info`).
  If it is **not** running, say so and offer to launch it — do **not** silently
  fall back to writing the code by hand. (`python cli.py --topology <name>
  "<task>"` is the equivalent in-process pipe path if the broker is unavailable.)
- **Cloud is paid + opt-in.** Tier 4 never runs on the default queues; it is the
  budget-gated last resort, not a convenience.
- **What NOT to route:** pure conversational replies, analysis/explanation with
  no output artifact, yes/no decisions.
