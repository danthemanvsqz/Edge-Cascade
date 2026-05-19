"""Phase-1 tuning benchmark -- offline, no model calls, no hardware.

Two measurements, each comparing the pre-Phase-1 behaviour against the new
path so the latency/VRAM claims in the plan are numbers, not assertions:

  1. logfmt parse + write
       old: str read (whole-file decode) -> re-encode -> parse, ALL fields
            decoded; writer = per-record open/encode/close (text mode)
       new: bytes in, parse with keep={query,answer}; writer = one binary
            handle, dump_record bytes written directly
  2. repair-prompt size (the local-VRAM budget)
       full program + all failures   vs   symbol-sliced   vs   sliced+bounded
       reported as estimated tokens against the NPU/GPU repair caps.

Run:  .venv\\Scripts\\python.exe -m bench.bench_phase1
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import validate_log as V
from cascade.config import CONFIG
from cascade.feedback import CheckFailure, build_repair_prompt
from cascade.logfmt import dump_record, parse_stream

# A value that embeds the grammar's own sentinels + fake timestamps: the
# adversarial payload the format exists to survive (and the realistic shape
# of a model answer / hop trace).
_NASTY = (
    "```python\n# %%REC v1 999\n%%END\n12:34:56 ---- QUERY: x\n"
    + "def solve(xs):\n    return sorted(xs)\n" * 12
    + "```\n"
)


def _synth_blob(n: int) -> bytes:
    """n realistic records: small query, large answer + large trace (the two
    big fields, one kept one skipped by the new projection)."""
    return b"".join(
        dump_record(i, {
            "ts": f"{1_700_000_000 + i}.000",
            "run_id": "deadbeefcafe",
            "query": f"task {i}: write a function",
            "answer": _NASTY,
            "final_tier": "gpu",
            "total_latency_s": "12.34",
            "trace": "router|NPU|0.50s|difficulty=0.6\n" * 8,
        })
        for i in range(n)
    )


def _time(label: str, fn, repeat: int) -> float:
    fn()  # warm
    t0 = time.perf_counter()
    for _ in range(repeat):
        fn()
    dt = (time.perf_counter() - t0) / repeat
    print(f"  {label:<46} {dt * 1e3:8.2f} ms")
    return dt


def bench_logfmt(n: int = 3000, repeat: int = 10) -> None:
    blob = _synth_blob(n)
    text = blob.decode("utf-8")
    print(f"\nlogfmt parse  (n={n} records, {len(blob) / 1024:.0f} KiB, "
          f"mean of {repeat})")

    # old: caller read_text (decode) -> parse_stream re-encoded internally ->
    # every field decoded. Emulate the decode+re-encode round trip + keep=None.
    old = _time("old: str round-trip + decode-all",
                lambda: parse_stream(text.encode("utf-8")), repeat)
    new = _time("new: bytes + keep={query,answer}",
                lambda: parse_stream(blob, frozenset({"query", "answer"})),
                repeat)
    print(f"  -> parse speedup: {old / new:4.2f}x")

    print(f"\nlogfmt write  (n={n} records, mean of {repeat})")
    tmp = Path(tempfile.gettempdir()) / "cascade_bench.rec"
    recs_b = [dump_record(i, {"query": f"q{i}", "answer": _NASTY})
              for i in range(n)]

    def write_old() -> None:  # per-record open/encode/close, text mode
        if tmp.exists():
            tmp.unlink()
        for rb in recs_b:
            with open(tmp, "a", encoding="utf-8") as fh:
                fh.write(rb.decode("utf-8"))

    def write_new() -> None:  # one binary handle, bytes written directly
        if tmp.exists():
            tmp.unlink()
        with open(tmp, "ab") as fh:
            for rb in recs_b:
                fh.write(rb)

    wo = _time("old: per-record open/encode/close", write_old, max(1, repeat // 2))
    wn = _time("new: one binary handle, bytes", write_new, max(1, repeat // 2))
    tmp.unlink(missing_ok=True)
    print(f"  -> write speedup: {wo / wn:4.2f}x")


# ---- repair-prompt size ------------------------------------------------------

_PROGRAM = (
    "import heapq\n"
    "def add_numbers(a, b):\n    return a + b\n\n"
    "def merge_sort(xs):\n"
    "    if len(xs) <= 1:\n        return xs\n"
    "    m = len(xs) // 2\n"
    "    return _merge(merge_sort(xs[:m]), merge_sort(xs[m:]))\n\n"
    "def _merge(a, b):\n"
    "    out = []\n    # ... 40 lines of merge logic ...\n"
    + "    out.append(0)\n" * 40 +
    "    return out\n\n"
    "def dijkstra(graph, start):\n"
    "    dist = {start: 0}\n"
    "    # buggy: KeyErrors on sink nodes, wrong relaxation\n"
    + "    dist[start] = dist.get(start, 0) + 1\n" * 30 +
    "    return dist\n\n"
    "print(dijkstra({'A': {'B': 1}}, 'A'))\n"
)


def _est_tokens(s: str) -> int:
    return len(s) // 4  # rough; no tokenizer dependency / model call


def bench_repair_context() -> None:
    fails = [
        V.Check("dijkstra", "drone_ok(dijkstra)", False,
                "KeyError: 'E' (in dijkstra(), line 14)" + " ctx" * 80,
                "must compute shortest path; init every node"),
    ] + [V.Check("dijkstra", f"dijkstra(g,'A')[{k}]==1", False, "KeyError")
         for k in range(12)]
    task = "Implement Dijkstra on a directed weighted graph."

    full = build_repair_prompt(
        task, _PROGRAM,
        [CheckFailure(c.expr, c.observed, c.requirement) for c in fails])
    sliced = build_repair_prompt(task, V._slice_for_repair(_PROGRAM, fails),
                                 [CheckFailure(c.expr, c.observed, c.requirement)
                                  for c in fails])
    bf, note = V._bounded_failures(fails)
    sb = build_repair_prompt(task, V._slice_for_repair(_PROGRAM, fails),
                             bf, note=note)

    npu = CONFIG.npu_repair_max_tokens
    gpu = CONFIG.gpu_max_new_tokens
    print(f"\nrepair-prompt size  (NPU cap={npu}, GPU cap={gpu} tokens)")
    for label, p in (("full program + all failures", full),
                     ("symbol-sliced", sliced),
                     ("sliced + bounded failures", sb)):
        t = _est_tokens(p)
        fits = "ok" if t <= npu else ("GPU-only" if t <= gpu else "OVER both")
        print(f"  {label:<32} ~{t:5d} tok  [{fits}]")
    print(f"  note: {note}")


def main() -> None:
    print("=" * 60)
    print("edge-cascade Phase-1 benchmark (offline)")
    print("=" * 60)
    bench_logfmt()
    bench_repair_context()
    print("\ndone.")


if __name__ == "__main__":
    main()
