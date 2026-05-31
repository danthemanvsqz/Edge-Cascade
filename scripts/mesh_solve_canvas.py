"""Canvas client CLI: dispatch a topology and print the Outcome.

Usage (after `docker compose up -d redis` and a Celery worker running):
    uv run python scripts/mesh_solve_canvas.py "write a python function add(a, b) -> a + b"
    uv run python scripts/mesh_solve_canvas.py --dsl "<dsl-text>" "<query>"
    uv run python scripts/mesh_solve_canvas.py --topology low_latency "<query>"

The --topology flag picks budget (sequential cascade) or low_latency (the
Slice-6b chord racing NPU draft vs GPU generate). Time both on the same prompt
to fill docs/FINDINGS-canvas-phase2-low-latency.md's wall-time table.

Worker launch (separate shell -- no `cloud` queue by default, so cloud spend
is structurally impossible without an explicit `-Q cloud` opt-in):
    uv run python -m celery -A cascade.celery_app worker -Q npu,gpu,verify \\
        --pool=solo -l info

This is the OPT-IN counterpart to `cli.py` (the in-process pipe path). Same
shape on the output side -- `mesh.Outcome` is the canonical return type --
so the parity proof in docs/FINDINGS-canvas-phase1.md can compare the two
runs record-for-record.

Live-validated, not unit-cov'd (charter: the Celery substrate's hot path
needs a real broker + hardware workers to exercise meaningfully).
"""
from __future__ import annotations

import argparse
import time

from cascade.canvas_client import (
    solve_budget_canvas,
    solve_budget_fanout,
    solve_low_latency_canvas,
)

_TOPOLOGIES = {
    "budget": solve_budget_canvas,
    "low_latency": solve_low_latency_canvas,
}


def _print_outcome(outcome, topology: str, wall: float) -> None:
    wall_str = f" ({wall:.2f}s)" if wall > 0 else ""
    print(f"\n=== Canvas {topology}{wall_str} ===")
    print(f"topology    : {outcome.topology}")
    print(f"final_tier  : {outcome.final_tier}")
    print(f"resolved    : {outcome.resolved}")
    print(f"capped      : {outcome.capped}")
    print(f"difficulty  : {outcome.difficulty:.2f}")
    print(f"repair_rnds : {outcome.repair_rounds}")
    print("trace       :")
    for line in outcome.trace:
        print(f"  - {line}")
    print()
    if outcome.resolved:
        print("=== answer ===")
        print(outcome.answer)
    else:
        print("=== locals exhausted (capped -> Tier 3 takes over) ===")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Dispatch a Canvas signature; print the Outcome.",
    )
    ap.add_argument(
        "--topology",
        choices=sorted([*_TOPOLOGIES, "budget_fanout"]),
        default="budget",
        help="which Canvas topology to dispatch (default: budget). "
             "budget_fanout accepts multiple query args as parallel sub-tasks.",
    )
    ap.add_argument(
        "--dsl", default=None,
        help="optional checks.dsl text passed to the functional gate; "
             "omit for syntax-only behavior (the gate still runs but with "
             "no functional assertions to enforce).",
    )
    ap.add_argument(
        "query", nargs="+",
        help="the prompt(s) to solve; budget_fanout treats each arg as a "
             "separate sub-task dispatched in parallel.",
    )
    args = ap.parse_args()

    t0 = time.perf_counter()
    if args.topology == "budget_fanout":
        outcomes = solve_budget_fanout(args.query, dsl=args.dsl)
        total_wall = time.perf_counter() - t0
        print(f"\n=== budget_fanout: {len(outcomes)} sub-tasks ({total_wall:.2f}s total) ===")
        for i, (sub_task, outcome) in enumerate(zip(args.query, outcomes, strict=True)):
            print(f"\n[sub-task {i}: {sub_task[:60]}]")
            _print_outcome(outcome, "budget_fanout", wall=0.0)
    else:
        query = " ".join(args.query)
        solve = _TOPOLOGIES[args.topology]
        outcome = solve(query, dsl=args.dsl)
        wall = time.perf_counter() - t0
        _print_outcome(outcome, args.topology, wall)


if __name__ == "__main__":
    main()
