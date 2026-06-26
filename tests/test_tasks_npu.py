"""Tests for the Tier-1 NPU tasks (`route`, `draft`) -- Canvas Phase 1 Slice 1.

Strategy: mock `cascade.tasks.make_npu_worker` so no real OpenVINO compile
runs (~9-21s). The recorded fns (`route`, `draft`) and the Celery wrappers
(`route_task`, `draft_task`) are tested as the two distinct layers:

- The recorded fns: shape contracts on the dict they return, including the
  `available:false` hand-off when the lazy `_get_npu` compile fails. This is
  the *contract surface* the Canvas chain envelope (D1 in
  docs/PLAN-canvas-phase1.md) will consume in Slice 3.
- The Celery wrappers under `task_always_eager`: prove dispatch round-trips
  the same shape; no broker required.

`cascade.tasks` is in `[tool.coverage.run] omit` (along with `celery_app.py`
and `canvas_spike.py`) -- it needs a running broker + the hardware workers,
so it's live-validated, not unit-cov'd. These tests still execute the route /
draft / wrapper bodies but coverage on them is excluded from the 100% gate.
"""
from __future__ import annotations

import pytest

# Celery is an opt-in extra (`uv sync --extra celery`); CI installs only
# `--extra mcp`. Skip the whole module cleanly when celery isn't available so
# the collection error doesn't redden the build (the lesson from the spike
# PR #83 / fix PR #86).
pytest.importorskip("celery", reason="celery is an opt-in extra")

from cascade import tasks  # noqa: E402  (skip-gated import)
from cascade.celery_app import app  # noqa: E402
from cascade.npu_worker import DraftResult, RouteResult  # noqa: E402


@pytest.fixture
def eager():
    """Run Celery tasks inline so no broker is required."""
    prev = app.conf.task_always_eager
    app.conf.task_always_eager = True
    try:
        yield
    finally:
        app.conf.task_always_eager = prev


@pytest.fixture(autouse=True)
def _reset_npu_cache():
    """`_get_npu` is `@cache`d for resident-worker semantics; clear it between
    tests so a fake-worker patch from one test never leaks into the next."""
    tasks._get_npu.cache_clear()
    yield
    tasks._get_npu.cache_clear()


def _fake_worker(mocker, *, route_ret=None, draft_ret=None):
    """Patch `make_npu_worker` to return a Mock with `.route` / `.draft`
    methods that return the supplied dataclasses. Sidesteps every real
    OpenVINO call."""
    fake = mocker.Mock()
    fake.route.return_value = route_ret or RouteResult(
        difficulty=0.42, category="standard", latency_s=0.05, device="NPU")
    fake.draft.return_value = draft_ret or DraftResult(
        text="def f():\n    return 1", latency_s=0.10, device="NPU")
    fake.device = "NPU"
    mocker.patch("cascade.tasks.make_npu_worker", return_value=fake)
    return fake


def test_route_returns_canonical_shape(mocker):
    """route() returns {available, difficulty, category, latency_s, device}
    matching mcp_servers/npu.py:route's contract -- with difficulty rounded
    to 3dp and latency_s to 2dp."""
    _fake_worker(mocker, route_ret=RouteResult(
        difficulty=0.42357, category="standard", latency_s=0.04999, device="NPU"))
    r = tasks.route(prompt="reverse a string")
    assert r == {"available": True, "difficulty": 0.424,
                 "category": "standard", "latency_s": 0.05, "device": "NPU", "seed": 0}


def test_draft_returns_canonical_shape(mocker):
    """draft() returns {available, text, latency_s, device} matching
    mcp_servers/npu.py:draft's contract. No `model`, no `tokens_per_s` (those
    are GPU-tier fields)."""
    _fake_worker(mocker, draft_ret=DraftResult(
        text="```python\nprint('hi')\n```", latency_s=0.103, device="NPU"))
    r = tasks.draft(prompt="say hi")
    assert r == {"available": True, "text": "```python\nprint('hi')\n```",
                 "latency_s": 0.10, "device": "NPU", "seed": 0}


def test_route_hands_off_when_compile_fails(mocker):
    """When `make_npu_worker` raises (no `accel` extra, NPU hardware missing),
    route() returns the standard `available:false` hand-off with the
    exception class + message in `reason`. NEVER raises -- the cascade treats
    a down tier as a status, not an error (charter inv. 5)."""
    mocker.patch("cascade.tasks.make_npu_worker",
                 side_effect=RuntimeError("openvino_genai missing"))
    r = tasks.route(prompt="hello")
    assert r == {"available": False,
                 "reason": "RuntimeError: openvino_genai missing"}


def test_draft_hands_off_when_compile_fails(mocker):
    """Same hand-off contract for draft()."""
    mocker.patch("cascade.tasks.make_npu_worker",
                 side_effect=RuntimeError("npu probe failed"))
    r = tasks.draft(prompt="hello")
    assert r == {"available": False,
                 "reason": "RuntimeError: npu probe failed"}


def test_route_passes_max_tokens_through_to_draft(mocker):
    """draft() forwards `max_tokens` to the worker as `max_new_tokens`. This
    is the lever the Canvas envelope will use when the chain step wants to
    cap a draft's length (e.g. the Phase 2 `low_power` topology)."""
    fake = _fake_worker(mocker)
    tasks.draft(prompt="x", max_tokens=64)
    fake.draft.assert_called_once_with("x", max_new_tokens=64)


def test_get_npu_compiles_once_per_process(mocker):
    """The ~9-21s OpenVINO compile must run AT MOST ONCE per process; @cache
    on _get_npu pins the worker for the lifetime of the Celery worker
    (matches `worker_max_tasks_per_child=0`). 30 consecutive route() calls
    => one compile, not thirty."""
    counter = mocker.Mock()

    def _build():
        counter()
        fake = mocker.Mock()
        fake.route.return_value = RouteResult(
            difficulty=0.5, category="standard", latency_s=0.05, device="NPU")
        fake.device = "NPU"
        return fake

    mocker.patch("cascade.tasks.make_npu_worker", side_effect=_build)
    for _ in range(30):
        tasks.route(prompt="x")
    assert counter.call_count == 1


def test_route_task_eager_round_trips_shape(eager, mocker):
    """The Celery wrapper runs route() inline under `task_always_eager` and
    returns the same dict (no envelope wrapping, no shape mutation). Proves
    the @app.task(name=..., queue=...) decorator is transparent."""
    _fake_worker(mocker)
    r = tasks.route_task.apply(args=["hello world"]).get()
    assert r == {"available": True, "difficulty": 0.42,
                 "category": "standard", "latency_s": 0.05, "device": "NPU", "seed": 0}


def test_draft_task_eager_round_trips_shape(eager, mocker):
    """Same transparency contract for draft_task."""
    _fake_worker(mocker)
    r = tasks.draft_task.apply(args=["write add(a,b)"]).get()
    assert r == {"available": True, "text": "def f():\n    return 1",
                 "latency_s": 0.10, "device": "NPU", "seed": 0}


def test_draft_task_forwards_max_tokens(eager, mocker):
    """draft_task accepts the same `max_tokens` arg as draft() and routes it
    through. Pinned so a future refactor doesn't silently drop it (the
    `low_power` topology depends on it)."""
    fake = _fake_worker(mocker)
    tasks.draft_task.apply(args=["x", 96]).get()
    fake.draft.assert_called_once_with("x", max_new_tokens=96)


def test_route_task_is_queue_pinned_to_npu():
    """The Celery task's queue routing must be `npu` -- in multi-box mode the
    Intel host's worker subscribes to that queue only, so a route dispatched
    to any other queue would run on the wrong hardware. The task name is the
    same stable contract (`mesh.route`) the topologies module will reference."""
    assert tasks.route_task.queue == "npu"
    assert tasks.draft_task.queue == "npu"
    assert tasks.route_task.name == "mesh.route"
    assert tasks.draft_task.name == "mesh.draft"
