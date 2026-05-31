"""Cap + telemetry-ordering tests for the Canvas repair-retry spike.

These run the task EAGERLY (`task_always_eager`) so the retry loop executes
inline -- no broker, no Ollama. `tasks.generate` / `tasks.verify_functional`
are replaced with scripted spies (pytest-mock), so every assertion is about
CONTROL FLOW (how many times each tier was called, in what order, with what
`prior`), which is exactly what the cap must guarantee.

The load-bearing test is `test_always_fail_holds_the_cap`: an always-failing
gate must produce EXACTLY `repair_cap + 1` generate calls and stop -- proving a
(cap+1)'th repair is structurally impossible, the invariant the 2026-05-20 log
breach violated when the cap lived only as a prompt rule.

NOTE (the eager-vs-broker caveat this spike exists to check): green here proves
the cap holds under eager execution. The broker path must still be confirmed
once against a live Redis worker -- see docs/FINDINGS-canvas-repair-retry-spike.
"""
from __future__ import annotations

import pytest

# Celery is an opt-in extra (`uv sync --extra celery`); CI installs only the
# `mcp` extra. Skip the whole module cleanly when celery isn't available so
# the collection error doesn't redden the build. cascade.canvas_spike is also
# in [tool.coverage.run] omit alongside its siblings cascade/celery_app.py and
# cascade/tasks.py, so this skip doesn't break the 100% coverage gate.
pytest.importorskip("celery", reason="celery is an opt-in extra")

from cascade import canvas_spike  # noqa: E402  (skip-gated import)
from cascade.celery_app import app  # noqa: E402
from cascade.config import CONFIG  # noqa: E402

CAP = CONFIG.repair_cap


@pytest.fixture
def eager():
    """Run tasks inline so self.retry() loops synchronously in-process."""
    prev_eager = app.conf.task_always_eager
    prev_prop = app.conf.task_eager_propagates
    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = False
    try:
        yield
    finally:
        app.conf.task_always_eager = prev_eager
        app.conf.task_eager_propagates = prev_prop


def _gen(text="```python\ndef add(a, b):\n    return a + b\n```"):
    """A successful (available) generate result, shaped like tasks.generate."""
    return {"available": True, "text": text, "model": "fake", "latency_s": 0.0}


def _patch(mocker, *, verify_seq, gen_text=None):
    """Patch the two recorded worker fns. `verify_seq` is the sequence of
    pass/fail booleans the gate returns across calls. Returns the two spies."""
    gen = mocker.patch(
        "cascade.tasks.generate",
        side_effect=lambda *a, **k: _gen(gen_text) if gen_text else _gen(),
    )
    verify = mocker.patch(
        "cascade.tasks.verify_functional",
        side_effect=[{"passed": p, "failures": () if p else ({"expr": "x"},)}
                     for p in verify_seq],
    )
    return gen, verify


def test_pass_first_try(eager, mocker):
    gen, verify = _patch(mocker, verify_seq=[True])
    out = canvas_spike.solve_budget("write add(a, b)", dsl="DSL")
    assert out["final_tier"] == "gpu"
    assert out["rounds"] == 0
    assert gen.call_count == 1
    assert verify.call_count == 1


def test_pass_after_one_repair(eager, mocker):
    gen, verify = _patch(mocker, verify_seq=[False, True])
    out = canvas_spike.solve_budget("write add(a, b)", dsl="DSL")
    assert out["final_tier"] == "gpu"
    assert out["rounds"] == 1          # one retry happened -> self.request.retries
    assert gen.call_count == 2         # fresh + 1 repair
    assert verify.call_count == 2


def test_always_fail_holds_the_cap(eager, mocker):
    # Gate fails on every attempt, more times than the loop can consume.
    gen, verify = _patch(mocker, verify_seq=[False] * (CAP + 5))
    out = canvas_spike.solve_budget("write add(a, b)", dsl="DSL")
    assert out["final_tier"] == "capped->tier3"
    assert out["rounds"] == CAP
    # THE INVARIANT: 1 fresh generate + CAP repairs, and NOT ONE MORE.
    assert gen.call_count == CAP + 1
    assert verify.call_count == CAP + 1


def test_gpu_unavailable_caps_immediately(eager, mocker):
    gen = mocker.patch(
        "cascade.tasks.generate",
        return_value={"available": False, "text": "[gpu unavailable]"},
    )
    verify = mocker.patch("cascade.tasks.verify_functional")
    out = canvas_spike.solve_budget("write add(a, b)", dsl="DSL")
    assert out["final_tier"] == "capped->tier3"
    assert out["reason"] == "gpu unavailable"
    assert gen.call_count == 1
    verify.assert_not_called()         # no point gating an absent answer


def test_get_exhaustion_is_capped(mocker):
    """Defensive guard: the eager pre-check normally stops the loop before
    Celery's own exhaustion fires, so `.get()` returns a dict. But if the BROKER
    path ever lets `MaxRetriesExceededError` escape `.get()`, solve_budget must
    still return a cap signal, not raise. (This is the eager-vs-broker seam the
    rest of the suite can't reach -- it's why the guard exists and is tested.)"""
    from celery.exceptions import MaxRetriesExceededError

    fake = mocker.Mock()
    fake.get.side_effect = MaxRetriesExceededError("exhausted")
    mocker.patch.object(canvas_spike.gpu_solve_task, "apply_async",
                        return_value=fake)
    out = canvas_spike.solve_budget("write add(a, b)", dsl="DSL")
    assert out["final_tier"] == "capped->tier3"
    assert out["rounds"] == CAP


def test_dsl_none_uses_syntax_gate_not_verify_functional(eager, mocker):
    """When dsl=None, gpu_solve_task must use the SYNTAX gate
    (cascade.verifier.verify) instead of tasks.verify_functional. This is the
    parity contract with the in-process pipe path (mesh.solve), which the
    Slice-4 live-broker run surfaced: without this fallback every Canvas run
    without a DSL caps to Tier-3 because the functional gate returns
    applicable:false (= passed:false) when no DSL is supplied."""
    # Mock generate to return a parseable Python block; verify_functional must
    # NOT be called on the dsl=None path.
    mocker.patch("cascade.tasks.generate", side_effect=lambda *a, **k: _gen())
    verify_func = mocker.patch("cascade.tasks.verify_functional")
    out = canvas_spike.solve_budget("write add(a, b)")  # dsl=None
    assert out["final_tier"] == "gpu"
    assert out["rounds"] == 0
    verify_func.assert_not_called()


def test_dsl_none_caps_when_syntax_fails(eager, mocker):
    """dsl=None path: if the generate text is NOT a parseable Python block,
    the syntax gate fails and the cap engages through the same retry loop.
    Cap invariant holds regardless of which gate is in play."""
    mocker.patch(
        "cascade.tasks.generate",
        side_effect=lambda *a, **k: {
            "available": True, "text": "not a code block",
            "model": "fake", "latency_s": 0.0,
        },
    )
    verify_func = mocker.patch("cascade.tasks.verify_functional")
    out = canvas_spike.solve_budget("x")  # dsl=None
    assert out["final_tier"] == "capped->tier3"
    assert out["rounds"] == CAP
    verify_func.assert_not_called()


def _run_gpu_solve(*, query="write add(a, b)", dsl="DSL", prior=None,
                   round_base=0):
    """Dispatch gpu_solve_task directly (eager) so round_base can be exercised --
    solve_budget never passes a prior/round_base (it's the standalone entry)."""
    return canvas_spike.gpu_solve_task.apply_async(
        kwargs={"query": query, "dsl": dsl, "prior": prior,
                "round_base": round_base}).get()


def test_round_base_one_first_repair_is_round_1(eager, mocker):
    """round_base=1 (a failed NPU draft handed in as `prior`): the first GPU
    generate REPAIRS it and, if it passes, is reported as round 1 -- the
    Canvas->pipe alignment (Slice 6a). Contrast test_pass_first_try, where the
    no-prior first generate is round 0."""
    gen, verify = _patch(mocker, verify_seq=[True])
    out = _run_gpu_solve(prior="```python\nbad npu draft\n```", round_base=1)
    assert out["final_tier"] == "gpu"
    assert out["rounds"] == 1          # first GPU call on a prior = round 1
    assert gen.call_count == 1


def test_round_base_one_caps_at_cap_generates(eager, mocker):
    """round_base=1 + always-fail: the cap bounds the run to EXACTLY `cap` GPU
    calls (the first is already a repair), vs cap+1 on the no-prior path. This
    is the structural half of the alignment -- no extra generate when capping on
    a prior, matching mesh.solve's range(1, cap+1)."""
    gen, verify = _patch(mocker, verify_seq=[False] * (CAP + 5))
    out = _run_gpu_solve(prior="```python\nbad npu draft\n```", round_base=1)
    assert out["final_tier"] == "capped->tier3"
    assert out["rounds"] == CAP
    assert gen.call_count == CAP        # NOT cap+1 -- the first call is a repair
    assert verify.call_count == CAP


def test_repair_threads_prior_draft_forward(eager, mocker):
    # Distinct draft text per generate call so we can see the prior threaded in.
    texts = [f"```python\n# draft {i}\ndef add(a, b):\n    return a + b\n```"
             for i in range(CAP + 1)]
    gen = mocker.patch("cascade.tasks.generate",
                       side_effect=[_gen(t) for t in texts])
    mocker.patch(
        "cascade.tasks.verify_functional",
        side_effect=[{"passed": False, "failures": ({"expr": "x"},)}
                     for _ in range(CAP + 1)],
    )
    canvas_spike.solve_budget("write add(a, b)", dsl="DSL")
    # First call: no prior. Each subsequent call repairs ON the previous draft.
    assert gen.call_args_list[0].kwargs.get("prior_attempt") is None
    for i in range(1, CAP + 1):
        assert gen.call_args_list[i].kwargs["prior_attempt"] == texts[i - 1]
