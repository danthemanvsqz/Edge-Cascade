"""Eager-mode tests for the balanced Canvas chain (Slice 3) -- the residue
that no other layer covers.

`cascade.topologies_canvas`, `cascade.canvas_client`, and `cascade.tasks` are
all in `[tool.coverage.run] omit` (the celery substrate is live-validated, not
unit-cov'd), so the mocks here earn ZERO coverage. The npu-resolve /
gpu-escalate / cap / syntax-fallback parity cases that used to live here were
pruned as no-payoff duplicates -- they are proven more robustly elsewhere with
fewer or no mocks:
  * test_canvas_spike      -- OWNS the cap invariant (mocks only the two seams
                              the retry loop calls; asserts call counts).
  * test_canvas_balanced_integration -- resolve/escalate/cap over a REAL broker
                              (embedded worker), mocking only the model boundary.
  * test_canvas_live_behavior        -- the same behaviors on the LIVE mesh,
                              NO mocks at all.

What remains here is only what those layers can't reach without extra
infrastructure: the cloud-enabled escalation (integration has no cloud worker;
live runs cloud-disabled), the win/lose log classification, the skip-draft
decision, the client Outcome shape, and the model registry/alias contracts.
Mock at the tier-op boundary (`tasks.*`) the chain calls -- never deeper.
"""
from __future__ import annotations

import logging

import pytest

pytest.importorskip("celery", reason="celery is an opt-in extra")

from cascade import canvas_client, mesh, tasks  # noqa: E402
from cascade.celery_app import app  # noqa: E402
from cascade.config import CONFIG  # noqa: E402

CAP = CONFIG.repair_cap


@pytest.fixture
def eager():
    """Run tasks inline so the chain (and gpu_solve_task's retry loop) execute
    synchronously in-process. Eager mode is the spike's proven path."""
    prev_eager = app.conf.task_always_eager
    prev_prop = app.conf.task_eager_propagates
    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = False
    try:
        yield
    finally:
        app.conf.task_always_eager = prev_eager
        app.conf.task_eager_propagates = prev_prop


@pytest.fixture(autouse=True)
def _reset_caches():
    """Slice 1's `_get_npu` cache + a clean `CONFIG.enable_cloud` between tests."""
    tasks._get_npu.cache_clear()
    yield
    tasks._get_npu.cache_clear()


def _route(mocker, difficulty=0.42, category="standard"):
    mocker.patch(
        "cascade.tasks.route",
        return_value={"available": True, "difficulty": difficulty,
                      "category": category, "latency_s": 0.05,
                      "device": "NPU"},
    )


def _draft(mocker, text="```python\nnpu draft\n```"):
    mocker.patch(
        "cascade.tasks.draft",
        return_value={"available": True, "text": text,
                      "latency_s": 0.10, "device": "NPU"},
    )


def _verify(mocker, sequence):
    """Sequence of pass/fail booleans for the verifier across calls. Empty
    failures on PASS, one structured failure on FAIL."""
    mocker.patch(
        "cascade.tasks.verify_functional",
        side_effect=[
            {"passed": p, "failures": [] if p else [{"expr": "x"}]}
            for p in sequence
        ],
    )


def _generate(mocker, text="```python\ngpu generate\n```"):
    """gpu_solve_task internally calls `cascade.tasks.generate` -- mocking
    that one fn covers the GPU repair loop entirely."""
    mocker.patch(
        "cascade.tasks.generate",
        return_value={"available": True, "text": text, "model": "fake",
                      "tokens_per_s": 0.0, "latency_s": 0.0},
    )


def _cloud(mocker, *, available=True, text="```python\ncloud answer\n```"):
    mocker.patch(
        "cascade.tasks.cloud_generate",
        return_value={"available": available, "text": text,
                      "model": "claude-opus-4-7", "latency_s": 1.0,
                      "input_tokens": 0, "output_tokens": 0,
                      "est_cost_usd": 0.0,
                      "reason": "ok" if available else "disabled"},
    )


# ---------------------------------------------------------------------------
# Cloud escalation -- the ONE balanced-chain path no other layer covers
# (test_canvas_balanced_integration has no cloud worker; the live suite runs
# cloud-disabled). The npu-resolve / gpu-escalate / cap parity cases that used
# to live here were pruned as no-payoff duplicates: this module is in
# [tool.coverage.run] omit (zero coverage), test_canvas_spike OWNS the cap with
# tighter seam mocks, and the integration + live suites prove resolve/escalate/
# cap with model-boundary-only / zero mocks.
# ---------------------------------------------------------------------------


def test_balanced_chain_escalates_to_cloud_when_enabled(eager, mocker):
    """Case 4: NPU + GPU all fail; cloud enabled + available => final_tier='cloud'."""
    mocker.patch("cascade.topologies_canvas.CONFIG",
                 mocker.Mock(spec=CONFIG, enable_cloud=True))
    _route(mocker)
    _draft(mocker, text="bad")
    _verify(mocker, [False] + [False] * (CAP + 1))
    _generate(mocker)
    _cloud(mocker, available=True, text="```python\ncloud fix\n```")
    outcome = canvas_client.solve_balanced_canvas("hard task", dsl="DSL")
    assert outcome.final_tier == "cloud"
    assert outcome.resolved is True
    assert outcome.capped is False
    assert outcome.answer == "```python\ncloud fix\n```"


# ---------------------------------------------------------------------------
# Trace + envelope details (smaller pins for the chain composition).
# ---------------------------------------------------------------------------


def test_balanced_chain_trace_records_each_step(eager, mocker):
    """Trace lines confirm every chain step ran in order (route -> draft ->
    gate -> gpu -> [cloud]). Resolved-shortcut steps don't append."""
    _route(mocker)
    _draft(mocker)
    _verify(mocker, [True])
    outcome = canvas_client.solve_balanced_canvas("test", dsl="DSL")
    joined = " | ".join(outcome.trace)
    assert "route difficulty=" in joined
    assert "npu draft ->" in joined
    assert "npu gate PASS" in joined
    # Resolved at NPU; gpu/cloud steps are pass-throughs and don't trace.
    assert "gpu solve" not in joined
    assert "cloud" not in joined


def test_balanced_done_logs_win_on_local_resolution(eager, mocker, caplog):
    """End-of-pipe `_balanced_done` classifies an NPU/GPU resolution as a WIN:
    a `done: WIN` trace entry + an INFO win log line. The marker returns env
    unchanged, so the outcome itself is identical to the no-done chain."""
    _route(mocker)
    _draft(mocker, text="```python\nrev\n```")
    _verify(mocker, [True])
    with caplog.at_level(logging.INFO, logger="cascade.topologies_canvas"):
        outcome = canvas_client.solve_balanced_canvas("reverse", dsl="DSL")
    assert outcome.final_tier == "npu"
    assert outcome.resolved is True
    assert outcome.trace[-1] == "done: WIN (local @ npu)"
    assert any("cascade WIN" in r.message for r in caplog.records)


def test_balanced_done_logs_lose_on_capped(eager, mocker, caplog):
    """A capped->tier3 run is a LOSS for the local pipe: `_balanced_done`
    emits a `done: LOSE` trace entry + an INFO lose log line."""
    mocker.patch("cascade.topologies_canvas.CONFIG",
                 mocker.Mock(enable_cloud=False))
    _route(mocker)
    _draft(mocker, text="bad")
    _verify(mocker, [False] + [False] * (CAP + 1))
    _generate(mocker)
    with caplog.at_level(logging.INFO, logger="cascade.topologies_canvas"):
        outcome = canvas_client.solve_balanced_canvas("hard task", dsl="DSL")
    assert outcome.final_tier == "capped->tier3"
    assert outcome.capped is True
    assert outcome.trace[-1] == "done: LOSE (-> capped->tier3)"
    assert any("cascade LOSE" in r.message for r in caplog.records)


def test_balanced_chain_skips_draft_above_difficulty_threshold(eager, mocker):
    """When the router scores difficulty >= `skip_draft_above` AND the prompt
    clears the length gate (BACKLOG #8), the NPU draft is skipped entirely (the
    2026-05-20 finding: 1.5B drafts never win on hard tasks). The chain
    proceeds straight to GPU without a draft to repair on. A SHORT hard prompt
    is no longer skipped -- the router over-rates short input."""
    from cascade import topologies as topo_module
    from cascade.config import CONFIG
    balanced = topo_module.get("balanced")
    assert balanced.skip_draft_above is not None  # contract pin
    _route(mocker, difficulty=balanced.skip_draft_above + 0.01)
    draft = mocker.patch("cascade.tasks.draft")
    _verify(mocker, [True])
    _generate(mocker, text="```python\ngpu fresh\n```")
    long_hard = "implement a hard task " * (CONFIG.skip_draft_min_chars // 20)
    outcome = canvas_client.solve_balanced_canvas(long_hard, dsl="DSL")
    draft.assert_not_called()
    assert outcome.final_tier == "gpu"
    assert outcome.answer == "```python\ngpu fresh\n```"


def test_qwen14b_is_registered_at_import(eager):
    """cascade.tasks registers `qwen14b` at module import (Slice 3b) so
    `chain(model.swap.s("qwen14b"), ...)` resolves to a known factory.
    Pinned because Slice 3c will rely on a non-empty registry to
    differentiate the swap-happens path from swap-noops.

    Tests don't assume initial residency state -- prior tests in this
    file may have driven a balanced chain that already loaded qwen14b
    via the swap step. The registry presence is the load-bearing
    contract; residency depends on which tests ran already."""
    from cascade import model_swap
    assert "qwen14b" in model_swap._FACTORIES
    _, footprint = model_swap._FACTORIES["qwen14b"]
    assert footprint > 0


def test_balanced_chain_dispatches_swap_before_gpu_solve(eager, mocker):
    """The chain step `_balanced_gpu_solve` MUST prepend
    `model.swap_task("qwen14b")` so the arbiter loads the model before
    gpu_solve_task runs. Pinned by spying on `model_swap.swap` and
    driving a full balanced chain that escalates to GPU.

    Without this guarantee Slice 3c's multi-model swap wouldn't fire on
    a GPU escalation."""
    from cascade import model_swap
    spy = mocker.spy(model_swap, "swap")
    _route(mocker)
    _draft(mocker, text="```python\nbad draft\n```")
    _verify(mocker, [False, True])  # NPU fail -> GPU first attempt pass
    _generate(mocker, text="```python\ngpu fix\n```")
    canvas_client.solve_balanced_canvas("write a fn", dsl="DSL")
    # The swap was called for qwen14b at least once during the chain.
    swap_calls = [c for c in spy.call_args_list if c.args == ("qwen14b",)]
    assert len(swap_calls) >= 1


def test_generate_alias_preserves_callers():
    """The Slice-3b rename keeps `cascade.tasks.generate` bound to the
    new `generate_qwen14b` for one release. Existing callers that
    reach for `tasks.generate(...)` (notably canvas_spike's
    gpu_solve_task at the @recorded layer) keep working with no edit."""
    from cascade import tasks
    assert tasks.generate is tasks.generate_qwen14b
    # Same Celery task NAME stays bound under both legacy + new
    # attribute names, so a worker that registered `mesh.generate` keeps
    # working AND the new `mesh.generate_qwen14b` is also dispatchable.
    assert tasks.generate_task.name == "mesh.generate"
    assert tasks.generate_qwen14b_task.name == "mesh.generate_qwen14b"


# ---------------------------------------------------------------------------
# Slice 3c -- second model (qwen7b) registered alongside qwen14b.
# ---------------------------------------------------------------------------


def test_qwen7b_registered_alongside_qwen14b():
    """Slice 3c registers qwen7b as a second model. Both names live in
    the arbiter so a chain or topology can select either at dispatch."""
    from cascade import model_swap, tasks  # noqa: F401  (import side effect)
    names = model_swap.registered()
    assert "qwen14b" in names
    assert "qwen7b" in names


def test_generate_qwen7b_hands_off_when_not_resident():
    """`generate_qwen7b` consults the arbiter and returns the standard
    `available:false` hand-off when qwen7b isn't loaded. The chain MUST
    chain `model.swap.s("qwen7b")` before dispatching this task; the
    hand-off makes a misconfigured chain fail LOUD instead of silently
    falling back to qwen14b."""
    from cascade import model_swap, tasks
    # Ensure qwen7b isn't resident -- safely clear from any prior test.
    if "qwen7b" in model_swap._resident:
        del model_swap._resident["qwen7b"]
        if "qwen7b" in model_swap._lru_order:
            model_swap._lru_order.remove("qwen7b")
    out = tasks.generate_qwen7b("hello")
    assert out["available"] is False
    assert "swap not invoked" in out["text"]
    assert out["model"] == "qwen2.5-coder:7b"


def test_generate_qwen7b_uses_resident_worker(mocker):
    """When qwen7b IS resident (the chain dispatched
    `model.swap.s("qwen7b")` before this task), generate_qwen7b
    dispatches to that worker's generate method. Pinned via a stub
    worker; the arbiter contract is tested separately in
    test_model_swap.py."""
    from cascade import model_swap, tasks
    from cascade.llama_worker import LlamaResult
    fake_worker = mocker.Mock()
    fake_worker.generate.return_value = LlamaResult(
        text="```python\nqwen7b answer\n```",
        latency_s=0.5, tokens_per_s=42.0,
        model="qwen2.5-coder:7b", available=True,
    )
    # Inject the fake worker as resident (bypass swap for unit-test
    # isolation -- the swap_cycle test in test_model_swap.py covers
    # the arbiter contract end-to-end).
    model_swap._resident["qwen7b"] = model_swap.ModelHandle(
        name="qwen7b", footprint_mb=5500, handle=fake_worker)
    try:
        out = tasks.generate_qwen7b("write hello")
        assert out["available"] is True
        assert out["text"] == "```python\nqwen7b answer\n```"
        assert out["model"] == "qwen2.5-coder:7b"
        assert out["tokens_per_s"] == 42.0
        fake_worker.generate.assert_called_once_with("write hello", max_new_tokens=None)
    finally:
        # Cleanup so other tests aren't affected.
        del model_swap._resident["qwen7b"]


def test_qwen7b_factory_uses_make_llama_worker(mocker):
    """The qwen7b factory wraps `make_llama_worker("qwen2.5-coder:7b")`.
    Pinned so a refactor doesn't accidentally drop the model id, and
    so the patch-via-name pattern (used by tests that exercise swap
    events without loading a real model) keeps working."""
    fake_worker = mocker.Mock()
    mocker.patch("cascade.tasks._make_qwen7b_worker", return_value=fake_worker)
    # Via the registered factory (this is what swap_task invokes):
    from cascade import model_swap
    factory, footprint = model_swap._FACTORIES["qwen7b"]
    out = factory()
    assert out is fake_worker
    assert footprint == 5500  # the conservative 4.7GB + 1GB KV estimate


def test_qwen7b_task_routes_to_gpu_queue():
    """`generate_qwen7b_task` ships on the `gpu` queue alongside
    `generate_qwen14b_task`. Pinned so a multi-box layout (Slice 5)
    can hardware-pin both 7b and 14b workers to the RTX box's `gpu`
    queue without per-task config."""
    from cascade import tasks
    assert tasks.generate_qwen7b_task.queue == "gpu"
    assert tasks.generate_qwen7b_task.name == "mesh.generate_qwen7b"


def test_canvas_client_returns_mesh_outcome_shape(eager, mocker):
    """The client's Outcome is the SAME `mesh.Outcome` dataclass `mesh.solve`
    returns -- callers can swap `cascade.canvas_client.solve_balanced_canvas`
    in for `mesh.solve(query, "balanced", ops)` without changing consumption
    code. Pinned because Slice 4's findings doc claims this shape parity."""
    _route(mocker)
    _draft(mocker)
    _verify(mocker, [True])
    outcome = canvas_client.solve_balanced_canvas("x", dsl="DSL")
    assert isinstance(outcome, mesh.Outcome)
    # All Outcome fields populated.
    assert outcome.topology == "balanced"
    assert outcome.difficulty == 0.42
    assert isinstance(outcome.trace, tuple)
    assert len(outcome.trace) > 0
