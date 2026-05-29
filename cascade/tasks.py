"""Tier ops as Celery tasks (C1 Phase-0 + Phase-1) -- OPT-IN, reuse workers.

Each task body is the SAME worker call the MCP server makes, wrapped by the SAME
`.rec` recorder (mcp_servers/_rec.py) -- so a Celery run writes `runs/<tier>.rec`
in the pipe path's grammar (server/tool/result fields match), and
replay.py/dashboard.py read it unchanged. That parity is the load-bearing proof
the substrate is viable (charter inv. 5: recording stays at the op boundary;
the executor underneath is what changed).

Phase-0 wrapped two tiers: `generate` (Tier-2 GPU) and `verify_functional` (the
deterministic gate). Phase-1 Slice 1 adds Tier-1 NPU `route` + `draft`, lifted
so a `balanced` Canvas chain can dispatch them on the `npu` queue (hardware
pinning when workers split across boxes). `cloud_generate` + Canvas composition
come next.
"""
from __future__ import annotations

import json
import subprocess
import sys
from functools import cache
from pathlib import Path

from cascade.celery_app import app
from cascade.config import CONFIG
from cascade.gpu_worker import make_gpu_worker
from cascade.npu_worker import NPUWorker, make_npu_worker
from mcp_servers._rec import make_recorder, recorded

ROOT = Path(__file__).resolve().parent.parent
_FUNC_TIMEOUT_S = 20

# Resident per worker process: one recorder + worker per tier; `run_id` is stable
# for the process (matches MCP-server semantics), so the warm GPU/NPU state and
# the .rec cohort survive across tasks (worker_max_tasks_per_child=0).
_GPU_REC = make_recorder("edge-gpu")
_VERIFY_REC = make_recorder("edge-verify")
_NPU_REC = make_recorder("edge-npu")
_gpu = make_gpu_worker()


@cache
def _get_npu() -> tuple[NPUWorker | None, str | None]:
    """Compile the Tier-1 NPU worker once per process, lazily.

    The OpenVINO compile is ~9-21s and can fail (no `accel` extra, hardware
    missing); doing it at module import would block every test collection and
    every Celery worker boot regardless of whether route/draft is ever called.
    Lazy + @cache matches the in-process pattern (orchestrator builds the
    worker inside `cascade_session`, not at import), and pins the result for
    the process so the warm pipeline survives the way
    `worker_max_tasks_per_child=0` requires.

    Returns `(worker, None)` on success, `(None, error_msg)` on compile failure
    -- the route/draft callers translate the second into the standard
    `{available: false, reason}` hand-off, never raising upward.
    """
    try:
        return make_npu_worker(), None
    except Exception as e:  # noqa: BLE001 - hand-off as available:false
        return None, f"{type(e).__name__}: {e}"


@recorded(_GPU_REC)
def generate(prompt: str, prior_attempt: str | None = None,
             max_tokens: int | None = None) -> dict:
    """Tier-2 generate (mirrors mcp_servers/gpu.py) -> records edge-gpu.rec.
    `available:false` is a clean status, not an error -- the cascade hands off."""
    if not _gpu.available():
        return {"available": False, "model": CONFIG.gpu_model,
                "text": "[gpu tier unavailable -- Ollama not reachable]",
                "tokens_per_s": 0.0, "latency_s": 0.0}
    query = prompt
    if prior_attempt:
        query = (f"{prompt}\n\n--- A lower tier produced this answer, which failed "
                 f"verification. Diagnose and correct it: ---\n{prior_attempt}")
    r = _gpu.generate(query, max_new_tokens=max_tokens)
    return {"available": r.available, "text": r.text, "model": r.model,
            "tokens_per_s": round(r.tokens_per_s, 2),
            "latency_s": round(r.latency_s, 2)}


@recorded(_VERIFY_REC)
def verify_functional(text: str, dsl: str | None = None) -> dict:
    """Functional gate (mirrors mcp_servers/verify.py) -> records edge-verify.rec.
    The untrusted exec is isolated to a killed subprocess, never the worker."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "mcp_servers._funcverify_child"],
            input=json.dumps({"text": text, "dsl": dsl}),
            capture_output=True, text=True, cwd=str(ROOT),
            timeout=_FUNC_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"ran": True, "applicable": True, "passed": False, "checked": 0,
                "failures": [{"symbol": "<sandbox>", "expr": "completes",
                              "observed": f"timed out after {_FUNC_TIMEOUT_S}s",
                              "requirement": "candidate must terminate"}]}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"ran": False, "applicable": False, "passed": False, "checked": 0,
                "failures": [{"symbol": "<sandbox>", "expr": "exits cleanly",
                              "observed": (proc.stderr or "no output").strip()[:500],
                              "requirement": "sandbox must run"}]}
    return json.loads(proc.stdout)


@app.task(name="mesh.generate", queue="gpu")
def generate_task(prompt: str, prior_attempt: str | None = None,
                  max_tokens: int | None = None) -> dict:
    return generate(prompt, prior_attempt, max_tokens)


@app.task(name="mesh.verify_functional", queue="verify")
def verify_functional_task(text: str, dsl: str | None = None) -> dict:
    return verify_functional(text, dsl)


@recorded(_NPU_REC)
def route(prompt: str) -> dict:
    """Tier-1 route (mirrors mcp_servers/npu.py:route) -> records edge-npu.rec.
    Returns difficulty score + category as the cascade's routing signal; an
    unavailable NPU is a clean `{available:false, reason}` hand-off, not an
    error."""
    npu, err = _get_npu()
    if npu is None:
        return {"available": False, "reason": err}
    r = npu.route(prompt)
    return {"available": True, "difficulty": round(r.difficulty, 3),
            "category": r.category, "latency_s": round(r.latency_s, 2),
            "device": r.device}


@recorded(_NPU_REC)
def draft(prompt: str, max_tokens: int | None = None) -> dict:
    """Tier-1 draft (mirrors mcp_servers/npu.py:draft) -> records edge-npu.rec.
    Fast cheap completion for trivial tasks; gate the result with the verifier
    before chaining it forward."""
    npu, err = _get_npu()
    if npu is None:
        return {"available": False, "reason": err}
    r = npu.draft(prompt, max_new_tokens=max_tokens)
    return {"available": True, "text": r.text,
            "latency_s": round(r.latency_s, 2), "device": r.device}


@app.task(name="mesh.route", queue="npu")
def route_task(prompt: str) -> dict:
    return route(prompt)


@app.task(name="mesh.draft", queue="npu")
def draft_task(prompt: str, max_tokens: int | None = None) -> dict:
    return draft(prompt, max_tokens)
