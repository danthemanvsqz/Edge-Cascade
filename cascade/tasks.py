"""Tier ops as Celery tasks (C1 Phase-0) -- OPT-IN, reuse the existing workers.

Each task body is the SAME worker call the MCP server makes, wrapped by the SAME
`.rec` recorder (mcp_servers/_rec.py) -- so a Celery run writes `runs/<tier>.rec`
byte-identically to the pipe path, and replay.py/dashboard.py read it unchanged.
That parity is the load-bearing proof the substrate is viable (charter inv. 5:
recording stays at the op boundary; the executor underneath is what changed).

Phase-0 wraps two tiers: `generate` (Tier-2 GPU) and `verify_functional` (the
deterministic gate). route/draft (NPU) + Canvas composition come next.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from cascade.celery_app import app
from cascade.config import CONFIG
from cascade.gpu_worker import make_gpu_worker
from mcp_servers._rec import make_recorder, recorded

ROOT = Path(__file__).resolve().parent.parent
_FUNC_TIMEOUT_S = 20

# Resident per worker process: one recorder + worker per tier; `run_id` is stable
# for the process (matches MCP-server semantics), so the warm GPU/NPU state and
# the .rec cohort survive across tasks (worker_max_tasks_per_child=0).
_GPU_REC = make_recorder("edge-gpu")
_VERIFY_REC = make_recorder("edge-verify")
_gpu = make_gpu_worker()


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
