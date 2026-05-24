"""edge-image Tier (C2) — Stable Diffusion (SD/SDXL) via diffusers on the GPU (CUDA).

Resident: the pipeline is loaded once (~30 s) and reused for the process, mirroring
npu_worker's "heavy model in a closure" shape. `torch`/`diffusers` are
lazy-loaded (only inside the build/generate path), so this module imports without
the heavy `imagegen` extra installed — the test suite never pulls torch.

The worker is intentionally pure-ish: spec dict in, artifact + metadata out. No
prompt-crafting or critique here — that's the agent's job (the edge-image skill;
Claude is the prompt mediator + vision critic). This keeps the op boundary
serializable, so it lifts to a Celery `image.generate` task later (C2).
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from .config import CONFIG


@dataclass
class ImageResult:
    path: str
    seed: int
    latency_s: float
    model: str
    spec: dict = field(default_factory=dict)
    available: bool = True


def _load_pipe(model: str, device: str):
    """Eager (~30 s) load, model-agnostic. fp16 + attention slicing / VAE tiling
    so the pipeline fits the 12 GB card. AutoPipelineForText2Image picks the right
    class (SD vs SDXL) from the checkpoint, so CASCADE_IMAGE_MODEL can point at a
    smaller model (e.g. SD1.5 ~2 GB) without a code change. Lazy-imports torch."""
    import torch  # noqa: PLC0415 - lazy: keep the module importable w/o the extra
    from diffusers import AutoPipelineForText2Image  # noqa: PLC0415

    try:
        pipe = AutoPipelineForText2Image.from_pretrained(
            model, torch_dtype=torch.float16, variant="fp16", use_safetensors=True)
    except (ValueError, OSError):  # not every checkpoint ships an fp16 variant (SD1.5)
        pipe = AutoPipelineForText2Image.from_pretrained(
            model, torch_dtype=torch.float16, use_safetensors=True)
    pipe = pipe.to(device)
    pipe.enable_attention_slicing()
    try:
        pipe.enable_vae_tiling()
    except (AttributeError, ValueError):  # pragma: no cover - version-dependent
        pass
    return pipe


def _generate(pipe, model: str, device: str, artifacts_dir: str,
              spec: dict) -> ImageResult:
    import torch  # noqa: PLC0415

    seed = int(spec["seed"]) if spec.get("seed") is not None \
        else uuid.uuid4().int % (2 ** 31)
    gen = torch.Generator(device=device).manual_seed(seed)
    t0 = time.perf_counter()
    image = pipe(
        prompt=spec["prompt"],
        negative_prompt=spec.get("negative_prompt") or None,
        width=int(spec.get("width", CONFIG.image_size)),
        height=int(spec.get("height", CONFIG.image_size)),
        num_inference_steps=int(spec.get("steps", CONFIG.image_steps)),
        guidance_scale=float(spec.get("guidance_scale", CONFIG.image_guidance)),
        generator=gen,
    ).images[0]
    dt = time.perf_counter() - t0

    out_dir = Path(artifacts_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = str(out_dir / f"{time.strftime('%Y%m%dT%H%M%S')}-{seed}.png")
    image.save(path)
    return ImageResult(path=path, seed=seed, latency_s=dt, model=model, spec=spec)


@dataclass(frozen=True)
class ImageWorker:
    """Immutable handle: the model id + bound generate/available closures over
    the resident pipeline. Pure data — no behavior on the object."""

    model: str
    generate: Callable[[dict], ImageResult]
    available: Callable[[], bool]


def make_image_worker() -> ImageWorker:
    """Load SDXL (eager) and bind generate over the resident pipeline."""
    pipe = _load_pipe(CONFIG.image_model, CONFIG.image_device)
    return ImageWorker(
        model=CONFIG.image_model,
        generate=partial(_generate, pipe, CONFIG.image_model,
                         CONFIG.image_device, CONFIG.image_artifacts_dir),
        available=lambda: True,
    )
