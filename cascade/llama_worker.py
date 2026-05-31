"""Tier 2 — qwen2.5-coder:14b on the NVIDIA GPU via llama-cpp-python (direct).

Phase 2 Slice 1: a SECOND backend for the GPU tier that loads the same GGUF
weights Ollama uses, but holds them resident in the worker process instead
of talking HTTP to a long-running daemon. No serialization hop, no second
process boundary, simpler stack traces on failure.

Same `GPUWorker` shape as `cascade.gpu_worker.make_gpu_worker` so
`cascade.tasks` and the Canvas chain see no diff -- this is a drop-in swap
behind the `CASCADE_GPU_BACKEND=llama_cpp` flag (default stays `ollama`
until the Slice 2 parity findings prove the direct path matches).

llama-cpp-python is lazy-loaded (only inside `make_llama_worker`), so this
module imports without the optional `llama-cpp` extra -- the import only
happens when the backend is actually selected.

Weights resolution: parses the Ollama manifest at
`<models_dir>/manifests/registry.ollama.ai/library/<name>/<tag>` to find
the layer with `mediaType: application/vnd.ollama.image.model`, then maps
its digest to `<models_dir>/blobs/sha256-<digest>`. One source of truth
(Ollama's existing cache) so we don't re-download the model.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from .config import CONFIG

_log = logging.getLogger(__name__)

_SYSTEM = (
    "You are an expert coding assistant. Produce correct, complete, runnable "
    "code as a single fenced code block. Before answering, sanity-check the "
    "common failure modes: derive the full set of entities from ALL references "
    "(e.g. a graph node that appears only as a neighbour still needs its own "
    "entry) and initialise state for every one before use; handle empty input, "
    "boundaries, and absent keys without raising."
)


@dataclass
class LlamaResult:
    text: str
    latency_s: float
    tokens_per_s: float
    model: str
    available: bool = True


def _resolve_ollama_blob(model_id: str, models_dir: Path) -> Path:
    """Map an Ollama model id (e.g. `qwen2.5-coder:14b`) to its on-disk GGUF
    blob. Reads the manifest at
    `<models_dir>/manifests/registry.ollama.ai/library/<name>/<tag>` and
    returns the path to the layer with
    `mediaType: application/vnd.ollama.image.model`.

    Raises `RuntimeError` with a clear message if the manifest is missing
    (model not pulled) or has no model layer (corrupted manifest)."""
    if ":" in model_id:
        name, tag = model_id.split(":", 1)
    else:
        name, tag = model_id, "latest"
    manifest_path = (
        models_dir / "manifests" / "registry.ollama.ai" / "library" / name / tag
    )
    if not manifest_path.exists():
        raise RuntimeError(
            f"Ollama manifest not found at {manifest_path}; "
            f"is `{model_id}` pulled? Run `ollama pull {model_id}`."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for layer in manifest.get("layers", []):
        if layer.get("mediaType") == "application/vnd.ollama.image.model":
            digest = layer["digest"]  # e.g. `sha256:ac9bc...`
            sha = digest.replace(":", "-")  # Ollama on-disk: `sha256-...`
            blob = models_dir / "blobs" / sha
            if not blob.exists():
                raise RuntimeError(
                    f"Manifest layer {digest} not present at {blob}; "
                    f"Ollama cache may be corrupted."
                )
            return blob
    raise RuntimeError(
        f"Manifest for `{model_id}` has no `image.model` layer; "
        f"unexpected schema. Manifest: {manifest_path}"
    )


def _preload_cuda_runtime() -> None:  # pragma: no cover - env-dependent
    """On Windows, pre-load the NVIDIA CUDA runtime DLLs so llama_cpp's
    bundled `llama.dll` finds them at import. The `nvidia-cuda-runtime-cu12`
    and `nvidia-cublas-cu12` PyPI packages stash the DLLs under
    `<venv>/Lib/site-packages/nvidia/<lib>/bin/`, but llama_cpp's loader
    uses a constrained Windows DLL search path that skips those dirs --
    pre-loading the runtime via absolute path is the documented workaround
    (see github.com/abetlen/llama-cpp-python issues on Windows + CUDA).

    No-op on non-Windows (Linux's RPATH handles this) and on systems where
    the nvidia-*-cu12 packages aren't installed (the user must install
    them, OR have the CUDA toolkit on PATH)."""
    import sys
    if sys.platform != "win32":
        return
    import ctypes
    import importlib.util
    for pkg, dll in (
        ("nvidia.cuda_runtime", "cudart64_12.dll"),
        ("nvidia.cublas", "cublas64_12.dll"),
    ):
        try:
            spec = importlib.util.find_spec(pkg)
        except (ModuleNotFoundError, ValueError):
            # find_spec raises when a parent namespace package exists but the
            # subpackage doesn't (e.g. `nvidia` namespace present, no cuda_runtime).
            continue
        if spec is None or not spec.submodule_search_locations:
            continue
        for loc in spec.submodule_search_locations:
            dll_path = Path(loc) / "bin" / dll
            if dll_path.exists():
                ctypes.CDLL(str(dll_path))
                break


def _llama():
    """Lazy import. The optional `llama-cpp` extra is only required when
    `CASCADE_GPU_BACKEND=llama_cpp`; this module's IMPORT remains free.
    (Note the underscore vs hyphen: `llama_cpp` is the Python module name,
    `llama-cpp` is the uv extra name -- they're independently spelled.)

    Pre-loads the CUDA runtime on Windows before importing llama_cpp so
    its bundled `llama.dll` finds the runtime's dependencies."""
    _preload_cuda_runtime()
    try:
        import llama_cpp
    except ModuleNotFoundError as e:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "llama-cpp-python is required for the llama_cpp GPU backend. "
            "Install it: uv sync --extra llama-cpp"
        ) from e
    return llama_cpp


def _generate(
    llm, model_id: str, query: str, max_new_tokens: int | None = None,
) -> LlamaResult:
    """The actual inference call. `llm` is injected (a real
    `llama_cpp.Llama` in production, a stub in tests), so this is pure with
    respect to the model and needs no monkeypatching to test."""
    n_tokens = max_new_tokens or CONFIG.gpu_max_new_tokens
    t0 = time.perf_counter()
    try:
        resp = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": query},
            ],
            max_tokens=n_tokens,
        )
    except Exception as e:  # noqa: BLE001 - hand off as unavailable
        dt = time.perf_counter() - t0
        return LlamaResult(f"[llama_cpp error: {e}]", dt, 0.0, model_id, False)
    dt = time.perf_counter() - t0
    text = resp["choices"][0]["message"]["content"]
    usage = resp.get("usage", {}) or {}
    out_tok = int(usage.get("completion_tokens", 0))
    tok_s = round(out_tok / dt, 2) if dt > 0 else 0.0
    return LlamaResult(text, dt, tok_s, model_id, True)


@dataclass(frozen=True)
class GPUWorker:
    """Same shape `cascade.gpu_worker.GPUWorker` exposes -- drop-in. Pure
    data: model id + bound `available`/`generate` closures over the
    resident `llama_cpp.Llama` handle.

    Local re-declaration (not an import from `gpu_worker`) so this module
    stays independent of the Ollama backend. The duck-typed equivalence
    is the contract: any callsite that takes a `cascade.gpu_worker.GPUWorker`
    accepts this one and vice versa."""

    model: str
    available: Callable[[], bool]
    generate: Callable[..., LlamaResult]


def make_llama_worker(model_id: str | None = None) -> GPUWorker:
    """Build the direct-loading Tier-2 worker. Loads the GGUF resolved from
    Ollama's blob cache (single source of truth for `model_id`); the load
    is eager (~10s for 14b on CUDA, then resident for the worker process
    lifetime via `worker_max_tasks_per_child=0`).

    `model_id` defaults to `CONFIG.gpu_model` so the call site doesn't have
    to thread it. `available` returns True if the construction succeeded;
    if init throws, the caller's `gpu_worker.make_gpu_worker` is expected
    to fall back / surface the error per the standard hand-off contract."""
    model_id = model_id or CONFIG.gpu_model
    # PT-2 tuning knobs -- env-var overrides for the parity sweep.
    # CASCADE_LLAMA_N_CTX:    KV context window (default 8192; PT-2 found 4096 saves
    #                         ~1s but harms quality on long tasks -- keep 8192)
    # CASCADE_LLAMA_FLASH_ATTN: flash attention (default True; Ollama enables by default;
    #                           PT-2 confirmed ~3s gain on Case B, within ±20% bar)
    # CASCADE_LLAMA_N_BATCH:  prefill batch size (default 512)
    n_ctx       = int(os.environ.get("CASCADE_LLAMA_N_CTX", "8192"))
    flash_attn  = os.environ.get("CASCADE_LLAMA_FLASH_ATTN", "1").lower() in ("1", "true")
    n_batch     = int(os.environ.get("CASCADE_LLAMA_N_BATCH", "512"))
    try:
        gguf_path = _resolve_ollama_blob(model_id, Path(CONFIG.ollama_models_dir))
        llama_cpp = _llama()
        _log.info(
            "llama_worker: n_ctx=%d flash_attn=%s n_batch=%d gguf=%s",
            n_ctx, flash_attn, n_batch, gguf_path.name[:16],
        )
        llm = llama_cpp.Llama(
            model_path=str(gguf_path),
            # Offload as many layers as fit on the GPU. -1 means "all".
            n_gpu_layers=-1,
            n_ctx=n_ctx,
            flash_attn=flash_attn,
            n_batch=n_batch,
            # Quiet by default; the @recorded decorator captures latency + tokens.
            verbose=False,
        )
    except Exception as exc:  # noqa: BLE001 - model load/resolve can fail many ways
        _log.warning("llama_worker: init failed (%s) -- worker unavailable", exc)
        err_text = f"[llama_cpp unavailable: {exc}]"
        return GPUWorker(
            model=model_id,
            available=lambda: False,
            generate=lambda **_kw: LlamaResult(err_text, 0.0, 0.0, model_id, False),
        )
    return GPUWorker(
        model=model_id,
        available=lambda: True,
        generate=partial(_generate, llm, model_id),
    )
