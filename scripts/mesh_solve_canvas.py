"""Canvas client CLI: dispatch a balanced topology and print the Outcome.

Usage (after `docker compose up -d redis` and a Celery worker running):
    uv run python scripts/mesh_solve_canvas.py "write a python function add(a, b) -> a + b"
    uv run python scripts/mesh_solve_canvas.py --dsl "<dsl-text>" "<query>"

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

from cascade.canvas_client import solve_balanced_canvas


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Dispatch a balanced Canvas signature; print the Outcome.",
    )
    ap.add_argument(
        "--dsl", default=None,
        help="optional checks.dsl text passed to the functional gate; "
             "omit for syntax-only behavior (the gate still runs but with "
             "no functional assertions to enforce).",
    )
    ap.add_argument("query", nargs="+", help="the prompt to solve")
    args = ap.parse_args()

    query = " ".join(args.query)
    t0 = time.perf_counter()
    outcome = solve_balanced_canvas(query, dsl=args.dsl)
    wall = time.perf_counter() - t0

    print(f"\n=== Canvas balanced ({wall:.2f}s) ===")
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


if __name__ == "__main__":
    main()
