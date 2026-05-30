# FINDINGS — Canvas Phase 2 Slice 6b: the `low_latency` chord

> **Status:** methodology + protocol shipped; **wall-time results TBD** (the
> live numbers come from a run on the user's NPU + RTX 5070 Ti + local Redis,
> same as Slice 4 / [FINDINGS-canvas-phase1.md](FINDINGS-canvas-phase1.md)).
> Companion to [DESIGN-celery-phase2.md](DESIGN-celery-phase2.md) (Slice 6) and
> [DESIGN-celery-canvas.md](DESIGN-celery-canvas.md) (the topology table).

## What it is

`low_latency` is the headline composition Phase 2 was building toward — it
subsumes the old backlog item **P2c "speculative GPU."** Instead of the
`balanced` chain's **sequential** `route → draft → gate → swap → generate`, it
races the NPU draft against the GPU generate **concurrently** and takes the
first candidate that verifies:

```python
low_latency = chord(
    group(
        draft_task.s(query),                     # Tier-1 NPU draft
        chain(swap_task.s("qwen14b"),            # ensure coder resident
              generate_qwen14b_task.si(query)),  # Tier-2 GPU generate
    ),
    pick_first_verified.s(env=env),              # gate both, pick first verified
)
```

`pick_first_verified` gates the raced candidates **cheapest-first**: a verified
NPU draft wins (Tier-1 is ~free); else the GPU candidate; else `capped->tier3`.

## Two properties that shape it (and the comparison)

1. **A Celery chord callback fires only after ALL group members finish.** So
   "first verified" means *first in preference order that passes*, **not first
   to finish** — there is no early-kill of the slower arm. The latency win is
   running the two arms **concurrently**, not cancelling the loser.

2. **`low_latency` does NOT enter the bounded GPU repair loop.** That is
   `balanced`'s job. A double-miss (neither raced candidate verifies) hands
   straight to Tier-3. This keeps `low_latency`'s wall time bounded by the
   slower of the two single shots (plus gating) rather than a multi-round
   cascade — it is the FAST path, by design.

## The tradeoff (why it is NOT the default)

`low_latency` **always runs the GPU generate**, even when the NPU draft alone
would have sufficed. So on easy/trivial prompts it spends GPU tokens (and
latency, since the chord waits for the GPU arm) it didn't need — it can be
*slower and costlier* than `balanced` there. Since quality + $cost rank above
tok/s for this project, `low_latency` is a **per-workload topology choice,
never a default**.

**Where it should win:** workloads where the NPU draft *usually fails the gate*
(the 2026-05-20 finding: the 1.5B drafts lose on hard tasks). There, `balanced`
pays NPU **then** GPU back-to-back; `low_latency` overlaps them, so its wall
time ≈ `max(t_npu, t_gpu) + gate` instead of `t_route + t_npu + gate + t_swap +
t_gpu + gate`.

## Protocol (fill the table on the hardware)

Bring up the broker + a worker covering `npu,gpu,verify` (see
[BARE-METAL-CELERY.md](BARE-METAL-CELERY.md) or single-box
`docker compose up -d redis` + `python -m celery -A cascade.celery_app worker
-Q npu,gpu,verify --pool=solo -l info`), then time both topologies on the SAME
prompts:

```powershell
.\.venv\Scripts\python.exe scripts\mesh_solve_canvas.py --topology balanced     "<prompt>"
.\.venv\Scripts\python.exe scripts\mesh_solve_canvas.py --topology low_latency  "<prompt>"
```

The CLI prints `=== Canvas <topology> (<wall>s) ===` and the resolved tier.
Run each ≥3× and take the median (NPU compile + Ollama warmup dominate the
first call — discard it).

### Cases

| Case | Prompt class | Expected NPU gate | balanced wall (med) | low_latency wall (med) | low_latency tier | Notes |
|------|--------------|-------------------|---------------------|------------------------|------------------|-------|
| A    | trivial (e.g. reverse a string) | PASS | _TBD_ | _TBD_ | npu | low_latency expected ≥ balanced (paid GPU it didn't need) |
| B    | hard (e.g. dijkstra) | FAIL → GPU | _TBD_ | _TBD_ | gpu | the target win: overlap saves the sequential GPU leg |
| C    | impossible / contradictory | FAIL both | _TBD_ | _TBD_ | capped->tier3 | low_latency caps fast (no repair loop) |

### Decision gate (Phase-0 rule)

`low_latency` earns a place iff it beats `balanced` on **P50 wall time** for the
NPU-fail-heavy class (Case B) by a margin that justifies the extra GPU spend on
the Case-A class. If it doesn't, it stays an available-but-non-default topology
and this doc records the investigation (same disposition rule as Slice 4).

## What stays the same

- **`.rec` is unchanged.** Both raced arms record their tier lanes
  (`edge-npu.rec` / `edge-gpu.rec`) via the same `@recorded` worker fns; the
  gate records `edge-verify.rec`. `replay.py` / `dashboard.py` read a
  `low_latency` run identically to any other.
- **Spend invariant holds.** No `cloud` task is in the chord; the paid tier is
  untouched (and structurally unspendable without a `cloud`-queue worker).
- **`mesh.Outcome` shape.** `solve_low_latency_canvas` returns the same
  dataclass as `balanced` / the pipe — callers swap topologies by entry point.
