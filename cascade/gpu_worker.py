"""Tier 2 — qwen2.5-coder:14b on the NVIDIA GPU. Two backends behind one
selector:

- `ollama` (default): HTTP to a long-running Ollama daemon. Stateless
  client; the model lives in another process. The path this module owned
  in Phase 1.
- `llama_cpp` (Phase 2 Slice 1, opt-in via CASCADE_GPU_BACKEND=llama_cpp):
  direct llama-cpp-python loading the same GGUF weights Ollama caches.
  See cascade/llama_worker.py.

`make_gpu_worker()` branches on `CONFIG.gpu_backend` and returns the
appropriate `GPUWorker`. Same immutable dataclass shape either way, so
`cascade.tasks` and the Canvas chain see no diff.

The Ollama path is still the default until the Slice-2 parity findings
prove the direct path matches (per docs/DESIGN-celery-phase2.md).
"""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import httpx

from .config import CONFIG

_SYSTEM = (
    "You are an expert coding assistant. Produce correct, complete, runnable "
    "code as a single fenced code block. Before answering, sanity-check the "
    "common failure modes: derive the full set of entities from ALL references "
    "(e.g. a graph node that appears only as a neighbour still needs its own "
    "entry) and initialise state for every one before use; handle empty input, "
    "boundaries, and absent keys without raising."
)


@dataclass
class GPUResult:
    text: str
    latency_s: float
    tokens_per_s: float
    model: str
    seed: int = 0
    available: bool = True


def _available(url: str, model: str) -> bool:
    try:
        r = httpx.get(f"{url}/api/tags", timeout=3.0)
        tags = {m["name"] for m in r.json().get("models", [])}
        return r.status_code == 200 and model in tags
    except (httpx.HTTPError, ValueError, KeyError):
        return False


def _generate(
    url: str, model: str, query: str, max_new_tokens: int | None = None
) -> GPUResult:
    seed = random.randint(0, 2**31 - 1)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": query},
        ],
        "stream": False,
        "options": {
            "num_predict": max_new_tokens or CONFIG.gpu_max_new_tokens,
            "temperature": CONFIG.gpu_temperature,
            "top_p": CONFIG.gpu_top_p,
            "seed": seed,
        },
    }
    t0 = time.perf_counter()
    try:
        r = httpx.post(f"{url}/api/chat", json=payload, timeout=180.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        return GPUResult(f"[gpu unavailable: {e}]", 0.0, 0.0, model, seed=0, available=False)
    dt = time.perf_counter() - t0
    data = r.json()
    text = data.get("message", {}).get("content", "").strip()
    eval_count = data.get("eval_count", 0)
    eval_ns = data.get("eval_duration", 0) or 1
    tok_s = eval_count / (eval_ns / 1e9)
    return GPUResult(text, dt, tok_s, model, seed=seed, available=True)


@dataclass(frozen=True)
class GPUWorker:
    """Immutable Tier-2 handle: the model id + bound `available`/`generate`
    closures. Pure data -- no behavior on the object."""

    model: str
    available: Callable[[], bool]
    generate: Callable[..., GPUResult]


def make_gpu_worker() -> GPUWorker:
    """Return the GPU worker for the configured backend. `ollama` => HTTP
    client to the Ollama daemon (this module). `llama_cpp` => direct GGUF
    loading via cascade.llama_worker. The two backends are duck-typed
    equivalent: both return a GPUWorker exposing `model`, `available`,
    `generate`.

    The dispatch happens HERE (one branch, one place) so every caller
    stays unchanged."""
    if CONFIG.gpu_backend == "llama_cpp":
        from cascade.llama_worker import make_llama_worker
        return make_llama_worker()
    if CONFIG.gpu_backend != "ollama":
        raise ValueError(
            f"unknown CASCADE_GPU_BACKEND={CONFIG.gpu_backend!r}; "
            f"expected `ollama` or `llama_cpp`"
        )
    url = CONFIG.ollama_base_url.rstrip("/")
    model = CONFIG.gpu_model
    return GPUWorker(
        model=model,
        available=partial(_available, url, model),
        generate=partial(_generate, url, model),
    )
