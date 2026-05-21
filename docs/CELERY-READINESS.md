# Celery-readiness charter — how we build *before* the substrate swap

> Status: **standing guardrail.** Celery Canvas is on the backlog (see
> [DESIGN-celery-canvas.md](DESIGN-celery-canvas.md), backlog C1/C2), *not* in
> the hot path. This doc governs the work we do **in the meantime** so that
> in-process work becomes the seam Celery snaps onto — not coupling that has to
> be unraveled at onboarding.

## The frame: stage-setting *is* model-B, done in-process first

The eventual Celery payoff (decision doc) is **multi-box distribution** of tier
workers. But two of its benefits — **deterministic orchestration** (a real cap,
not a prompt rule) and **topology selection** (routing as config, not branching)
— need no broker at all. They live in plain-Python orchestration today.

So we build them now, **shaped so the executor swap (in-process call → Canvas
`chain`/`group`/`chord`) is mechanical.** That is "model-B minus the broker":
the agent calls one `solve(query, topology)`; the cascade shape, the cap, the
ops, and the `.rec` recording all stay put when Celery later swaps what runs
underneath.

### The coupling that already exists (and that Celery will unravel)

The cascade is implemented **twice** today:
- **In-process:** `cascade/orchestrator.py:run_pipeline` — deterministic but
  *single-shot per tier* (`gpu_then_cloud` does one `generate`; **no repair
  loop**), routing is hard-coded difficulty branches.
- **Agentic:** the launched Claude runs the repair loop *by reasoning*, with the
  2-round cap living only as a prompt rule in `CLAUDE.md`. **This is the
  implementation that breached the cap** (observed: 3 over-cap episodes).

The cap therefore exists as code **nowhere**. Adding new logic to the prompt or
piling more `if/else` into `run_pipeline` *deepens* the exact duplication Celery
must tear out. The Celery-ready move is to **converge both paths onto one
deterministic orchestrator the agent merely calls.**

## The invariants (every story PR must hold these)

Chosen so the executor swap stays mechanical. If a change can't satisfy one,
that's a design smell to fix *before* it lands.

1. **The tier op is the only unit.** All work goes through the existing ops —
   `route`, `draft`, `generate`, `verify_syntax`, `verify_functional`,
   `repair`, `cloud_generate`. Never reimplement a tier inline or reach past a
   worker into Ollama/OpenVINO. *(Celery wraps each op as a task body verbatim;
   logic outside the op can't move to a worker.)*

2. **Op boundary = serializable data in/out.** Plain dicts/dataclasses,
   JSON-clean — no live handles, sockets, or shared objects crossing between
   ops. **Litmus: if `mcp_servers/_rec.py:recorded` can't cleanly serialize it,
   it can't be a Celery task.** *(Args/results cross Redis.)*

3. **Orchestration = composition over ops, selected by a named topology,
   expressed as data, in one place.** Replace scattered difficulty branches +
   prompt rules with a topology table `{name → ordered steps + cap}`. *(Design
   thesis: "topology is a config value, not a rewrite." The table maps 1:1 onto
   `chain`/`group`/`chord`.)*

4. **Every policy count is deterministic code with one constant — never a
   prompt rule.** The 2-round cap is a bounded loop using one
   `REPAIR_CAP_MAX`. *(Ports to "a chord count / `link_error` chain"; a prompt
   rule ports to nothing, and drifts — see the breach.)*

5. **`.rec` stays at the op boundary, one append-only stream per tier; nothing
   assumes co-location.** No cross-tier in-memory coordination or shared result
   cache — ops communicate *only* via explicit I/O + `.rec`. *(Multi-box means
   separate hosts and separate `runs/`; a shared in-process scoreboard breaks
   the instant tiers split.)*

6. **No new dependence on streaming or single-process liveness.** Streaming
   stays internal to a worker; ops return final results. *(Streaming is exactly
   what the Celery path removes.)*

## The severe stories, shaped to land on the seam

Derived from the 2026-05-20 log review (Dijkstra benchmark: 27% functional-gate
pass rate, 3 cap breaches, NPU draft never the final tier, Tier-3 takeovers
invisible).

- **S1 — One transport-agnostic orchestrator: topology table + deterministic
  cap; agent calls `solve(query, topology)`.** The keystone — kills the cap
  breach (inv. 4), removes the dual-implementation coupling, *is* the Celery
  seam. `CLAUDE.md` shrinks to "call `solve`, handle `capped→Tier-3`."
- **S2 — A `hard-task` topology that skips the always-failing NPU draft** (the
  `npu:0` finding). Once S1's table exists this is one row of config, not code.
- **S3 — Dijkstra-class model fix (few-shot the sink-node-init pattern).**
  Substrate-independent; lives entirely inside the `draft`/`generate` op
  prompts; the cheapest real lift to the gate pass rate. Can run in parallel.
- **S4 — Record the Tier-3 takeover edge** so `capped→tier3` stops inferring as
  "unresolved." Falls out of S1's return value.

Build order: **S1 first** (the seam everything else hangs off; banks the cap fix
+ de-duplication immediately), then S2/S4 on top of it, S3 in parallel.

## How to use this doc

Reference it in every severe-story PR description; check the diff against the six
invariants before merge. When Celery onboarding begins (C1), this charter is the
acceptance test for "did we keep it mindful?" — the swap should touch the
*executor*, not the topology table, the cap, the ops, or `.rec`.
