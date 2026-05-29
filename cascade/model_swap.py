"""Model-swap arbiter -- Phase 2 Slice 3a.

The single source of truth for which model is resident on this worker
process's hardware RIGHT NOW. Per the Phase-2 design (see
docs/DESIGN-celery-phase2.md), each Celery worker process owns its own
`_resident` dict; the swap arbiter is the ONLY place that mutates it.
Per-model generate tasks (Slice 3b+) read via `get(name)` and assume
the resident model matches what they expect -- they don't probe.

Composition contract: clients chain `model.swap.s(name)` BEFORE the
corresponding `generate_<model>.s(...)` task. The swap task runs on
the same queue as the model it manages, so Celery's FIFO-per-queue
guarantee orders swap-then-generate correctly even under contention.

VRAM accounting is conservative: each model is registered with a
footprint (rounded UP, like cloud_worker.py's price table) so the
arbiter never under-counts and never OOMs the GPU. If a model is
registered but its footprint exceeds the total VRAM budget alone, the
swap returns `loaded:false` with a clear reason -- the cascade treats
unavailable models as a status, not an error (charter inv. 5), so the
caller (the Canvas chain) can hand off to Tier-3 just like a down tier.

The registry is module-level: per-model task modules (added in Slice
3b/3c) call `register(name, factory, footprint_mb)` at import. Tests
clear the registry between cases via the `_reset_swap_state` fixture.

Lives in cascade/ to share the module with the per-tier Celery tasks
(which import the swap helpers); covered fully by unit tests (no
hardware needed -- factory is injectable).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cascade.config import CONFIG


@dataclass(frozen=True)
class ModelHandle:
    """The resident state for ONE model on this worker. `handle` is the
    worker-specific value (a `GPUWorker`, an `NPUWorker`, etc.); the
    arbiter is opaque to that shape -- per-model generate tasks
    downcast as they need."""

    name: str
    footprint_mb: int
    handle: object


# Module-level singletons. The worker process owns these; only this
# module's `swap`/`register`/`_reset` mutate them. Generate tasks read
# via `get`.
_resident: dict[str, ModelHandle] = {}
_lru_order: list[str] = []
_FACTORIES: dict[str, tuple[Callable[[], Any], int]] = {}


def register(name: str, factory: Callable[[], Any], footprint_mb: int) -> None:
    """Register a model name with its factory + VRAM footprint. Called
    by per-model task modules at import; the swap arbiter has no
    knowledge of any specific model otherwise. Idempotent re-register
    overwrites (last definition wins) so tests + reloads behave
    predictably."""
    _FACTORIES[name] = (factory, footprint_mb)


def get(name: str) -> object | None:
    """Return the resident model's worker handle, or None if not loaded.
    Generate tasks call this AFTER chaining through `model.swap` -- a
    None return at this point means the upstream chain didn't actually
    swap, which is a programming error worth surfacing (the caller's
    job to handle, this module just reports state)."""
    h = _resident.get(name)
    return h.handle if h is not None else None


def status() -> dict:
    """Read-only snapshot for the dashboard + the `model.status` task.
    Returns {resident, vram_used_mb, vram_free_mb, vram_total_mb}. The
    order of `resident` is LRU-ascending (oldest first) so debugging
    can see who'd be evicted next."""
    used = sum(h.footprint_mb for h in _resident.values())
    return {
        "resident": [name for name in _lru_order],
        "vram_used_mb": used,
        "vram_free_mb": CONFIG.vram_total_mb - used,
        "vram_total_mb": CONFIG.vram_total_mb,
    }


def swap(name: str) -> dict:
    """Ensure `name` is the resident model. Idempotent: if already
    loaded, just touches LRU order and returns {loaded:true,
    was_swap:false}.

    On a miss: computes VRAM headroom, evicts LRU until the new model
    fits, calls the registered factory, records the handle, returns
    {loaded:true, was_swap:true, evicted:[...], vram_used_mb}.

    On failure (unknown name, model exceeds total VRAM, factory raises),
    returns {loaded:false, name, reason} WITHOUT raising. The cascade
    treats unavailable models as a hand-off, not an error (charter
    inv. 5)."""
    # Already resident: touch LRU + return.
    if name in _resident:
        _lru_order.remove(name)
        _lru_order.append(name)
        return {
            "loaded": True, "name": name, "was_swap": False,
            "evicted": [], "vram_used_mb": _used_mb(),
        }

    # Unknown model name.
    if name not in _FACTORIES:
        return {
            "loaded": False, "name": name,
            "reason": f"unknown model {name!r}; not in registry",
        }

    factory, footprint = _FACTORIES[name]

    # Model alone exceeds the GPU's total VRAM -- structurally impossible.
    if footprint > CONFIG.vram_total_mb:
        return {
            "loaded": False, "name": name,
            "reason": (f"model footprint {footprint}MB exceeds "
                       f"vram_total_mb={CONFIG.vram_total_mb}"),
        }

    # Evict LRU until the new model fits in free VRAM.
    free = CONFIG.vram_total_mb - _used_mb()
    evicted: list[str] = []
    while footprint > free and _lru_order:
        victim_name = _lru_order.pop(0)
        victim = _resident.pop(victim_name)
        evicted.append(victim_name)
        free += victim.footprint_mb

    # Load. A factory exception is reported as `loaded:false` per
    # charter inv. 5; the partial state from any eviction stays --
    # those models are GONE either way, and the caller's chain can
    # decide whether to retry-swap a different model.
    try:
        handle = factory()
    except Exception as e:  # noqa: BLE001 -- hand off as `loaded:false`
        return {
            "loaded": False, "name": name, "evicted": evicted,
            "reason": f"{type(e).__name__}: {e}",
        }

    _resident[name] = ModelHandle(name=name, footprint_mb=footprint, handle=handle)
    _lru_order.append(name)
    return {
        "loaded": True, "name": name, "was_swap": True,
        "evicted": evicted, "vram_used_mb": _used_mb(),
    }


def _used_mb() -> int:
    return sum(h.footprint_mb for h in _resident.values())


def _reset_for_tests() -> None:
    """Clear all module-level state. Tests use this between cases via
    the autouse fixture in tests/test_model_swap.py. NOT for production
    use -- the resident state is per-process and should survive the
    process lifetime per worker_max_tasks_per_child=0."""
    _resident.clear()
    _lru_order.clear()
    _FACTORIES.clear()
