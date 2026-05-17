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

import atexit
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ._rec import make_recorder, recorded

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade.config import CONFIG  # noqa: E402

mcp = FastMCP("edge-npu")
_REC = make_recorder("edge-npu")

# Generous: covers the iGPU compile (~9s) + safety. The NPU-probe path is
# longer; CASCADE_SKIP_NPU=1 is the recommended Tier-1 setting.
_READY_TIMEOUT = 90
_RPC_TIMEOUT = 60


def make_npu_rpc():
    """Own the isolated OpenVINO worker subprocess behind a bounded line RPC.

    The state -- the process handle and the cached hard-failure -- lives in
    this closure, not an object. The worker is a *process-scoped* resource:
    spawned lazily on the first tool call (a broken Tier-1 must not take the
    server down at boot) and reused by every later call, so its lifetime hook
    is `atexit`, not a `with` block. A `threading.Lock` keeps the RPC
    single-flight; `_dead_reason` is cached so a ~9s failed compile is not
    retried in a loop. Returns the `rpc(op, ...)` callable.
    """
    q: queue.Queue[str | None] = queue.Queue()
    lock = threading.Lock()
    proc: subprocess.Popen | None = None
    dead_reason: str | None = None

    def reader(stdout) -> None:
        try:
            for line in stdout:
                q.put(line)
        finally:
            q.put(None)  # EOF sentinel -> worker exited

    def spawn() -> str | None:
        nonlocal proc
        # Private pipes (NOT the MCP JSON-RPC channel). stderr inherits, so
        # OpenVINO chatter lands on the server's stderr, never on a protocol
        # stream. close_fds (Popen default) keeps it from inheriting handles.
        proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_servers._npu_worker_proc"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", cwd=str(ROOT), env=dict(os.environ),
        )
        threading.Thread(
            target=reader, args=(proc.stdout,), daemon=True
        ).start()
        try:
            line = q.get(timeout=_READY_TIMEOUT)
        except queue.Empty:
            return f"worker did not become ready within {_READY_TIMEOUT}s"
        if line is None:
            return "worker exited before ready"
        ready = json.loads(line)
        if not ready.get("ok"):
            return ready.get("error", "worker failed to construct NPUWorker")
        return None

    def rpc(op: str, timeout: int = _RPC_TIMEOUT, **kw) -> dict:
        nonlocal dead_reason
        with lock:
            if dead_reason is not None:
                return {"ok": False, "error": dead_reason}
            if proc is None:
                err = spawn()
                if err is not None:
                    dead_reason = err
                    return {"ok": False, "error": err}
            try:
                proc.stdin.write(json.dumps({"op": op, **kw}) + "\n")
                proc.stdin.flush()
                line = q.get(timeout=timeout)
            except (queue.Empty, BrokenPipeError, OSError) as e:
                dead_reason = f"{type(e).__name__}: worker unresponsive ({op})"
                return {"ok": False, "error": dead_reason}
            if line is None:
                dead_reason = f"worker exited during {op}"
                return {"ok": False, "error": dead_reason}
            return json.loads(line)

    def shutdown() -> None:
        # Previously leaked: the worker subprocess was never asked to exit.
        # Best-effort graceful shutdown, then hard-kill if it lingers.
        if proc is None:
            return
        try:
            proc.stdin.write('{"op": "shutdown"}\n')
            proc.stdin.flush()
            proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            proc.kill()

    atexit.register(shutdown)
    return rpc


_rpc = make_npu_rpc()


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
    r = _rpc("status", timeout=_READY_TIMEOUT)
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
    r = _rpc("route", prompt=prompt)
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
    r = _rpc("draft", prompt=prompt, max_tokens=max_tokens)
    if not r.get("ok"):
        return {"available": False, "reason": r.get("error")}
    return {"available": True, "text": r["text"],
            "latency_s": round(r["latency_s"], 2), "device": r["device"]}


if __name__ == "__main__":
    mcp.run()
