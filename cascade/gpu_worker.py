"""Tier 2 — qwen2.5-coder:14b on the NVIDIA GPU via Ollama."""
from __future__ import annotations

import time
from dataclasses import dataclass

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


class GPUWorker:
    def __init__(self) -> None:
        self._url = CONFIG.ollama_base_url.rstrip("/")
        self._model = CONFIG.gpu_model

    def available(self) -> bool:
        try:
            r = httpx.get(f"{self._url}/api/tags", timeout=3.0)
            tags = {m["name"] for m in r.json().get("models", [])}
            return r.status_code == 200 and self._model in tags
        except (httpx.HTTPError, ValueError, KeyError):
            return False

    def generate(self, query: str) -> GPUResult:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": query},
            ],
            "stream": False,
            "options": {"num_predict": CONFIG.gpu_max_new_tokens},
        }
        t0 = time.perf_counter()
        try:
            r = httpx.post(f"{self._url}/api/chat", json=payload, timeout=180.0)
            r.raise_for_status()
        except httpx.HTTPError as e:
            return GPUResult(f"[gpu unavailable: {e}]", 0.0, 0.0, self._model, False)
        dt = time.perf_counter() - t0
        data = r.json()
        text = data.get("message", {}).get("content", "").strip()
        eval_count = data.get("eval_count", 0)
        eval_ns = data.get("eval_duration", 0) or 1
        tok_s = eval_count / (eval_ns / 1e9)
        return GPUResult(text, dt, tok_s, self._model, True)
