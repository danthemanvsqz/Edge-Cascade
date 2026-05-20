"""Phase-2a tuning benchmark -- offline, no model calls, no hardware.

Measures the cost claim of incremental tail-parse:

  full re-parse   : every "tick", parse the whole `.rec` from byte 0
                    (today's load_streams + parse_stream behaviour).
  incremental     : per-tick, read only from the last safe offset; parse only
                    the new tail (parse_stream_incremental).

For an append-only single-writer stream, incremental cost should be ~constant
per appended record regardless of how much history precedes it. Full re-parse
cost grows linearly with total history -- so as a long-running session
accumulates records, the dashboard's per-tick cost diverges.

Run:  .venv\\Scripts\\python.exe -m bench.bench_incremental
"""
from __future__ import annotations

import time

from cascade.logfmt import dump_record, parse_stream, parse_stream_incremental

# A realistically-sized record (small query, larger answer + trace).
_PAYLOAD = (
    "```python\n"
    + "def f(x):\n    return sorted(x)\n" * 20
    + "```\n"
)


def _make_record(i: int) -> bytes:
    return dump_record(i, {
        "ts": f"{1_700_000_000 + i}.0",
        "run_id": "abc123def456",
        "query": f"task {i}",
        "answer": _PAYLOAD,
        "final_tier": "gpu",
        "total_latency_s": "12.34",
        "trace": "router|NPU|0.5s|ok\ngpu|NVIDIA/qwen|0.7s|ok",
    })


def _time(label: str, fn, repeat: int = 5) -> float:
    fn()  # warm
    t0 = time.perf_counter()
    for _ in range(repeat):
        fn()
    dt = (time.perf_counter() - t0) / repeat
    print(f"  {label:<48} {dt * 1e3:8.2f} ms")
    return dt


def bench_growing_log(history: int = 5000, ticks: int = 50) -> None:
    """Simulate `ticks` dashboard refreshes against a `.rec` of `history`
    pre-existing records plus one new record per tick. Compare:
      full       : parse_stream(whole_buffer) per tick.
      incremental: parse_stream_incremental(whole_buffer, last_offset) per tick.
    """
    print(f"\nGrowing log:  history={history} records,  ticks={ticks} "
          f"(one new record per tick)")
    # Pre-build the byte buffer for the full state at every tick (history + k).
    chunks = [_make_record(i) for i in range(history + ticks)]
    base = b"".join(chunks[:history])
    print(f"  base size: {len(base) / 1024:.0f} KiB  "
          f"(grows by ~{len(chunks[history]) / 1024:.1f} KiB per tick)")

    def full_path() -> None:
        # The shape today's dashboard.snapshot() runs: parse the whole file
        # every tick, regardless of how much is actually new.
        buf = base
        for k in range(ticks):
            buf += chunks[history + k]
            parse_stream(buf)

    def incremental_path() -> None:
        # The proposed shape: keep an offset, parse only new bytes per tick.
        buf = base
        # First call: parse the base once to establish offset.
        _, off = parse_stream_incremental(buf)
        for k in range(ticks):
            buf += chunks[history + k]
            new_slice = buf[off:]
            recs, buf_off = parse_stream_incremental(new_slice)
            off += buf_off
            del recs  # cost not the records' fate

    full = _time(f"full re-parse  ({ticks} ticks, parse all every time)",
                 full_path, repeat=3)
    inc = _time(f"incremental    ({ticks} ticks, parse new tail only)",
                incremental_path, repeat=3)
    if inc > 0:
        print(f"  -> speedup: {full / inc:5.1f}x")
    return


def bench_one_shot(n: int = 5000, repeat: int = 5) -> None:
    """Single-call equivalence: parse_stream and parse_stream_incremental on
    the same input MUST yield the same records and incur ~the same cost
    (the wrapper is just a discard-the-offset call)."""
    blob = b"".join(_make_record(i) for i in range(n))
    print(f"\nSingle-call equivalence:  n={n} records, "
          f"{len(blob) / 1024:.0f} KiB,  mean of {repeat}")
    _time("parse_stream(blob)",
          lambda: parse_stream(blob), repeat)
    _time("parse_stream_incremental(blob)[0]",
          lambda: parse_stream_incremental(blob)[0], repeat)


def main() -> None:
    print("=" * 60)
    print("edge-cascade P2a benchmark (offline)")
    print("=" * 60)
    bench_one_shot()
    bench_growing_log()
    print("\ndone.")


if __name__ == "__main__":
    main()
