#!/usr/bin/env python3
"""PT-1 diagnostic: verify qwen2.5-coder:14b is fully offloaded to GPU.

Compares VRAM used before/after model load against the GGUF blob size.
When all layers are on GPU, the VRAM delta should be >= the model weights;
when layers spill to CPU, the delta is much smaller than the GGUF.

Usage:
    CASCADE_GPU_BACKEND=llama_cpp uv run python scripts/pt1_gpu_offload_check.py

Requires:
    - A running nvidia-smi (CUDA GPU present)
    - CASCADE_GPU_BACKEND=llama_cpp
    - uv sync --extra llama-cpp  (the CUDA llama-cpp-python wheel)
    - The qwen2.5-coder:14b model pulled in Ollama's blob cache
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _vram_used_mb() -> int | None:
    """Query current GPU VRAM usage via nvidia-smi. Returns None if unavailable."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True, stderr=subprocess.DEVNULL,
        )
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


def _vram_total_mb() -> int | None:
    """Query total GPU VRAM via nvidia-smi."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            text=True, stderr=subprocess.DEVNULL,
        )
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


def main() -> None:
    if os.getenv("CASCADE_GPU_BACKEND") != "llama_cpp":
        print("ERROR: Set CASCADE_GPU_BACKEND=llama_cpp before running.")
        print(
            "  e.g.  $env:CASCADE_GPU_BACKEND='llama_cpp';"
            " uv run python scripts/pt1_gpu_offload_check.py"
        )
        sys.exit(1)

    # Import after the env check so the error message is clear.
    from cascade.config import CONFIG
    from cascade.llama_worker import _resolve_ollama_blob, make_llama_worker

    model_id = CONFIG.gpu_model
    gguf = _resolve_ollama_blob(model_id, Path(CONFIG.ollama_models_dir))
    gguf_mb = gguf.stat().st_size // (1024 * 1024)

    total_vram = _vram_total_mb()
    before = _vram_used_mb()

    print(f"Model     : {model_id}")
    print(f"GGUF      : {gguf.name}  ({gguf_mb:,} MB)")
    if total_vram is not None:
        print(f"VRAM total: {total_vram:,} MB")
    if before is not None:
        print(f"VRAM before load: {before:,} MB")
    else:
        print("VRAM: nvidia-smi unavailable")

    # Warn early if Ollama is likely holding the model in VRAM.
    if before is not None and total_vram is not None:
        free = total_vram - before
        if free < gguf_mb:
            print()
            print(f"WARNING: only {free:,} MB VRAM free, but GGUF needs {gguf_mb:,} MB.")
            print("  Ollama may have the model loaded. Stop the Ollama process first:")
            print("  Stop-Process -Name ollama  (or let the Ollama tray icon quit)")
            print("  Then re-run this diagnostic.")
            print()

    print()
    print("Loading model (this may take ~10s) ...")

    worker = make_llama_worker()
    _ = worker.available()  # confirm it's alive

    after = _vram_used_mb()
    print()

    if before is not None and after is not None:
        delta = after - before
        pct = (delta / gguf_mb * 100) if gguf_mb > 0 else 0.0
        print(f"VRAM after load : {after:,} MB")
        print(f"VRAM delta      : +{delta:,} MB  ({pct:.0f}% of GGUF size)")
        print()
        # Interpretation:
        # Full offload: delta >= GGUF (model weights + KV cache both on GPU)
        # Partial offload: delta < GGUF (some layers on CPU)
        # The KV cache at n_ctx=8192 adds to the delta, so full offload → pct > 100.
        if delta >= gguf_mb:
            print("PASS: VRAM delta >= GGUF size — all layers are on GPU.")
            print(f"      (excess {delta - gguf_mb:,} MB is the KV cache + overhead at n_ctx=8192)")
        elif pct >= 80:
            print(f"PARTIAL: VRAM delta is {pct:.0f}% of GGUF — "
                  "most layers on GPU, some may spill.")
            print("         Run PT-2 next: reduce n_ctx=8192→4096 to free VRAM for the KV cache.")
        else:
            print(f"FAIL: VRAM delta only {pct:.0f}% of GGUF — significant CPU spill.")
            print("      This likely explains the 3.4x llama_cpp vs Ollama perf gap.")
            print("      Action: check n_gpu_layers=-1 is taking effect; try flash_attn=True.")
    else:
        print("nvidia-smi unavailable; cannot measure VRAM delta.")
        print("Start the worker manually and run: nvidia-smi dmon -s mu -d 1")
        print("Watch VRAM climb during model load — should reach ~8-10 GB for full offload.")


if __name__ == "__main__":
    main()
