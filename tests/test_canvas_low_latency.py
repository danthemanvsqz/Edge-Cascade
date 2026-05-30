"""Eager-mode tests for the low_latency Canvas chord (Slice 6b).

The chord races the NPU draft against the GPU generate (a `group`) and its
callback `_pick_first_verified` resolves to the first candidate (npu preferred)
that passes the gate, or caps to Tier-3 on a double miss. These run EAGERLY
(`task_always_eager`) so the group + callback execute inline; the underlying
tier ops (`tasks.draft`, `tasks.generate_qwen14b`, `tasks.verify_functional`)
are scripted spies, so every assertion is about CONTROL FLOW: which arm wins,
that BOTH arms ran (the race), and the cap-to-Tier-3 hand-off.

`cascade.topologies_canvas` / `cascade.canvas_client` / `cascade.tasks` are in
`[tool.coverage.run] omit` (the celery substrate is live-validated, not unit
cov'd), so these exercise the chord end-to-end without counting toward the
100% gate.
"""
from __future__ import annotations

import pytest

pytest.importorskip("celery", reason="celery is an opt-in extra")

from cascade import canvas_client, mesh, tasks  # noqa: E402
from cascade.celery_app import app  # noqa: E402


@pytest.fixture
def eager():
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
    tasks._get_npu.cache_clear()
    yield
    tasks._get_npu.cache_clear()


def _draft(mocker, *, text="```python\nnpu draft\n```", available=True):
    return mocker.patch(
        "cascade.tasks.draft",
        return_value={"available": available, "text": text,
                      "latency_s": 0.10, "device": "NPU"},
    )


def _generate(mocker, *, text="```python\ngpu generate\n```", available=True):
    # generate_qwen14b_task -> generate_qwen14b (NOT the `generate` alias).
    return mocker.patch(
        "cascade.tasks.generate_qwen14b",
        return_value={"available": available, "text": text, "model": "fake",
                      "tokens_per_s": 0.0, "latency_s": 0.0},
    )


def _verify(mocker, sequence):
    """Functional-gate verdicts in CALL order. The callback gates npu first,
    then gpu -- so sequence is [npu_verdict, gpu_verdict] (npu omitted when the
    NPU arm is unavailable, since an unavailable candidate is never gated)."""
    return mocker.patch(
        "cascade.tasks.verify_functional",
        side_effect=[
            {"passed": p, "failures": [] if p else [{"expr": "x"}]}
            for p in sequence
        ],
    )


# ---------------------------------------------------------------------------
# Winner selection.
# ---------------------------------------------------------------------------


def test_low_latency_npu_wins_when_it_verifies(eager, mocker):
    """NPU draft verifies => final_tier='npu' (cheapest-first preference), even
    though the GPU arm also ran. The GPU generate is NOT gated (npu short-
    circuits the callback) but it WAS executed (the race)."""
    draft = _draft(mocker, text="```python\nrev\n```")
    gen = _generate(mocker)
    verify = _verify(mocker, [True])  # npu PASS
    outcome = canvas_client.solve_low_latency_canvas("reverse", dsl="DSL")
    assert isinstance(outcome, mesh.Outcome)
    assert outcome.final_tier == "npu"
    assert outcome.resolved is True
    assert outcome.answer == "```python\nrev\n```"
    # The race: BOTH arms executed, even though only npu was gated.
    assert draft.called
    assert gen.called
    assert verify.call_count == 1  # npu only; gpu short-circuited


def test_low_latency_gpu_wins_when_npu_fails(eager, mocker):
    """NPU draft fails the gate; GPU candidate verifies => final_tier='gpu'.
    No repair loop -- the speculative GPU result is taken as-is."""
    _draft(mocker, text="```python\nbad\n```")
    _generate(mocker, text="```python\ngpu fix\n```")
    _verify(mocker, [False, True])  # npu FAIL, gpu PASS
    outcome = canvas_client.solve_low_latency_canvas("write a fn", dsl="DSL")
    assert outcome.final_tier == "gpu"
    assert outcome.resolved is True
    assert outcome.answer == "```python\ngpu fix\n```"


def test_low_latency_caps_when_both_miss(eager, mocker):
    """Both raced candidates fail the gate => capped->tier3. low_latency does
    NOT fall into the bounded repair loop (that's balanced) -- a double miss
    hands straight to Tier-3."""
    _draft(mocker, text="```python\nbad\n```")
    _generate(mocker, text="```python\nalso bad\n```")
    _verify(mocker, [False, False])
    outcome = canvas_client.solve_low_latency_canvas("hard", dsl="DSL")
    assert outcome.final_tier == "capped->tier3"
    assert outcome.capped is True
    assert outcome.resolved is False
    assert outcome.answer is None


def test_low_latency_uses_gpu_when_npu_unavailable(eager, mocker):
    """NPU arm unavailable (no draft text) => skipped without gating; the GPU
    candidate is gated and wins. The verify sequence has ONE verdict (gpu),
    proving the unavailable npu arm was never sent to the gate."""
    _draft(mocker, available=False, text="")
    _generate(mocker, text="```python\ngpu only\n```")
    verify = _verify(mocker, [True])  # gpu only
    outcome = canvas_client.solve_low_latency_canvas("x", dsl="DSL")
    assert outcome.final_tier == "gpu"
    assert outcome.resolved is True
    assert verify.call_count == 1


# ---------------------------------------------------------------------------
# dsl=None syntax-gate fallback (parity with the pipe / balanced path).
# ---------------------------------------------------------------------------


def test_low_latency_dsl_none_uses_syntax_gate(eager, mocker):
    """dsl=None => the SYNTAX gate (cascade.verifier.verify), not
    verify_functional -- same parity contract the balanced path got in Slice 4.
    A parseable NPU draft wins without verify_functional being called."""
    _draft(mocker, text="```python\ndef f():\n    return 1\n```")
    _generate(mocker)
    verify_func = mocker.patch("cascade.tasks.verify_functional")
    outcome = canvas_client.solve_low_latency_canvas("reverse")  # dsl=None
    assert outcome.final_tier == "npu"
    assert outcome.resolved is True
    verify_func.assert_not_called()


def test_low_latency_dsl_none_caps_on_nonparseable(eager, mocker):
    """dsl=None: neither candidate is a parseable Python block => syntax gate
    fails both => capped->tier3, still without verify_functional."""
    _draft(mocker, text="not python")
    _generate(mocker, text="also not python")
    verify_func = mocker.patch("cascade.tasks.verify_functional")
    outcome = canvas_client.solve_low_latency_canvas("x")  # dsl=None
    assert outcome.final_tier == "capped->tier3"
    assert outcome.capped is True
    verify_func.assert_not_called()


# ---------------------------------------------------------------------------
# Shape + trace.
# ---------------------------------------------------------------------------


def test_low_latency_returns_mesh_outcome_shape(eager, mocker):
    """The client returns the SAME mesh.Outcome dataclass as balanced/pipe, so
    a caller swaps topologies by entry point, not by reshaping output."""
    _draft(mocker, text="```python\nok\n```")
    _generate(mocker)
    _verify(mocker, [True])
    outcome = canvas_client.solve_low_latency_canvas("x", dsl="DSL")
    assert isinstance(outcome, mesh.Outcome)
    assert outcome.topology == "low_latency"
    assert isinstance(outcome.trace, tuple)
    joined = " | ".join(outcome.trace)
    assert "low_latency:" in joined  # the callback traced its decision
