# Design doc — Celery Canvas + RabbitMQ as a tunable, distributed mesh substrate

> Status: **decision doc + Phase-0 spike** (scope locked in review).
> Verdict: **pursue it** (opt-in; pipes stay default until a topology proves
> out). Reviewed decisions are recorded below and shape the whole design.

## Context

edge-cascade works and is fast. The driver is **not** performance — it's
**tunability**: select a *mesh topology* (low-power vs low-latency) instead of
one hardcoded cascade. Secondary: the IPC is stdio pipes, which couple
producer/consumer rate (fixed buffers, blocking — "pressure bursts pipes").
Proposal: model each tier as a Celery task over RabbitMQ; use **Canvas**
(`chain`/`group`/`chord`) to compose tasks into named topologies; move the
cascade orchestration off the model and into the workflow. `.rec` stays the
eyes-and-ears.

## Decisions (from review — these shape everything below)

1. **Multi-box is the goal.** NPU box and GPU box will become separate hosts.
   → Decisive: distributing tier workers across hardware is exactly what pipes
   cannot do and what Celery is built for. The case is **strong**, not marginal.
2. **Streaming is traded away.** The live "watch it hop" token stream is
   acceptable to lose for topology flexibility. → Removes the single biggest
   technical cost. Celery path is final-result-only; per-hop `.rec` still
   records, so the dashboard/replay view is unaffected.
3. **Orchestrator-on-Canvas (model B).** The cascade/escalation logic moves
   into the Canvas workflow; the agent submits `(query, topology)` and gets a
   verified answer or a "locals exhausted → your turn" signal.
4. **Scope = decision doc + Phase-0 spike.** Finalize the recommendation and a
   concrete minimal spike; decide on later phases after the spike proves it.

## Why this is the right call (given the above)

- **Latency is not the justification** — a model call is 2–20 s; broker
  round-trip is ~1–10 ms (<0.1%). Celery won't make it faster; it makes it
  *tunable and distributable*. Don't sell it on latency.
- **Tunability via Canvas is a genuine fit** — topology = composition.
- **Distribution is the unlock** (decision #1): one Celery worker per tier,
  pinned to its hardware, consuming its queue. The Intel box runs `npu`; the
  RTX box runs `gpu`; the broker routes between them. The single-process pipe
  model can't reach this.
- **Backpressure is solved properly** — RabbitMQ durable queues + `prefetch=1`
  on heavy tiers = a worker pulls the next job only when free. No burst pipes.

## Current architecture (what we're replacing)

- **Agentic path** (launched Claude = Tier 3): `scripts/edge-cli.ps1` →
  CLI with `--mcp-config` → each tier is a subprocess
  (`python -m mcp_servers.{npu,gpu,verify,cloud}`) over **FastMCP stdio
  transport** (JSON-RPC on stdin/stdout — *the pipes*). Each tool
  (`mcp_servers/gpu.py`) wraps an in-process worker (`cascade/gpu_worker.py`)
  and is wrapped by `@recorded(_REC)` (`mcp_servers/_rec.py`) → appends a
  length-framed record to `runs/<server>.rec`. **Routing lives in the model's
  reasoning** under `CLAUDE.md`.
- **Non-agentic path** (`cli.py` → `cascade/orchestrator.py:run_pipeline`):
  direct in-process calls, the **hardcoded** NPU→GPU→cloud cascade.

Both reuse the worker factories (`make_*_worker`) and the logfmt `.rec`
grammar — the shared core that makes a substrate swap feasible without
rewriting tiers.

## Proposed architecture

```
 agent/CLI ──MCP── mesh.solve(query, topology)        # the ONE entry (model B)
                       │ topology selector → Canvas signature
                       ▼
                  Celery Canvas (chain/group/chord)
                       │
              RabbitMQ broker  ──queues──▶  tier workers (pinned to hardware)
                       │                     ├ npu     queue → Intel box
              Redis result backend           ├ gpu     queue → RTX box
                       │                      ├ verify  queue → either
                       ▼                      └ cloud   queue → no worker unless enabled
              verified answer OR "capped → Tier-3"
   every task wraps the existing worker fn + writes runs/<tier>.rec  (unchanged eyes/ears)
```

- **Tasks**: one Celery task per tier op — `route`, `draft`, `generate`,
  `verify_syntax`, `verify_functional`, `repair`, `cloud_generate`. Body = the
  *existing* worker call + the `.rec` write (lift `_rec.recorded` into a Celery
  task decorator). Tiers don't change.
- **Broker**: RabbitMQ, one queue per tier (workers pin to hardware;
  `prefetch=1` on NPU/GPU for backpressure).
- **Result backend**: **Redis**, not RabbitMQ. RabbitMQ is the *broker*;
  `group`/`chord` need a real result backend → Redis. Stack = **RabbitMQ
  broker + Redis results**.
- **Model B surface**: the agent's per-tier tools collapse to one
  `mesh.solve(query, topology)` MCP tool that dispatches the Canvas workflow and
  returns the verified answer or a cap signal. The escalation dance (route →
  draft → verify → repair → escalate, 2-round cap) lives in Canvas, not the
  model. (Keep the raw edge-* tools for manual/debug use.)

## Topologies as Canvas (the tunable knob)

```python
# low-power: strict chain, NPU-only unless the gate forces escalation.
low_power  = chain(route.s(q), draft.s(), verify.s()).on_error(escalate_gpu.s())

# low-latency: speculate -- NPU draft AND GPU generate race; first gate-pass wins.
#   (this IS the old P2c "speculative GPU" -- a first-class chord, not
#    hand-rolled futures in orchestrator.py)
low_latency = chord(group(draft.s(q), generate.s(q)), pick_first_verified.s())

# balanced: today's cascade -- chain with escalate-on-failure + 2-round cap.
balanced   = chain(route.s(q), draft.s(), verify.s(), maybe_escalate.s())

# batch: map a topology across many tasks, distributed over workers.
batch      = group(balanced.clone([t]) for t in tasks)
```

Selector picks one at dispatch → topology is a config value, not a rewrite.

## What stays / what changes

- **`.rec` STAYS the ground truth.** Each task records exactly as the MCP tools
  do; `replay.py`/`dashboard.py`/`scripts/snapshot_evidence.py` keep working
  unchanged. **Multi-box wrinkle (decision #1):** each host writes its own
  `runs/<tier>.rec` → need aggregation (a `rec` events queue every task also
  publishes to, drained by one collector into the canonical `runs/` on the
  dashboard host). Single-box interim: shared `runs/`.
- **Worker factories STAY** — `make_*_worker()` called inside tasks; zero
  tier-logic rewrite. NPU pipeline's ~12–21 s compile stays warm in a resident
  worker (`worker_max_tasks_per_child` must NOT recycle it).
- **Routing LEAVES the model (model B).** `run_pipeline`'s cascade becomes the
  `balanced` Canvas workflow; the 2-round repair cap becomes a chord count /
  `link_error` chain — port faithfully so `over_cap_episodes` stays meaningful.
  The agent's job shrinks to: submit `mesh.solve`, handle the "capped → Tier-3
  takes over" return, and gate cloud.
- **Spend invariant preserved/strengthened**: the `cloud` queue has no worker
  unless explicitly enabled → structurally unspendable, same guarantee as
  today's `--strict-mcp-config` exclusion. `tier3_takeovers`/`cap_hits` map to
  the escalation edge; metrics unchanged (read from `.rec`).

## Streaming (resolved — decision #2)

Accepted loss. The Celery path returns final results, not token streams. The
worker still streams from Ollama *internally* (for its own latency) but returns
the completed text. The live console "hop across silicon" view goes away on the
Celery path; the **per-hop `.rec` events remain**, so `replay.py --last 1` and
the dashboard still show the hop sequence after the fact. No Redis pub/sub
progress channel for v1.

## Relationship to the existing backlog (consolidate, don't double-build)

Model B + Canvas **subsumes** several open items — build them *inside* the
substrate, not separately:
- **P2c (speculative GPU)** → the `low_latency` `group/chord`.
- **P2b (energy/joules)** → per-task accounting; discarded-work joules of a
  speculative `group` = a `chord` callback metric.
- **A6 (gpu→tier3 takeovers)** → the Canvas escalation edge; metric unchanged.
- **A5 (router short-circuit)** → low-power topology can skip `route` entirely;
  topology *is* the routing decision.
- **Vision tiers (image-gen + CV-analysis)** → naturally two more queues; the
  GPU image model's VRAM model-swap with the coder is exactly the kind of
  per-queue arbitration Celery makes clean.

→ Freeze P2b/P2c/A5 as standalone items pending the Phase-0 spike; if the
substrate proceeds, re-scope them as substrate features.

## Recommendation

**Pursue it. Run the Phase-0 spike, opt-in; pipes stay default until a topology
beats the hardcoded cascade on a real metric.** All four review answers point
the same way: multi-box is the goal (Celery's core payoff), streaming is no
longer a blocker, model B is wanted, scope is bounded to a spike. Sequence the
broader migration as Phases 1–3 *after* the spike de-risks it. Don't rip out
the working pipe path until a distributed topology is proven.

## Phase-0 spike spec (the agreed next concrete step)

**Goal:** prove the substrate end-to-end on the smallest real slice,
single-box, opt-in — enough to decide on Phases 1–3.

**Build (new, all opt-in; nothing in the hot path changes):**
- `docker-compose.yml` — RabbitMQ + Redis (dev only).
- `cascade/celery_app.py` — Celery app: RabbitMQ broker + Redis backend; one
  queue per tier; resident-worker config (no child recycling).
- `cascade/tasks.py` — wrap **two** tiers as tasks reusing the existing
  workers: `generate` (`make_gpu_worker`) and `verify_functional` (the
  verifier), each writing `runs/<tier>.rec` via the lifted recorder.
- `cascade/topologies.py` — two signatures: `balanced` (chain) and
  `low_latency` (chord with `group(draft, generate)`), selectable by name.
- `scripts/mesh_solve.py` — CLI: `python scripts/mesh_solve.py --topology
  low_latency "<query>"` → dispatch the Canvas signature, print the verified
  result + which tier answered.

**Verification (proof the substrate is viable):**
- `docker-compose up` → RabbitMQ + Redis up; `celery -A cascade.celery_app
  worker` starts and stays resident.
- `mesh_solve.py` runs both topologies on the RUNBOOK `dijkstra` task; the
  `low_latency` chord runs NPU + GPU concurrently and returns the first
  gate-passing answer; `balanced` reproduces the cascade.
- `runs/edge-gpu.rec` / `runs/edge-verify.rec` grow; **`replay.py` and
  `dashboard.py` render the Celery-path episodes identically to the pipe path**
  (same `.rec` grammar) — spend `$0`, tiers attributed, `over_cap`/`tier3`
  metrics intact. The load-bearing proof: the eyes-and-ears survive the swap.
- Capture a `scripts/snapshot_evidence.py` dir as the spike's committed proof.

**Decision gate after Phase-0:** does a topology beat the hardcoded cascade on a
real metric (low_latency P50 via speculation, or low_power joules/task once
energy accounting is in)? If yes → Phase 1 (all tiers as tasks) → Phase 2
(topology selector + model-B `mesh.solve`) → Phase 3 (distribute workers across
the NPU/GPU hosts + `.rec` aggregation — the multi-box payoff). If no → keep
pipes; this doc stands as the recorded investigation.

## Critical paths (for the spike)
- Reuse: `cascade/gpu_worker.py`, `cascade/npu_worker.py`, `cascade/verifier.py`,
  `validate_log.py` (functional gate), `mcp_servers/_rec.py` (recorder pattern),
  `cascade/logfmt.py` (`.rec` writer).
- New: `cascade/celery_app.py`, `cascade/tasks.py`, `cascade/topologies.py`,
  `scripts/mesh_solve.py`, `docker-compose.yml`.
- Unchanged consumers: `replay.py`, `dashboard.py`, `scripts/snapshot_evidence.py`.
