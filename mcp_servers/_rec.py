"""Shared structured recorder for the MCP servers.

Every tool call on every server emits one record in the deterministic
cascade/logfmt.py grammar -- the same length-framed format the orchestrator
uses, so a tool result containing fake timestamps or the record sentinels
can never corrupt the stream.

ONE FILE PER SERVER (runs/<server>.rec). The servers are separate processes;
funnelling them into a single file would interleave partial writes and
corrupt records (the grammar tolerates a truncated tail, not interleaving).
Per-server files keep each stream single-writer and append-only.

`make_recorder(server)` returns an `emit(tool, fields)` closure -- the append
path and the seq counter live in the closure (no object, no manual counter:
the seq is an `itertools.count()` generator). Wrap a tool with the `recorded`
decorator, applied UNDER @mcp.tool() so it wraps the real tool;
functools.wraps keeps the wrapper transparent to FastMCP's signature/type
introspection.
"""
from __future__ import annotations

import functools
import json
import sys
import time
import uuid
from collections.abc import Callable
from itertools import count
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade.logfmt import dump_record  # noqa: E402

Emit = Callable[[str, dict[str, str]], None]

# Experiment evidence lanes get this stream-name prefix so the replay/dashboard
# consumers can SEGREGATE them from live-mesh metrics: an experiment reuses tool
# names like `generate`/`verify_functional`, so without segregation it would
# pollute cascade health/spend. Single source of truth for the prefix.
EXPERIMENT_PREFIX = "experiment-"


def _s(obj: object) -> str:
    """Compact, never-raising JSON; falls back to repr for exotic values."""
    try:
        return json.dumps(obj, default=repr, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(obj)


def make_recorder(server: str) -> Emit:
    """Return an `emit(tool, fields)` that appends one logfmt record per call.

    State -- the append path and the monotonic seq -- lives in the closure,
    not an object. The seq is `itertools.count()`: a lazy generator, so there
    is no counter to read-modify-write.

    `run_id` is created once per server process (the seq resets to 0 on every
    restart, so it alone cannot tie records to a process); `ts` is wall-clock
    seconds, the only thing that orders records *across* the per-server files.
    """
    path = ROOT / "runs" / f"{server}.rec"
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = count()
    run_id = uuid.uuid4().hex[:12]

    def emit(tool: str, fields: dict[str, str]) -> None:
        rec = dump_record(
            next(seq),
            {
                "server": server,
                "tool": tool,
                "ts": f"{time.time():.3f}",
                "run_id": run_id,
                **fields,
            },
        )
        # Single write of the whole record: append-only, single-writer.
        # dump_record is bytes-native (value UTF-8 encoded once) -> append in
        # binary so there is no text-layer re-encode.
        with open(path, "ab") as fh:
            fh.write(rec)

    return emit


def make_experiment_recorder(topic: str) -> Emit:
    """Recorder for an EXPERIMENT lane -> `runs/experiment-<topic>.rec`.

    Identical grammar/record shape to any tier recorder; the only difference is
    the `experiment-` stream prefix (EXPERIMENT_PREFIX), which the consumers use
    to keep experiment runs OUT of live-mesh metrics. Use this for any
    experiment so its telemetry is replayable/queryable like every tier without
    contaminating cascade health."""
    if not topic or " " in topic or "/" in topic or "\\" in topic:
        raise ValueError(f"illegal experiment topic: {topic!r}")
    return make_recorder(f"{EXPERIMENT_PREFIX}{topic}")


def recorded(emit: Emit):
    """Decorator: emit a logfmt record for the wrapped tool call (args,
    result, latency, ok). Errors are recorded then re-raised so FastMCP still
    surfaces them."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 - record then re-raise
                emit(fn.__name__, {
                    "args": _s(kwargs or args),
                    "ok": "false",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": f"{(time.perf_counter() - t0) * 1000:.1f}",
                })
                raise
            emit(fn.__name__, {
                "args": _s(kwargs or args),
                "ok": "true",
                "result": _s(result),
                "latency_ms": f"{(time.perf_counter() - t0) * 1000:.1f}",
            })
            return result

        return wrapper

    return deco
