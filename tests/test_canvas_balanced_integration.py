"""Integration tests for the balanced Canvas chain -- Phase 2 Slice 4.

These tests drive the FULL Celery dispatch path: real worker (embedded),
in-memory broker (`memory://`), `.apply_async().get()` round-trip. The
unit tests in test_canvas_balanced.py mock at the function boundary; the
integration tests mock only at the *model* boundary (llama-cpp / Ollama)
so task registration, queue routing, chain composition, and `.get()`
semantics all run for real.

The three regression cases for PR #91's live-broker bugs are the
load-bearing tests in this file. Each would have failed under the
pre-#91 code:

1. **NotRegistered regression** -- the chain step tasks must be
   registered on the embedded worker. Pre-#91 bug: `celery_app.include`
   listed only `cascade.tasks`, missing `cascade.canvas_spike` and
   `cascade.topologies_canvas`, so a chain dispatch yielded
   `celery.exceptions.NotRegistered: 'mesh.balanced._route'`. Eager mode
   skips the broker so unit tests passed.
2. **Gate divergence regression** -- a no-DSL run on a parseable NPU
   draft must NOT cap to Tier-3. Pre-#91 bug: the chain used the
   functional gate which returns `passed:false` when `dsl=None`, so
   every no-DSL run capped. Fixed by syntax-fallback in
   `_balanced_draft_gate`.
3. **Cloud-queue deadlock regression** -- the chain must complete
   (resolve OR cap) within a timeout when no worker subscribes to
   `cloud`. Pre-#91 bug: `_balanced_cloud.queue="cloud"` accidentally
   routed the chain's terminal step to the unconsumed cloud queue, so
   `.get()` blocked forever. Fixed by moving the chain step to `gpu`
   queue (the cloud_generate TASK stays on `cloud` for the spend
   invariant).
"""
from __future__ import annotations

import pytest

pytest.importorskip("celery", reason="celery is an opt-in extra")

from cascade import canvas_client, mesh  # noqa: E402

# All tests in this module require the embedded Celery worker fixture and
# are opt-in via `pytest -m integration`. Default `pytest` skips them
# (Windows teardown hang on start_worker shutdown -- see pyproject.toml's
# [tool.pytest.ini_options] addopts).
pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _mock_tier_ops(mocker):
    """Mock at the tier-op boundary so no real NPU/GPU/cloud call fires.
    The TASK dispatch still goes through the embedded worker; only the
    model call is faked. Matches the design's "mock at the
    llama-cpp-python boundary" testing strategy from
    docs/DESIGN-celery-phase2.md."""
    # NPU route: trivial standard task by default.
    mocker.patch(
        "cascade.tasks.route",
        return_value={"available": True, "difficulty": 0.42,
                      "category": "standard", "latency_s": 0.01,
                      "device": "NPU"},
    )
    # NPU draft: parseable Python block by default (so syntax gate passes
    # under the no-DSL path).
    mocker.patch(
        "cascade.tasks.draft",
        return_value={"available": True,
                      "text": "```python\ndef f(): return 1\n```",
                      "latency_s": 0.01, "device": "NPU"},
    )
    # GPU generate: parseable Python block (gpu_solve_task syntax-gates
    # this under no-DSL).
    mocker.patch(
        "cascade.tasks.generate",
        return_value={"available": True,
                      "text": "```python\ndef f(): return 1\n```",
                      "model": "fake-14b", "tokens_per_s": 0.0,
                      "latency_s": 0.01},
    )


# ---------------------------------------------------------------------------
# The three PR #91 regression tests.
# ---------------------------------------------------------------------------


def test_pr91_regression_all_chain_tasks_registered_on_worker(
    celery_integration_worker,
):
    """Pre-#91 bug: `celery_app.include` was `["cascade.tasks"]` only,
    so the chain-step tasks from `cascade.topologies_canvas` and the
    spike's `cascade.canvas_spike.gpu_solve_task` weren't loaded at the
    worker. First chain dispatch yielded
    `NotRegistered: 'mesh.balanced._route'`. The unit tests passed in
    eager mode (which skips the broker + uses the client's task
    registry).

    Fixed in #91 by extending `celery_app.include` to all three
    modules. Pinned here at the broker layer: the embedded worker
    must know about every task the balanced chain dispatches."""
    registered = set(celery_integration_worker.app.tasks.keys())
    # Chain steps (cascade.topologies_canvas) -- these are the ones the
    # pre-#91 bug missed.
    expected_chain_steps = {
        "mesh.balanced._route",
        "mesh.balanced._draft",
        "mesh.balanced._draft_gate",
        "mesh.balanced._gpu_solve",
        "mesh.balanced._merge_gpu",
        "mesh.balanced._cloud",
    }
    missing = expected_chain_steps - registered
    assert not missing, (
        f"Missing chain-step tasks on worker: {missing}. "
        f"This is the pre-#91 NotRegistered bug regressing -- "
        f"cascade.celery_app.include must list cascade.topologies_canvas."
    )
    # The spike's bounded-retry task (cascade.canvas_spike) -- second
    # half of the pre-#91 bug.
    assert "cascade.canvas_spike.gpu_solve_task" in registered, (
        "Spike's gpu_solve_task missing -- cascade.celery_app.include "
        "must list cascade.canvas_spike."
    )


def test_pr91_regression_no_dsl_uses_syntax_gate_not_functional(
    celery_integration_worker,
):
    """Pre-#91 bug: a no-DSL Canvas run on a parseable NPU draft FAILED
    the functional gate (`verify_functional` returns `passed:false`
    when `dsl=None`), so EVERY no-DSL run escalated to GPU then capped.

    Fixed in #91 by syntax-fallback in `_balanced_draft_gate`: when
    `dsl=None`, use `cascade.verifier.verify` (parseable-Python check)
    instead. The mocked NPU draft above is a parseable block, so the
    chain MUST resolve at NPU with no GPU calls.

    Pinned here via the embedded worker: under broker dispatch, the
    chain runs the actual gate logic (not a unit-test mock). If
    syntax-fallback ever regresses, this test caps."""
    outcome = canvas_client.solve_balanced_canvas(
        "anything -- the parseable mocked draft above is what matters",
    )
    assert isinstance(outcome, mesh.Outcome)
    assert outcome.final_tier == "npu", (
        f"no-DSL chain on a parseable draft should resolve at NPU "
        f"(syntax gate); got final_tier={outcome.final_tier!r}. This is "
        f"the pre-#91 gate-divergence bug regressing."
    )
    assert outcome.resolved is True
    assert outcome.capped is False


def test_pr91_regression_no_cloud_worker_chain_completes(
    celery_integration_worker, mocker,
):
    """Pre-#91 bug: `_balanced_cloud.queue="cloud"` routed the chain's
    terminal step to the cloud queue. The integration worker fixture
    deliberately does NOT subscribe to `cloud` (the Slice-2 spend
    invariant). With the pre-#91 wiring, the chain's `.get()` blocked
    forever waiting for a worker that didn't exist.

    Fixed in #91 by moving the chain step to `gpu` queue (the
    cloud_generate_task itself stays on `cloud` for the spend
    invariant). Pinned here by driving a chain that REACHES the cloud
    step (NPU fail + GPU fail with cloud-disabled) and asserting it
    completes within a tight timeout.

    The `cloud_generate_task` (the actual paid-API task) STILL routes to
    `cloud` queue and would deadlock if dispatched -- but the chain's
    `_balanced_cloud` step (the envelope manipulator) only inlines a
    direct call when `CONFIG.enable_cloud=True`. We pin the cloud-
    disabled path here; cloud-enabled is its own slice (see Slice 7
    when it un-parks)."""
    # Force NPU draft to be non-parseable (syntax gate FAIL) + GPU
    # likewise so the chain caps -- and assert it caps cleanly without
    # deadlocking on the cloud step.
    mocker.patch(
        "cascade.tasks.draft",
        return_value={"available": True, "text": "not python",
                      "latency_s": 0.01, "device": "NPU"},
    )
    mocker.patch(
        "cascade.tasks.generate",
        return_value={"available": True, "text": "still not python",
                      "model": "fake", "tokens_per_s": 0.0,
                      "latency_s": 0.01},
    )
    # Cloud disabled at CONFIG layer (already the test-environment
    # default, but pin it explicitly so a future config drift doesn't
    # quietly break the test). Use `spec=` so an unintended attr access
    # in _balanced_cloud (e.g., CONFIG.cloud_max_tokens) would FAIL
    # loud instead of returning a truthy Mock that silently masks a bug.
    from cascade.config import CONFIG
    mocker.patch("cascade.topologies_canvas.CONFIG",
                 mocker.Mock(spec=CONFIG, enable_cloud=False))
    outcome = canvas_client.solve_balanced_canvas("trigger cap path")
    assert isinstance(outcome, mesh.Outcome)
    assert outcome.capped is True, (
        f"chain didn't cap -- final_tier={outcome.final_tier!r}; "
        f"the pre-#91 cloud-queue-deadlock bug would have blocked .get() "
        f"here instead of letting the chain complete."
    )
    assert outcome.final_tier == "capped->tier3"


# ---------------------------------------------------------------------------
# Smoke tests beyond the regression cases.
# ---------------------------------------------------------------------------


def test_balanced_chain_resolves_at_npu_under_broker_dispatch(
    celery_integration_worker,
):
    """End-to-end smoke: parseable NPU draft + no DSL -> resolved at NPU
    via syntax gate. Same outcome as the eager-mode unit test, but
    routes through the embedded worker so any broker-level regression
    (serialization, queue routing, registration) surfaces here."""
    outcome = canvas_client.solve_balanced_canvas("hello")
    assert outcome.final_tier == "npu"
    assert outcome.resolved is True


def test_balanced_chain_escalates_to_gpu_under_broker_dispatch(
    celery_integration_worker, mocker,
):
    """NPU draft FAILS syntax gate (non-parseable); GPU first attempt
    PASSES (parseable). Chain must resolve at GPU via the
    `self.replace()` handoff to the spike's gpu_solve_task. Live broker
    exercises chain composition + sub-chain replacement."""
    mocker.patch(
        "cascade.tasks.draft",
        return_value={"available": True, "text": "not parseable",
                      "latency_s": 0.01, "device": "NPU"},
    )
    outcome = canvas_client.solve_balanced_canvas("trigger gpu")
    assert outcome.final_tier == "gpu"
    assert outcome.resolved is True


def test_balanced_chain_holds_cap_under_broker_dispatch(
    celery_integration_worker, mocker,
):
    """Cap invariant on a live broker: NPU + 3 GPU attempts (cap+1)
    all fail the syntax gate => capped->tier3. The spike's
    `gpu_solve_task.max_retries=CONFIG.repair_cap` is what bounds the
    loop. Same invariant as the eager-mode test but routed through the
    embedded worker -- the Slice-4 explicit goal is "would have caught
    PR #91's three bugs", and a cap-leak in the broker path would
    qualify as exactly that class of regression."""
    from cascade.config import CONFIG
    mocker.patch(
        "cascade.tasks.draft",
        return_value={"available": True, "text": "not parseable",
                      "latency_s": 0.01, "device": "NPU"},
    )
    mocker.patch(
        "cascade.tasks.generate",
        return_value={"available": True, "text": "still not parseable",
                      "model": "fake", "tokens_per_s": 0.0,
                      "latency_s": 0.01},
    )
    mocker.patch("cascade.topologies_canvas.CONFIG",
                 mocker.Mock(spec=CONFIG, enable_cloud=False))
    outcome = canvas_client.solve_balanced_canvas("impossible")
    assert outcome.capped is True
    assert outcome.repair_rounds == CONFIG.repair_cap


def test_model_swap_task_dispatchable_via_broker(
    celery_integration_worker,
):
    """Smoke: the new Slice-3a/3b/3c model.swap_task is registered and
    callable via `.apply_async().get()`. Returns the standard
    {loaded, name, was_swap, evicted, vram_used_mb} dict for a known
    registered model (`qwen14b`). No new test for the swap-cycle here
    -- that's covered exhaustively in test_model_swap.py; this is the
    broker-registration pin."""
    from cascade import tasks
    result = tasks.swap_task.apply_async(args=["qwen14b"]).get(timeout=30)
    assert result["loaded"] is True
    assert result["name"] == "qwen14b"


def test_model_status_task_dispatchable_via_broker(
    celery_integration_worker,
):
    """Same smoke for model.status_task. Returns the status snapshot
    {resident, vram_used_mb, vram_free_mb, vram_total_mb}."""
    from cascade import tasks
    result = tasks.status_task.apply_async().get(timeout=30)
    assert "resident" in result
    assert "vram_total_mb" in result
