"""Tier 1 — small coder model on the Intel NPU via OpenVINO GenAI.

Two roles in the cascade:
  route(query)  -> difficulty score + category, so the orchestrator can pick a tier
  draft(query)  -> a fast, cheap answer used directly for trivial queries
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass

import openvino_genai as ov_genai

from .config import CONFIG

_CHAT = (
    "<|im_start|>system\n{system}<|im_end|>\n"
    "<|im_start|>user\n{user}<|im_end|>\n"
    "<|im_start|>assistant\n"
)

_ROUTER_SYSTEM = (
    "You are a difficulty router for a code-assistant cascade. Output ONLY a "
    'compact JSON object: {"difficulty": <0..1>, "category": '
    '"<trivial|standard|hard>"}. Calibrate difficulty on this exact scale and '
    "use the FULL range:\n"
    "0.05-0.20 trivial: one-liners, syntax recall, a tiny self-contained "
    "function (reverse a string, sum a list, fizzbuzz).\n"
    "0.40-0.65 standard: one non-trivial function or small class with clear "
    "requirements (an LRU cache, parse CSV, a REST handler).\n"
    "0.85-0.98 hard: distributed systems, concurrency correctness, tricky "
    "algorithms, multi-file design, ambiguous specs (a Raft implementation, a "
    "lock-free queue).\n"
    "JSON only, no prose."
)

_DRAFT_SYSTEM = "You are a concise expert coding assistant. Answer directly with code."


@dataclass
class RouteResult:
    difficulty: float
    category: str
    latency_s: float
    device: str


@dataclass
class DraftResult:
    text: str
    latency_s: float
    device: str


class NPUWorker:
    def __init__(self) -> None:
        self.device = self._compile()

    @staticmethod
    def _npu_can_compile() -> bool:
        # The NPU (vpux) compiler can hard-abort the process (LLVM ERROR /
        # non-zero exit) rather than raise a catchable exception, so probe it
        # in a throwaway subprocess before trusting it in-process.
        code = (
            "import openvino_genai as g;"
            f"g.LLMPipeline(r'{CONFIG.npu_model_dir}','NPU');"
            "print('ok')"
        )
        try:
            r = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=240,
            )
            return r.returncode == 0 and "ok" in r.stdout
        except (subprocess.TimeoutExpired, OSError):
            return False

    def _compile(self) -> str:
        last_err: Exception | None = None
        for dev in CONFIG.npu_device_order:
            if dev == "NPU" and not self._npu_can_compile():
                print(
                    "[npu_worker] NPU rejected this model (vpux compiler) -- "
                    "falling back to next device"
                )
                continue
            try:
                self._pipe = ov_genai.LLMPipeline(CONFIG.npu_model_dir, dev)
                return dev
            except Exception as e:
                last_err = e
                print(f"[npu_worker] device {dev} unavailable: {e}")
        raise RuntimeError(
            f"No OpenVINO device could load the Tier-1 model: {last_err}"
        )

    def _gen(self, system: str, user: str, max_new_tokens: int) -> tuple[str, float]:
        cfg = ov_genai.GenerationConfig()
        cfg.max_new_tokens = max_new_tokens
        cfg.stop_strings = {"<|im_end|>"}
        cfg.include_stop_str_in_output = False
        prompt = _CHAT.format(system=system, user=user)
        t0 = time.perf_counter()
        out = self._pipe.generate(prompt, cfg)
        return str(out).strip(), time.perf_counter() - t0

    def route(self, query: str) -> RouteResult:
        raw, dt = self._gen(_ROUTER_SYSTEM, query, max_new_tokens=48)
        difficulty, category = 0.5, "standard"
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                difficulty = float(obj.get("difficulty", 0.5))
                category = str(obj.get("category", "standard"))
            except (ValueError, TypeError):
                pass
        difficulty = min(1.0, max(0.0, difficulty))
        return RouteResult(difficulty, category, dt, self.device)

    def draft(
        self, query: str, max_new_tokens: int | None = None
    ) -> DraftResult:
        text, dt = self._gen(
            _DRAFT_SYSTEM, query,
            max_new_tokens=max_new_tokens or CONFIG.npu_max_new_tokens,
        )
        return DraftResult(text, dt, self.device)
