"""Client-side entry for the Canvas substrate -- model B, one blocking call.

The agent (or `scripts/mesh_solve_canvas.py`) calls `solve_balanced_canvas`,
which dispatches the Canvas signature, blocks ONCE on the final envelope, and
adapts it to a `mesh.Outcome` so callers swap from `mesh.solve` without
changing their consumption code (charter inv. 3: the topology table is a
config value, not a rewrite).

THE ONE `.get()` (the client boundary): no task in the chain calls `.get()` on
another task -- that's the worker-blocking-on-children anti-pattern. The
chain's data dependency is its own composition (each step's input is the
previous step's output, via Celery), and the gpu-solve handoff uses
`self.replace()` (proven Slice 3) rather than blocking-and-waiting.
"""
from __future__ import annotations

import time
import uuid
from itertools import count
from pathlib import Path

from cascade import mesh
from cascade.config import CONFIG
from cascade.logfmt import dump_record
from cascade.topologies_canvas import balanced_signature, low_latency_signature

# --- cascade-outcome telemetry lane (the SD-4 dashboard panel) --------------
# The in-process orchestrator (cascade.orchestrator.write_record) appends one
# record per Outcome to runs/cascade.rec; the dashboard's mesh-effectiveness
# panel reads that lane. The Canvas client bypasses the orchestrator, so emit
# the SAME lane here -- otherwise SD-4 stays blank on Canvas runs even though
# the per-tier edge-*.rec lanes (the flow river) light up. Process-stable
# run_id + a per-process seq mirror the orchestrator's session fields.
_RUN_ID = uuid.uuid4().hex[:12]
_seq = count()


def _cascade_rec_path() -> Path:
    """`runs/cascade.rec` -- resolved at call time (CONFIG is a frozen instance
    set at import; resolving here keeps the path patchable in tests so they
    don't append to the real runs/ lane)."""
    return Path(CONFIG.log_path).with_suffix(".rec")


def _record_outcome(query: str, outcome: mesh.Outcome, wall_s: float) -> None:
    """Append one cascade-outcome record so the dashboard counts Canvas runs
    like pipe runs. Field set matches `cascade.orchestrator.write_record` (the
    dashboard reads `final_tier` + `trace`; `replay.py` reads the rest).
    Best-effort: telemetry must never break a solve, so a write error is
    swallowed."""
    rec = dump_record(next(_seq), {
        "ts": f"{time.time():.3f}",
        "run_id": _RUN_ID,
        "query": query,
        "answer": outcome.answer or "",
        "final_tier": outcome.final_tier,
        "topology": outcome.topology,
        "total_latency_s": f"{wall_s:.2f}",
        "trace": "\n".join(outcome.trace),
    })
    try:
        path = _cascade_rec_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as fh:
            fh.write(rec)
    except OSError:
        pass  # best-effort telemetry; never fail a solve on a .rec write


def _to_outcome(env: dict) -> mesh.Outcome:
    """Adapt a final Canvas envelope to the `mesh.Outcome` dataclass `mesh.solve`
    returns, so every Canvas topology is consumed identically to the pipe path
    (charter inv. 3). Shared by all `solve_*_canvas` entries."""
    return mesh.Outcome(
        answer=env["answer"],
        final_tier=env["final_tier"] or "capped->tier3",
        resolved=bool(env["resolved"]),
        capped=bool(env["capped"]),
        repair_rounds=int(env["repair_rounds"]),
        difficulty=float(env["difficulty"]),
        topology=env["topology"],
        trace=tuple(env["trace"]),
    )


def solve_balanced_canvas(query: str, dsl: str | None = None) -> mesh.Outcome:
    """Dispatch the `balanced` Canvas signature, block on its final envelope,
    and adapt to `mesh.Outcome`. Mirrors `cascade.canvas_spike.solve_balanced`
    in shape but covers the FULL balanced topology (route + draft + gate +
    GPU repair loop + cloud), not just the GPU phase.

    Returns the same `mesh.Outcome` dataclass `mesh.solve(query, "balanced",
    ops)` returns, so existing callers (`cascade.orchestrator.run_pipeline`'s
    `outcome.resolved` / `outcome.capped` consumers) swap with no shape
    change.
    """
    t0 = time.perf_counter()
    env = balanced_signature(query, dsl).apply_async().get(timeout=600)
    outcome = _to_outcome(env)
    _record_outcome(query, outcome, time.perf_counter() - t0)
    return outcome


def solve_low_latency_canvas(query: str, dsl: str | None = None) -> mesh.Outcome:
    """Dispatch the `low_latency` Canvas signature (NPU draft raced against the
    GPU generate via a chord) and adapt the callback's envelope to
    `mesh.Outcome` -- same return shape as `solve_balanced_canvas`, so a caller
    swaps topologies by choosing the entry point, not by reshaping output.

    Speculative: both tiers always run (trades GPU cost for latency); resolves
    to the first verified candidate or caps to Tier-3 on a double miss. See
    docs/FINDINGS-canvas-phase2-low-latency.md."""
    t0 = time.perf_counter()
    env = low_latency_signature(query, dsl).apply_async().get(timeout=600)
    outcome = _to_outcome(env)
    _record_outcome(query, outcome, time.perf_counter() - t0)
    return outcome
