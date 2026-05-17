"""Tier 2 — qwen2.5-coder:14b on the NVIDIA GPU via Ollama.

No worker object: set-once config (Ollama URL + model) and two stateless
HTTP calls. `make_gpu_worker()` returns an immutable GPUWorker value object
binding `available` / `generate` closures.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import httpx

from .config import CONFIG

_SYSTEM = (
    "You are an expert coding assistant. Produce correct, complete, runnable "
    "code. Prefer a single fenced code block."
)


@dataclass
class GPUResult:
    text: str
    latency_s: float
    tokens_per_s: float
    model: str
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
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": query},
        ],
        "stream": False,
        "options": {"num_predict": max_new_tokens or CONFIG.gpu_max_new_tokens},
    }
    t0 = time.perf_counter()
    try:
        r = httpx.post(f"{url}/api/chat", json=payload, timeout=180.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        return GPUResult(f"[gpu unavailable: {e}]", 0.0, 0.0, model, False)
    dt = time.perf_counter() - t0
    data = r.json()
    text = data.get("message", {}).get("content", "").strip()
    eval_count = data.get("eval_count", 0)
    eval_ns = data.get("eval_duration", 0) or 1
    tok_s = eval_count / (eval_ns / 1e9)
    return GPUResult(text, dt, tok_s, model, True)


@dataclass(frozen=True)
class GPUWorker:
    """Immutable Tier-2 handle: the model id + bound `available`/`generate`
    closures. Pure data -- no behavior on the object."""

    model: str
    available: Callable[[], bool]
    generate: Callable[..., GPUResult]


def make_gpu_worker() -> GPUWorker:
    url = CONFIG.ollama_base_url.rstrip("/")
    model = CONFIG.gpu_model
    return GPUWorker(
        model=model,
        available=partial(_available, url, model),
        generate=partial(_generate, url, model),
    )
