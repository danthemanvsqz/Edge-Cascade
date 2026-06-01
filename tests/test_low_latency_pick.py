"""Unit tests for the pure low_latency gating decision helper.

cascade.topologies_canvas._pick_first_verified is in coverage.omit (Celery
substrate). The decision logic — cheapest-first candidate selection, available-
skip, double-miss-cap — lives here under the 100% gate, with an injectable
gate_fn so no broker or hardware is needed.
"""
from __future__ import annotations

from cascade.low_latency_pick import _pick_decision

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _env(dsl: str | None = "DSL") -> dict:
    return {
        "dsl": dsl,
        "trace": [],
        "answer": None,
        "final_tier": "",
        "resolved": False,
        "capped": False,
    }


def _res(text: str = "```python\ndef f(): pass\n```", available: bool = True) -> dict:
    return {"available": available, "text": text}


def _gate_seq(passes: list[bool]):
    """Fake gate_fn yielding the given pass/fail sequence in call order."""
    it = iter(passes)

    def gate(text: str, dsl: str | None) -> tuple[bool, list]:
        passed = next(it)
        return passed, ([] if passed else [{"expr": "x"}])

    return gate


# ---------------------------------------------------------------------------
# Winner selection
# ---------------------------------------------------------------------------


def test_npu_wins_when_verified():
    env = _env()
    npu = _res("```python\ndef npu(): pass\n```")
    gpu = _res("```python\ndef gpu(): pass\n```")
    result = _pick_decision([npu, gpu], env, gate_fn=_gate_seq([True]))
    assert result["final_tier"] == "npu"
    assert result["resolved"] is True
    assert result["answer"] == npu["text"]
    assert result["capped"] is False


def test_gpu_wins_when_npu_fails():
    env = _env()
    result = _pick_decision(
        [_res("bad npu"), _res("```python\ndef gpu(): pass\n```")],
        env,
        gate_fn=_gate_seq([False, True]),
    )
    assert result["final_tier"] == "gpu"
    assert result["resolved"] is True
    assert result["capped"] is False


def test_capped_when_both_fail():
    env = _env()
    result = _pick_decision(
        [_res("bad"), _res("also bad")],
        env,
        gate_fn=_gate_seq([False, False]),
    )
    assert result["capped"] is True
    assert result["resolved"] is False
    assert result["answer"] is None


def test_npu_unavailable_falls_through_to_gpu():
    env = _env()
    gate = _gate_seq([True])  # called exactly once, for the gpu arm
    result = _pick_decision(
        [_res(text="", available=False), _res()],
        env,
        gate_fn=gate,
    )
    assert result["final_tier"] == "gpu"
    assert result["resolved"] is True


def test_npu_empty_text_treated_as_unavailable():
    """available=True but empty text => skip (same as available=False)."""
    env = _env()
    gate = _gate_seq([True])
    result = _pick_decision(
        [_res(text=""), _res()],
        env,
        gate_fn=gate,
    )
    assert result["final_tier"] == "gpu"
    assert result["resolved"] is True


def test_both_unavailable_caps():
    env = _env()
    result = _pick_decision(
        [_res(available=False, text=""), _res(available=False, text="")],
        env,
        gate_fn=_gate_seq([]),
    )
    assert result["capped"] is True
    assert result["resolved"] is False


def test_empty_results_caps():
    env = _env()
    result = _pick_decision([], env, gate_fn=_gate_seq([]))
    assert result["capped"] is True
    assert result["resolved"] is False


# ---------------------------------------------------------------------------
# Trace content
# ---------------------------------------------------------------------------


def test_trace_records_npu_pass():
    env = _env()
    _pick_decision([_res()], env, gate_fn=_gate_seq([True]))
    assert any("npu race candidate gate PASS" in t for t in env["trace"])


def test_trace_records_npu_fail_then_gpu_pass():
    env = _env()
    _pick_decision([_res("bad"), _res()], env, gate_fn=_gate_seq([False, True]))
    trace = " ".join(env["trace"])
    assert "npu race candidate gate FAIL" in trace
    assert "gpu race candidate gate PASS" in trace


def test_trace_records_unavailable_skip():
    env = _env()
    _pick_decision(
        [_res(available=False, text=""), _res()],
        env,
        gate_fn=_gate_seq([True]),
    )
    assert any("npu race candidate unavailable" in t for t in env["trace"])


def test_trace_records_double_miss_cap():
    env = _env()
    _pick_decision([_res("bad"), _res("bad")], env, gate_fn=_gate_seq([False, False]))
    assert any("neither raced candidate verified" in t for t in env["trace"])


# ---------------------------------------------------------------------------
# DSL threading
# ---------------------------------------------------------------------------


def test_gate_receives_env_dsl():
    """The gate_fn is called with the env's dsl value, not hardcoded."""
    calls: list[tuple[str, str | None]] = []

    def recording_gate(text: str, dsl: str | None) -> tuple[bool, list]:
        calls.append((text, dsl))
        return True, []

    env = _env(dsl="assert f(1) == 2")
    _pick_decision([_res()], env, gate_fn=recording_gate)
    assert len(calls) == 1
    assert calls[0][1] == "assert f(1) == 2"


def test_gate_receives_none_dsl():
    calls: list[tuple[str, str | None]] = []

    def recording_gate(text: str, dsl: str | None) -> tuple[bool, list]:
        calls.append((text, dsl))
        return True, []

    env = _env(dsl=None)
    _pick_decision([_res()], env, gate_fn=recording_gate)
    assert calls[0][1] is None
