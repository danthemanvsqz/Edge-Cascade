"""edge-npu MCP server -- Tier 1, Intel NPU "AI Boost" / Xe iGPU / CPU.

OpenVINO GenAI, qwen2.5-coder-1.5B channel-wise symmetric INT4. Two roles:
the cheap up-front difficulty `route`, and a fast `draft` for trivial code.

SUBPROCESS-ISOLATED (measured root cause): openvino_genai.LLMPipeline compile
returns in ~3.5s in a normal process and even with the bare MCP stdio
transport up, but NEVER returns inside the full FastMCP request-dispatch path,
on any thread (process-global). So OpenVINO is NOT loaded in this process;
it runs in mcp_servers._npu_worker_proc over a PRIVATE pipe. This server is a
thin, bounded RPC client -- it never imports OpenVINO.

The worker (the ~9s compile) is spawned lazily on first use, in the worker
process; a spawn/compile failure is cached so we don't respawn a ~9s loop.
Tool contract is unchanged: {available, ...} or {available:false, reason}.

Tools:
  status  compiled device + static-shape token caps (Tier-1 input ceiling)
  route   difficulty score + category, the cascade's routing signal
  draft   fast cheap completion for trivial tasks

Run:  python -m mcp_servers.npu        (stdio transport)
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ._rec import Recorder, recorded

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade.config import CONFIG  # noqa: E402

mcp = FastMCP("edge-npu")
_REC = Recorder("edge-npu")

# Generous: covers the iGPU compile (~9s) + safety. The NPU-probe path is
# longer; CASCADE_SKIP_NPU=1 is the recommended Tier-1 setting.
_READY_TIMEOUT = 90
_RPC_TIMEOUT = 60


class _WorkerClient:
    """Owns the isolated OpenVINO worker subprocess + a bounded line RPC."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._q: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._dead_reason: str | None = None  # cached hard failure

    def _reader(self, stdout) -> None:
        try:
            for line in stdout:
                self._q.put(line)
        finally:
            self._q.put(None)  # EOF sentinel -> worker exited

    def _spawn(self) -> str | None:
        # Private pipes (NOT the MCP JSON-RPC channel). stderr inherits, so
        # OpenVINO chatter lands on the server's stderr, never on a protocol
        # stream. close_fds (Popen default) keeps it from inheriting handles.
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_servers._npu_worker_proc"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", cwd=str(ROOT), env=dict(os.environ),
        )
        threading.Thread(
            target=self._reader, args=(self._proc.stdout,), daemon=True
        ).start()
        try:
            line = self._q.get(timeout=_READY_TIMEOUT)
        except queue.Empty:
            return f"worker did not become ready within {_READY_TIMEOUT}s"
        if line is None:
            return "worker exited before ready"
        ready = json.loads(line)
        if not ready.get("ok"):
            return ready.get("error", "worker failed to construct NPUWorker")
        return None

    def rpc(self, op: str, timeout: int = _RPC_TIMEOUT, **kw) -> dict:
        with self._lock:
            if self._dead_reason is not None:
                return {"ok": False, "error": self._dead_reason}
            if self._proc is None:
                err = self._spawn()
                if err is not None:
                    self._dead_reason = err
                    return {"ok": False, "error": err}
            try:
                self._proc.stdin.write(json.dumps({"op": op, **kw}) + "\n")
                self._proc.stdin.flush()
                line = self._q.get(timeout=timeout)
            except (queue.Empty, BrokenPipeError, OSError) as e:
                self._dead_reason = (
                    f"{type(e).__name__}: worker unresponsive ({op})"
                )
                return {"ok": False, "error": self._dead_reason}
            if line is None:
                self._dead_reason = f"worker exited during {op}"
                return {"ok": False, "error": self._dead_reason}
            return json.loads(line)


_CLIENT = _WorkerClient()


def _caps() -> dict:
    return {
        "npu_max_tokens": CONFIG.npu_max_new_tokens,
        "npu_repair_max_tokens": CONFIG.npu_repair_max_tokens,
    }


@mcp.tool()
@recorded(_REC)
def status() -> dict:
    """Tier-1 readiness. First call spawns the worker (the ~9s compile runs
    in the worker process). `device` is the processor that loaded the model
    (NPU | GPU.0 | CPU). Token caps are the static-shape input ceiling."""
    r = _CLIENT.rpc("status", timeout=_READY_TIMEOUT)
    if not r.get("ok"):
        return {"available": False, "device": None,
                "reason": r.get("error"), **_caps()}
    return {"available": True, "device": r["device"], "reason": None,
            **_caps()}


@mcp.tool()
@recorded(_REC)
def route(prompt: str) -> dict:
    """Score difficulty (0..1) + category {trivial|standard|hard}. Unavailable
    => the router should fall back to its default banding, not fail."""
    r = _CLIENT.rpc("route", prompt=prompt)
    if not r.get("ok"):
        return {"available": False, "reason": r.get("error")}
    return {"available": True, "difficulty": round(r["difficulty"], 3),
            "category": r["category"], "latency_s": round(r["latency_s"], 2),
            "device": r["device"]}


@mcp.tool()
@recorded(_REC)
def draft(prompt: str, max_tokens: int | None = None) -> dict:
    """Fast Tier-1 completion for trivial tasks. `max_tokens` defaults to
    config's npu_max_new_tokens. Gate the result with edge-verify before
    trusting it."""
    r = _CLIENT.rpc("draft", prompt=prompt, max_tokens=max_tokens)
    if not r.get("ok"):
        return {"available": False, "reason": r.get("error")}
    return {"available": True, "text": r["text"],
            "latency_s": round(r["latency_s"], 2), "device": r["device"]}


if __name__ == "__main__":
    mcp.run()
