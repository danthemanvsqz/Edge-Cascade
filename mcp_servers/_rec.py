"""Shared structured recorder for the MCP servers.

Every tool call on every server emits one record in the deterministic
cascade/logfmt.py grammar -- the same length-framed format the orchestrator
uses, so a tool result containing fake timestamps or the record sentinels
can never corrupt the stream.

ONE FILE PER SERVER (runs/<server>.rec). The servers are separate processes;
funnelling them into a single file would interleave partial writes and
corrupt records (the grammar tolerates a truncated tail, not interleaving).
Per-server files keep each stream single-writer and append-only.

Use via the `recorded` decorator, applied UNDER @mcp.tool() so it wraps the
real tool. functools.wraps keeps the wrapper transparent to FastMCP's
signature/type-hint introspection.
"""
from __future__ import annotations

import functools
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade.logfmt import dump_record  # noqa: E402


def _s(obj: object) -> str:
    """Compact, never-raising JSON; falls back to repr for exotic values."""
    try:
        return json.dumps(obj, default=repr, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(obj)


class Recorder:
    def __init__(self, server: str) -> None:
        self.server = server
        self.path = ROOT / "runs" / f"{server}.rec"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0

    def emit(self, tool: str, fields: dict[str, str]) -> None:
        rec = dump_record(
            self._seq, {"server": self.server, "tool": tool, **fields}
        )
        # Single write of the whole record: append-only, single-writer.
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(rec)
        self._seq += 1


def recorded(rec: Recorder):
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
                rec.emit(fn.__name__, {
                    "args": _s(kwargs or args),
                    "ok": "false",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": f"{(time.perf_counter() - t0) * 1000:.1f}",
                })
                raise
            rec.emit(fn.__name__, {
                "args": _s(kwargs or args),
                "ok": "true",
                "result": _s(result),
                "latency_ms": f"{(time.perf_counter() - t0) * 1000:.1f}",
            })
            return result

        return wrapper

    return deco
