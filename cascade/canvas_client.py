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

from cascade import mesh
from cascade.topologies_canvas import balanced_signature


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
    env = balanced_signature(query, dsl).apply_async().get()
    final_tier = env["final_tier"] or "capped->tier3"
    return mesh.Outcome(
        answer=env["answer"],
        final_tier=final_tier,
        resolved=bool(env["resolved"]),
        capped=bool(env["capped"]),
        repair_rounds=int(env["repair_rounds"]),
        difficulty=float(env["difficulty"]),
        topology=env["topology"],
        trace=tuple(env["trace"]),
    )
