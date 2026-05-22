"""edge-image model server (C2) — SDXL (diffusers/CUDA) behind a tiny FastAPI.

Resident: loads the SDXL pipeline once on startup. The `edge-image` skill
(Claude) POSTs a spec, gets a PNG path back, then Reads the PNG with its own
vision to critique + iterate. PNG -> runs/artifacts/; metadata -> runs/edge-image.rec
(same logfmt grammar as every tier, so replay/dashboard see it; spend $0, local).

Setup + run:
    uv pip install torch --index-url https://download.pytorch.org/whl/cu124  # CUDA build
    uv sync --extra imagegen
    uv run uvicorn scripts.image_server:app --port 8188
    # set CASCADE_FREE_OLLAMA=1 to unload the 14B coder on startup (12GB VRAM
    # can't hold SDXL + the coder at once).
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade.config import CONFIG  # noqa: E402
from cascade.image_worker import make_image_worker  # noqa: E402
from mcp_servers._rec import make_recorder, recorded  # noqa: E402

_REC = make_recorder("edge-image")
_worker = None  # set on startup (resident SDXL pipeline)


def _free_ollama() -> None:
    """Best-effort: unload the Ollama coder so SDXL has the 12GB to itself.
    keep_alive:0 tells Ollama to evict the model immediately. Never raises."""
    try:
        httpx.post(f"{CONFIG.ollama_base_url.rstrip('/')}/api/generate",
                   json={"model": CONFIG.gpu_model, "keep_alive": 0}, timeout=10.0)
    except httpx.HTTPError:
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _worker
    if os.environ.get("CASCADE_FREE_OLLAMA") == "1":
        _free_ollama()
    _worker = make_image_worker()   # eager SDXL load (~30s)
    yield


app = FastAPI(title="edge-image", lifespan=lifespan)


class Spec(BaseModel):
    prompt: str
    negative_prompt: str | None = None
    width: int = CONFIG.image_size
    height: int = CONFIG.image_size
    steps: int = CONFIG.image_steps
    guidance_scale: float = CONFIG.image_guidance
    seed: int | None = None


@app.get("/health")
def health() -> dict:
    return {"available": _worker is not None, "model": CONFIG.image_model,
            "device": CONFIG.image_device, "artifacts": CONFIG.image_artifacts_dir}


@recorded(_REC)
def generate(spec: dict) -> dict:
    """The recorded op boundary -> writes runs/edge-image.rec (tool=generate)."""
    r = _worker.generate(spec)
    return {"available": r.available, "path": r.path, "seed": r.seed,
            "latency_s": round(r.latency_s, 2), "model": r.model}


@app.post("/generate")
def generate_endpoint(spec: Spec) -> dict:
    if _worker is None:  # pragma: no cover - startup race / model failed to load
        return {"available": False, "error": "model not loaded"}
    return generate(spec.model_dump())
