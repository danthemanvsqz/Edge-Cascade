"""edge-npu MCP server -- Tier 1, Intel NPU "AI Boost" / Xe iGPU / CPU.

OpenVINO GenAI, qwen2.5-coder-1.5B channel-wise symmetric INT4. Two roles:
the cheap up-front difficulty `route`, and a fast `draft` for trivial code.

Hardware concerns hidden from the agent (per the skill's "never re-route a
crash" rule):
  * The vpux compiler can HARD-ABORT the process (exit 127, uncatchable).
    NPUWorker already probes it in a throwaway subprocess and silently falls
    back NPU -> iGPU -> CPU; the agent only ever sees a working device string.
  * If OpenVINO/`accel` isn't installed, or every device rejects the model,
    the tools report {available:false} -- they never crash the server.

Construction (the device compile, ~20s) is LAZY: the first tool call pays it,
not import -- so the test suite / smoke import never needs the heavy `accel`
extra and a broken Tier-1 can't take the server down at boot.

Tools:
  status  compiled device + static-shape token caps (Tier-1 input ceiling)
  route   difficulty score + category, the cascade's routing signal
  draft   fast cheap completion for trivial tasks

Run:  python -m mcp_servers.npu        (stdio transport)
"""
from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ._rec import Recorder, recorded

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade.config import CONFIG  # noqa: E402

mcp = FastMCP("edge-npu")
_REC = Recorder("edge-npu")

_worker = None          # cached NPUWorker once compiled
_unavailable: str | None = None  # cached failure reason (don't retry a ~20s compile)


def _get_worker():
    """Lazily compile Tier-1. Returns (worker, None) or (None, reason).

    A failed compile is cached: re-probing a missing device or a model the
    vpux compiler rejects costs ~20s and will fail identically.
    """
    global _worker, _unavailable
    if _worker is not None:
        return _worker, None
    if _unavailable is not None:
        return None, _unavailable
    try:
        from cascade.npu_worker import NPUWorker

        _worker = NPUWorker()  # _compile(): NPU subprocess probe -> iGPU -> CPU
        return _worker, None
    except Exception as e:  # noqa: BLE001 - any failure => tier unavailable
        _unavailable = f"{type(e).__name__}: {e}"
        return None, _unavailable


def _caps() -> dict:
    return {
        "npu_max_tokens": CONFIG.npu_max_new_tokens,
        "npu_repair_max_tokens": CONFIG.npu_repair_max_tokens,
    }


@mcp.tool()
@recorded(_REC)
def status() -> dict:
    """Tier-1 readiness. First call triggers the ~20s device compile.

    `device` is the processor that actually loaded the model (NPU | GPU.0 |
    CPU). Token caps are the static-shape input ceiling: the router must not
    hand Tier-1 a task larger than these.
    """
    w, reason = _get_worker()
    if w is None:
        return {"available": False, "device": None, "reason": reason, **_caps()}
    return {"available": True, "device": w.device, "reason": None, **_caps()}


@mcp.tool()
@recorded(_REC)
def route(prompt: str) -> dict:
    """Score difficulty (0..1) + category {trivial|standard|hard}.

    This is the cascade's routing signal. Unavailable => the router should
    fall back to its default banding rather than fail.
    """
    w, reason = _get_worker()
    if w is None:
        return {"available": False, "reason": reason}
    r = w.route(prompt)
    return {
        "available": True, "difficulty": round(r.difficulty, 3),
        "category": r.category, "latency_s": round(r.latency_s, 2),
        "device": r.device,
    }


@mcp.tool()
@recorded(_REC)
def draft(prompt: str, max_tokens: int | None = None) -> dict:
    """Fast Tier-1 completion for trivial tasks. `max_tokens` defaults to
    config's npu_max_new_tokens (the static-shape cap). Gate the result with
    edge-verify before trusting it."""
    w, reason = _get_worker()
    if w is None:
        return {"available": False, "reason": reason}
    d = w.draft(prompt, max_new_tokens=max_tokens)
    return {
        "available": True, "text": d.text,
        "latency_s": round(d.latency_s, 2), "device": d.device,
    }


if __name__ == "__main__":
    mcp.run()
