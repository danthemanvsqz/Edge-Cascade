"""Tier 1 — small coder model on the Intel NPU via OpenVINO GenAI.

Two roles in the cascade:
  route(query)  -> difficulty score + category, so the orchestrator can pick a tier
  draft(query)  -> a fast, cheap answer used directly for trivial queries

No worker object: the compiled pipeline is real, long-lived state, but it is
built once and reused for the process with no `with` scope -- so it is closed
over by `route`/`draft` closures, not wrapped in a class. `make_npu_worker()`
does the (eager, ~9-20s) compile and binds them. `openvino_genai` is
lazy-loaded (only inside the compile/gen path), so this module still imports
without the optional `accel` extra.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from .config import CONFIG

_OV = None


def _ov():
    """Lazy-load openvino_genai so the module imports without the (heavy,
    optional) `accel` extra -- the import only happens when a worker is
    actually compiled/used."""
    global _OV
    if _OV is None:
        try:
            import openvino_genai
        except ModuleNotFoundError as e:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "openvino_genai is required to run the NPU/iGPU tier. "
                "Install it: uv sync --extra accel"
            ) from e
        _OV = openvino_genai
    return _OV

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

_DRAFT_SYSTEM = (
    "You are a concise expert coding assistant. Answer with code in one fenced "
    "block. Initialise state for every entity before use -- including ones that "
    "appear only as references (e.g. a graph node seen only as a neighbour) -- "
    "and avoid KeyErrors on missing keys."
)


@dataclass
class RouteResult:
    difficulty: float
    category: str
    latency_s: float
    device: str
    seed: int = 0


@dataclass
class DraftResult:
    text: str
    latency_s: float
    device: str
    seed: int = 0


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


def _compile() -> tuple[str, object]:
    """Walk CONFIG.npu_device_order (NPU probed in a subprocess first); return
    (device, pipe) for the first that loads, else raise."""
    last_err: Exception | None = None
    for dev in CONFIG.npu_device_order:
        if dev == "NPU" and not _npu_can_compile():
            print(
                "[npu_worker] NPU rejected this model (vpux compiler) -- "
                "falling back to next device"
            )
            continue
        try:
            pipe = _ov().LLMPipeline(CONFIG.npu_model_dir, dev)
            return dev, pipe
        except Exception as e:
            last_err = e
            print(f"[npu_worker] device {dev} unavailable: {e}")
    raise RuntimeError(
        f"No OpenVINO device could load the Tier-1 model: {last_err}"
    )


def _gen(
    pipe, system: str, user: str, max_new_tokens: int
) -> tuple[str, float, int]:
    cfg = _ov().GenerationConfig()
    cfg.max_new_tokens = max_new_tokens
    cfg.stop_strings = {"<|im_end|>"}
    cfg.include_stop_str_in_output = False
    seed = random.randint(0, 2**31 - 1)
    # rng_seed only influences output when sampling (temperature > 0);
    # at the default temp=0.0 greedy decode the seed is recorded but inert.
    cfg.rng_seed = seed
    if CONFIG.npu_temperature > 0:
        cfg.temperature = CONFIG.npu_temperature
    prompt = _CHAT.format(system=system, user=user)
    t0 = time.perf_counter()
    out = pipe.generate(prompt, cfg)
    return str(out).strip(), time.perf_counter() - t0, seed


def _route(pipe, device: str, query: str) -> RouteResult:
    raw, dt, seed = _gen(pipe, _ROUTER_SYSTEM, query, max_new_tokens=48)
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
    return RouteResult(difficulty, category, dt, device, seed)


def _draft(
    pipe, device: str, query: str, max_new_tokens: int | None = None
) -> DraftResult:
    text, dt, seed = _gen(
        pipe, _DRAFT_SYSTEM, query,
        max_new_tokens=max_new_tokens or CONFIG.npu_max_new_tokens,
    )
    return DraftResult(text, dt, device, seed)


@dataclass(frozen=True)
class NPUWorker:
    """Immutable Tier-1 handle: the loaded device + bound route/draft closures
    over the compiled pipeline. Pure data -- no behavior on the object."""

    device: str
    route: Callable[[str], RouteResult]
    draft: Callable[..., DraftResult]


def make_npu_worker() -> NPUWorker:
    """Compile Tier-1 (NPU probe -> iGPU -> CPU) and bind route/draft over the
    loaded pipeline. Eager compile (~9-20s); callers print 'loading' around
    it. The module still imports without `accel` (openvino_genai is lazy)."""
    device, pipe = _compile()
    return NPUWorker(
        device=device,
        route=partial(_route, pipe, device),
        draft=partial(_draft, pipe, device),
    )
