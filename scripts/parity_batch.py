"""Live parity batch -- runs 3 cases through the Canvas chain and captures
results in JSON. Originally landed for Slice 4 (Phase 1 closure) to verify
the pipe path vs Canvas; extended for Slice 2 of Phase 2 to also pivot on
the GPU backend (Ollama vs llama-cpp-python).

  uv run python scripts/parity_batch.py                       # canvas, default backend
  uv run python scripts/parity_batch.py --backend ollama      # canvas, Ollama explicit
  uv run python scripts/parity_batch.py --backend llama_cpp   # canvas, llama-cpp direct

Each canvas run captures:
  - Outcome shape (final_tier, resolved, capped, repair_rounds, difficulty)
  - Trace
  - Wall time
  - .rec deltas per lane (edge-npu, edge-gpu, edge-verify, edge-cloud)
  - Answer preview (first 200 chars)

Forces os._exit(0) at the end because Celery's amqp/connection-pool threads
hold the process alive past main()'s return; we don't need clean teardown
for a one-shot batch.

The `--backend` flag sets `CASCADE_GPU_BACKEND` BEFORE importing the cascade
modules so `Config()` picks it up at construction. The worker side must be
launched with the same backend env var (workers compile their model at boot;
the client-side flag only governs which backend identifier ends up in the
output JSON's metadata).
"""
from __future__ import annotations

import argparse
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
    ap = argparse.ArgumentParser(
        description="Dispatch the 3-case balanced-topology parity batch; "
                    "write the JSON-encoded results to runs/parity-canvas[-<backend>].json.",
    )
    ap.add_argument(
        "--backend", choices=("ollama", "llama_cpp"), default=None,
        help="GPU backend identifier. Sets CASCADE_GPU_BACKEND for THIS process "
             "(metadata only; the worker must be launched with the same flag). "
             "Omit to use whatever the worker has configured.",
    )
    args = ap.parse_args()

    if args.backend is not None:
        # MUST be set before importing cascade -- Config() reads env at init.
        os.environ["CASCADE_GPU_BACKEND"] = args.backend

    from cascade.canvas_client import solve_balanced_canvas
    from cascade.config import CONFIG

    suffix = f"-{args.backend}" if args.backend else ""
    out_path = Path(f"runs/parity-canvas{suffix}.json")
    results = []
    metadata = {
        "backend": args.backend or CONFIG.gpu_backend,
        "gpu_model": CONFIG.gpu_model,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
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

    payload = {"metadata": metadata, "results": results}
    fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        os.write(fd, json.dumps(payload, indent=2).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    sys.stderr.write(f"wrote {out_path}\n")
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
