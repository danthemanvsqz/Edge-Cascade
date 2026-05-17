"""edge-gpu MCP server -- Tier 2, NVIDIA RTX 5070 Ti via Ollama.

Local reasoning / multi-file logic / repair of a failed Tier-1 draft. Stateless
HTTP to Ollama (localhost:11434). "Unavailable" is a normal status, NOT an
error: if Ollama is down the tools say so cleanly and the router skips Tier 2
and carries any Tier-1 draft up as prior_attempt.

Reality baked in: this is the 12 GB Laptop part (not the 16 GB the design
papers assume). status() surfaces live VRAM so the router escalates
big-context tasks instead of letting them OOM.

Tools:
  status    Ollama reachable? model resident? VRAM headroom?
  generate  qwen2.5-coder:14b completion; optional prior_attempt / max_tokens

Run:  python -m mcp_servers.gpu        (stdio transport)
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from ._rec import Recorder, recorded

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade.config import CONFIG  # noqa: E402
from cascade.gpu_worker import GPUWorker  # noqa: E402

mcp = FastMCP("edge-gpu")
_REC = Recorder("edge-gpu")
_worker = GPUWorker()
_URL = CONFIG.ollama_base_url.rstrip("/")


def _vram() -> dict | None:
    """Best-effort live VRAM of resident models (Ollama /api/ps). None if the
    endpoint is unreachable or shapes unexpectedly -- never raises."""
    try:
        r = httpx.get(f"{_URL}/api/ps", timeout=3.0)
        models = r.json().get("models", [])
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    if not models:
        return {"resident_models": 0, "vram_bytes": 0}
    return {
        "resident_models": len(models),
        "vram_bytes": sum(m.get("size_vram", 0) for m in models),
    }


@mcp.tool()
@recorded(_REC)
def status() -> dict:
    """Tier-2 readiness. `available` gates whether the router uses this tier.

    available=false is expected (Ollama not running / model not pulled) and is
    NOT an error -- the router should skip Tier 2, not fail.
    """
    available = _worker.available()
    return {
        "available": available,
        "model": CONFIG.gpu_model,
        "base_url": _URL,
        "vram": _vram() if available else None,
        # 12 GB part: 14B-Q4 (~9 GB) leaves only ~2-3 GB for KV cache, so the
        # practical local context is far below the papers' 128K. The router
        # should escalate big-context tasks rather than truncate/OOM.
        "context_note": "12GB VRAM: realistic local context ~8-32K, not 128K",
    }


@mcp.tool()
@recorded(_REC)
def generate(
    prompt: str,
    prior_attempt: str | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Generate with the 14B coder model. On a repair, pass the failed
    lower-tier answer as `prior_attempt`. `max_tokens` caps output (defaults
    to config's gpu_max_new_tokens).

    If Ollama is unreachable returns {available:false} -- skip, don't fail.
    """
    if not _worker.available():
        return {
            "available": False, "text": "[gpu tier unavailable -- Ollama not "
            "reachable]", "tokens_per_s": 0.0, "latency_s": 0.0,
            "model": CONFIG.gpu_model,
        }
    query = prompt
    if prior_attempt:
        query = (
            f"{prompt}\n\n--- A lower tier produced this answer, which failed "
            f"verification. Diagnose and correct it: ---\n{prior_attempt}"
        )
    r = _worker.generate(query, max_new_tokens=max_tokens)
    return {
        "available": r.available, "text": r.text,
        "tokens_per_s": round(r.tokens_per_s, 2),
        "latency_s": round(r.latency_s, 2), "model": r.model,
    }


if __name__ == "__main__":
    mcp.run()
