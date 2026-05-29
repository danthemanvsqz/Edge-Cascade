"""Tests for the model-swap arbiter -- Phase 2 Slice 3a.

`cascade.model_swap` is pure Python (no Celery, no real models): the
factory is injectable so the entire arbiter is covered without
hardware. The accompanying Celery wrappers (`swap_task`, `status_task`
in `cascade.tasks`) are tested separately under the celery-extra-gated
suite (those need pytest.importorskip("celery") and live in
test_tasks_model_swap.py at Slice 3b).
"""
from __future__ import annotations

import pytest

from cascade import model_swap
from cascade.config import CONFIG


@pytest.fixture(autouse=True)
def _reset_swap_state():
    """Module-level singletons in model_swap leak across tests; clear
    them per case so each test sees a pristine arbiter."""
    model_swap._reset_for_tests()
    yield
    model_swap._reset_for_tests()


def _fake_handle(name: str) -> object:
    """A sentinel for the worker handle the factory returns. The
    arbiter is opaque to its shape; tests just need something to assert
    `get(name)` returns."""
    return {"sentinel": name}


def test_register_adds_to_factory_map():
    """register() puts a model in the factory map; the arbiter knows
    about it but doesn't load yet."""
    called = {"count": 0}

    def factory():
        called["count"] += 1
        return _fake_handle("qwen14b")

    model_swap.register("qwen14b", factory, footprint_mb=9000)
    # Registration alone doesn't trigger the factory.
    assert called["count"] == 0
    # The model isn't resident yet.
    assert model_swap.get("qwen14b") is None
    # Status reflects empty.
    assert model_swap.status()["resident"] == []


def test_swap_first_time_loads_and_tracks():
    """A first-time swap calls the factory, records the handle, and
    returns `was_swap:true` with the freshly-loaded model in the
    resident set."""
    model_swap.register("qwen14b", lambda: _fake_handle("qwen14b"), 9000)
    out = model_swap.swap("qwen14b")
    assert out == {
        "loaded": True, "name": "qwen14b", "was_swap": True,
        "evicted": [], "vram_used_mb": 9000,
    }
    assert model_swap.get("qwen14b") == _fake_handle("qwen14b")
    s = model_swap.status()
    assert s["resident"] == ["qwen14b"]
    assert s["vram_used_mb"] == 9000
    assert s["vram_free_mb"] == CONFIG.vram_total_mb - 9000


def test_swap_idempotent_when_already_resident():
    """Calling swap() on a resident model is a no-op: factory NOT called
    again, returns `was_swap:false`, but the LRU order updates so a
    subsequent eviction picks a different victim."""
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return _fake_handle("qwen14b")

    model_swap.register("qwen14b", factory, 9000)
    model_swap.swap("qwen14b")
    assert calls["n"] == 1
    out = model_swap.swap("qwen14b")
    assert out["was_swap"] is False
    assert out["loaded"] is True
    assert calls["n"] == 1  # factory NOT called a second time


def test_swap_evicts_lru_when_full(mocker):
    """When the new model doesn't fit, the LRU model is evicted first.
    With small VRAM budget + two models that don't co-fit, swap(A)
    then swap(B) evicts A."""
    mocker.patch("cascade.model_swap.CONFIG", mocker.Mock(vram_total_mb= 10000))  # 10GB budget
    model_swap.register("qwen14b", lambda: _fake_handle("qwen14b"), 9000)
    model_swap.register("sd15", lambda: _fake_handle("sd15"), 6000)
    model_swap.swap("qwen14b")  # 9000/10000 used
    out = model_swap.swap("sd15")  # needs 6000; total would be 15000 > 10000
    assert out["loaded"] is True
    assert out["was_swap"] is True
    assert out["evicted"] == ["qwen14b"]
    assert out["vram_used_mb"] == 6000
    # qwen14b is gone; sd15 is the only resident.
    assert model_swap.get("qwen14b") is None
    assert model_swap.get("sd15") == _fake_handle("sd15")


def test_swap_evicts_multiple_lru_until_fit(mocker):
    """If one eviction isn't enough, swap keeps evicting until the new
    model fits OR the resident set is empty."""
    mocker.patch("cascade.model_swap.CONFIG", mocker.Mock(vram_total_mb= 12000))
    model_swap.register("a", lambda: _fake_handle("a"), 4000)
    model_swap.register("b", lambda: _fake_handle("b"), 4000)
    model_swap.register("c", lambda: _fake_handle("c"), 4000)
    model_swap.register("big", lambda: _fake_handle("big"), 10000)
    model_swap.swap("a")  # 4000
    model_swap.swap("b")  # 8000
    model_swap.swap("c")  # 12000 (full)
    # Now load `big` (10000) -- needs to evict at least 2 of {a,b,c}.
    # LRU order is a, b, c so a goes first, then b. After evicting both,
    # free=8000; still not enough for 10000; evict c too.
    out = model_swap.swap("big")
    assert out["loaded"] is True
    assert out["was_swap"] is True
    assert out["evicted"] == ["a", "b", "c"]
    assert model_swap.status()["resident"] == ["big"]


def test_swap_unknown_model_returns_handoff():
    """An unregistered model name => loaded:false, clear reason. Never
    raises (charter inv. 5)."""
    out = model_swap.swap("not-a-real-model")
    assert out["loaded"] is False
    assert out["name"] == "not-a-real-model"
    assert "unknown model" in out["reason"]
    assert model_swap.status()["resident"] == []


def test_swap_exceeds_total_vram_returns_handoff(mocker):
    """A model registered with a footprint > total VRAM is structurally
    impossible to load. Return loaded:false with the budget in the
    reason so a debugging operator can act."""
    mocker.patch("cascade.model_swap.CONFIG", mocker.Mock(vram_total_mb= 8000))
    model_swap.register("huge", lambda: _fake_handle("huge"), 99999)
    out = model_swap.swap("huge")
    assert out["loaded"] is False
    assert "exceeds" in out["reason"]
    assert "8000" in out["reason"]


def test_swap_factory_exception_returns_handoff_and_keeps_eviction(mocker):
    """If the factory raises (e.g. CUDA OOM at load time), report
    loaded:false with the exception in `reason`. Any models evicted
    BEFORE the factory call stay evicted -- they're gone either way."""
    mocker.patch("cascade.model_swap.CONFIG", mocker.Mock(vram_total_mb= 10000))
    model_swap.register("good", lambda: _fake_handle("good"), 8000)

    def broken():
        raise RuntimeError("CUDA OOM at load time")

    model_swap.register("broken", broken, 5000)
    model_swap.swap("good")  # 8000 used
    out = model_swap.swap("broken")  # need 5000 -> evict good (8000 freed)
    assert out["loaded"] is False
    assert "CUDA OOM" in out["reason"]
    assert out["evicted"] == ["good"]
    # good was evicted; broken never loaded; resident is empty.
    assert model_swap.status()["resident"] == []


def test_status_reflects_lru_order():
    """status().resident is LRU-ascending (oldest first) so the next
    eviction candidate is `resident[0]`. Touching via swap() updates
    that order."""
    model_swap.register("a", lambda: _fake_handle("a"), 100)
    model_swap.register("b", lambda: _fake_handle("b"), 100)
    model_swap.swap("a")
    model_swap.swap("b")
    assert model_swap.status()["resident"] == ["a", "b"]
    # Touching `a` again moves it to the end (most-recently-used).
    model_swap.swap("a")
    assert model_swap.status()["resident"] == ["b", "a"]


def test_status_includes_vram_accounting(mocker):
    """status returns vram_used_mb, vram_free_mb, vram_total_mb so the
    dashboard can paint a progress bar without doing its own math."""
    mocker.patch("cascade.model_swap.CONFIG", mocker.Mock(vram_total_mb= 12000))
    model_swap.register("m", lambda: _fake_handle("m"), 4000)
    model_swap.swap("m")
    s = model_swap.status()
    assert s["vram_used_mb"] == 4000
    assert s["vram_free_mb"] == 8000
    assert s["vram_total_mb"] == 12000


def test_get_returns_handle_when_resident():
    """get(name) returns the worker handle from the registered factory."""
    h = _fake_handle("qwen14b")
    model_swap.register("qwen14b", lambda: h, 9000)
    model_swap.swap("qwen14b")
    assert model_swap.get("qwen14b") is h


def test_get_returns_none_when_not_resident():
    """Before any swap, get() returns None for everything (including
    registered models). After eviction, get() returns None for the
    evicted model."""
    model_swap.register("qwen14b", lambda: _fake_handle("qwen14b"), 9000)
    assert model_swap.get("qwen14b") is None
    assert model_swap.get("not-registered") is None


def test_register_overwrites_existing():
    """A repeat register() for the same name takes the latest factory
    and footprint (last-write-wins). Useful for tests + reloads;
    callers shouldn't rely on this in production."""
    model_swap.register("m", lambda: _fake_handle("first"), 5000)
    model_swap.register("m", lambda: _fake_handle("second"), 6000)
    model_swap.swap("m")
    assert model_swap.get("m") == _fake_handle("second")
    assert model_swap.status()["vram_used_mb"] == 6000


def test_swap_with_empty_resident_set_doesnt_loop_forever(mocker):
    """A pathological model that doesn't fit even on an empty GPU
    (caught earlier by the `> vram_total_mb` check) must NOT enter the
    eviction loop. Pinned because an off-by-one in the loop condition
    could spin forever."""
    mocker.patch("cascade.model_swap.CONFIG", mocker.Mock(vram_total_mb= 1000))
    model_swap.register("huge", lambda: _fake_handle("huge"), 99999)
    out = model_swap.swap("huge")
    assert out["loaded"] is False
    assert model_swap.status()["resident"] == []
