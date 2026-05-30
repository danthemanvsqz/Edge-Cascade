"""Shared pytest fixtures for the cascade test suite.

Phase 2 Slice 4 adds the **integration** fixtures: a real Celery worker
embedded in the test process, talking to an in-memory broker. The unit
tests in test_canvas_balanced.py etc. continue to use `task_always_eager`
(fast, no broker); the new test_canvas_balanced_integration.py uses these
fixtures to drive the FULL dispatch path -- task registration, queue
routing, broker handoff, `.get()` -- exactly the path PR #91's three live
bugs hid in. Eager mode skips the broker; embedded worker doesn't.

Uses `celery.contrib.testing.worker.start_worker` directly (the same
underlying mechanism `pytest-celery` wraps with Docker fixtures, but
without the Docker/Redis dependency -- we run in-memory).

The fixtures are session-scoped so the embedded worker boots ONCE for the
integration test module. Per-test isolation is provided by the existing
`_reset_swap_state` autouse fixture in test_model_swap.py + per-test
`mocker.patch` calls in test_canvas_balanced_integration.py.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_cascade_rec(tmp_path, monkeypatch):
    """Redirect the Canvas client's cascade-outcome `.rec` lane to a tmp file so
    any test driving `solve_*_canvas` doesn't append telemetry to the real
    `runs/cascade.rec`. Lazy + CI-safe: only patches when `canvas_client` is
    already imported (i.e. a canvas test module was collected); a celery-less CI
    run never imports it, so this is a no-op there."""
    import sys
    cc = sys.modules.get("cascade.canvas_client")
    if cc is not None:
        monkeypatch.setattr(
            cc, "_cascade_rec_path", lambda: tmp_path / "cascade.rec",
        )


@pytest.fixture(scope="session")
def celery_integration_app():
    """Override `cascade.celery_app.app` for the integration test
    session: `memory://` broker + `cache+memory://` backend so no Redis
    is needed. Restores the prior URLs after the session ends so other
    test modules / live runs see the production config.

    `task_always_eager` is FORCED OFF because the integration tests'
    whole point is to exercise broker dispatch (which eager skips).
    Unit tests that need eager mode set it themselves per-test."""
    pytest.importorskip("celery", reason="celery is an opt-in extra")
    from cascade.celery_app import app
    prev = {
        "broker_url": app.conf.broker_url,
        "result_backend": app.conf.result_backend,
        "task_always_eager": app.conf.task_always_eager,
        "task_eager_propagates": app.conf.task_eager_propagates,
    }
    app.conf.broker_url = "memory://"
    app.conf.result_backend = "cache+memory://"
    app.conf.task_always_eager = False
    app.conf.task_eager_propagates = False
    try:
        yield app
    finally:
        for k, v in prev.items():
            setattr(app.conf, k, v)


@pytest.fixture(scope="session")
def celery_integration_worker(celery_integration_app):
    """Embedded Celery worker running in the test process, subscribing
    to the queues the canvas chain uses (npu, gpu, verify). Notably
    NOT subscribed to `cloud` -- the spend invariant from PR #88
    relies on no worker consuming that queue, so the cloud_generate_task
    structurally never runs. Tests that need cloud explicitly start a
    second worker subscribing to `cloud`.

    Yields the started worker context so tests can introspect
    registered tasks if needed."""
    from celery.contrib.testing.worker import start_worker
    # `perform_ping_check=False` skips a probe that requires an extra
    # ping task to be registered (it's part of celery.contrib.testing
    # but not in our `include` list); we don't need it for our chains.
    with start_worker(
        celery_integration_app,
        queues=("npu", "gpu", "verify"),
        perform_ping_check=False,
        shutdown_timeout=30,
    ) as worker:
        yield worker
