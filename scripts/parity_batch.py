"""Slice-4 live parity batch: run 3 cases through the Canvas chain (and capture
results in JSON). The pipe path (cli.py) is run separately because it builds
its own cascade_session with NPU compile + cloud check; running both side by
side from the same process would double-init.

Each canvas run captures:
  - Outcome shape (final_tier, resolved, capped, repair_rounds, difficulty)
  - Trace
  - Wall time
  - .rec deltas per lane (edge-npu, edge-gpu, edge-verify, edge-cloud)
  - Answer preview (first 200 chars)

Forces os._exit(0) at the end because Celery's amqp/connection-pool threads
hold the process alive past main()'s return; we don't need clean teardown
for a one-shot batch.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

CASES = [
    {
        "label": "A.canvas",
        "query": "reverse a python string",
        "dsl": None,  # syntax fallback path
    },
    {
        "label": "B.canvas",
        "query": "write a python function for dijkstra's shortest path",
        "dsl": None,  # syntax fallback path
    },
    {
        "label": "C.canvas",
        "query": "write a python function add(a, b) -> a + b",
        # An impossible DSL: forces functional gate to FAIL every time -> cap
        "dsl": "when add\n  assert add(1,1)==2\n  assert add(1,1)==3",
    },
]


def _rec_sizes() -> dict[str, int]:
    return {
        f.stem: f.stat().st_size if f.exists() else 0
        for f in [
            Path("runs/edge-npu.rec"),
            Path("runs/edge-gpu.rec"),
            Path("runs/edge-verify.rec"),
            Path("runs/edge-cloud.rec"),
        ]
    }


def main() -> None:
    from cascade.canvas_client import solve_balanced_canvas

    out_path = Path("runs/parity-canvas.json")
    results = []
    for case in CASES:
        pre = _rec_sizes()
        t0 = time.perf_counter()
        outcome = solve_balanced_canvas(case["query"], dsl=case["dsl"])
        wall = time.perf_counter() - t0
        post = _rec_sizes()
        deltas = {k: post[k] - pre[k] for k in pre}
        result = {
            "label": case["label"],
            "query": case["query"],
            "dsl": case["dsl"],
            "wall_s": round(wall, 3),
            "final_tier": outcome.final_tier,
            "resolved": outcome.resolved,
            "capped": outcome.capped,
            "repair_rounds": outcome.repair_rounds,
            "difficulty": round(outcome.difficulty, 3),
            "trace": list(outcome.trace),
            "answer_preview": (outcome.answer or "")[:200],
            "rec_deltas": deltas,
        }
        results.append(result)
        sys.stderr.write(f"[done] {case['label']}: tier={result['final_tier']} "
                         f"resolved={result['resolved']} wall={result['wall_s']}s\n")
        sys.stderr.flush()

    fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        os.write(fd, json.dumps(results, indent=2).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    sys.stderr.write(f"wrote {out_path}\n")
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
