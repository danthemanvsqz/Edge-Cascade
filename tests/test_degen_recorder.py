"""SD-2b: PD-1 v1 degeneration recorder writes a parseable lane that the
dashboard panel can tail directly (no cascade.log grepping)."""
from __future__ import annotations

import json

from cascade.degen_recorder import make_degen_recorder
from cascade.degeneration import DegenerationResult
from cascade.logfmt import parse_stream


def _result(score=0.25, degraded=True, reasons=("looping: trigram_repeat=0.10 > 0.04",)):
    return DegenerationResult(
        degraded=degraded,
        score=score,
        text_reasons=tuple(reasons),
        features={
            "trigram_repeat": 0.1037,
            "max_sent_repeat": 2.0,
            "distinct_sent_ratio": 0.8125,
            "ttr": 0.6542,
        },
    )


def test_emit_appends_one_parseable_record_per_call(tmp_path):
    emit = make_degen_recorder(tmp_path / "cascade-degeneration.rec")
    emit("npu", _result())
    emit("gpu", _result(score=0.0, degraded=False, reasons=()))

    records = parse_stream((tmp_path / "cascade-degeneration.rec").read_bytes())
    assert len(records) == 2
    assert [r["tier"] for r in records] == ["npu", "gpu"]
    assert records[0]["server"] == "cascade-degeneration"
    assert records[0]["tool"] == "observe"


def test_emit_seq_is_monotonic_in_closure(tmp_path):
    emit = make_degen_recorder(tmp_path / "cascade-degeneration.rec")
    for _ in range(3):
        emit("npu", _result())
    records = parse_stream((tmp_path / "cascade-degeneration.rec").read_bytes())
    assert [r["_seq"] for r in records] == ["0", "1", "2"]


def test_emit_run_id_is_stable_per_recorder(tmp_path):
    """run_id ties an observation back to a session. One recorder = one
    run_id across all its emits."""
    emit = make_degen_recorder(tmp_path / "cascade-degeneration.rec")
    emit("npu", _result())
    emit("gpu", _result())
    records = parse_stream((tmp_path / "cascade-degeneration.rec").read_bytes())
    assert records[0]["run_id"] == records[1]["run_id"]
    assert len(records[0]["run_id"]) == 12


def test_emit_two_recorders_have_independent_run_ids(tmp_path):
    """Each session opens its own recorder, so run_ids must not collide."""
    a = make_degen_recorder(tmp_path / "a.rec")
    b = make_degen_recorder(tmp_path / "b.rec")
    a("npu", _result())
    b("npu", _result())
    ra = parse_stream((tmp_path / "a.rec").read_bytes())[0]
    rb = parse_stream((tmp_path / "b.rec").read_bytes())[0]
    assert ra["run_id"] != rb["run_id"]


def test_emit_fields_carry_score_degraded_and_all_four_features(tmp_path):
    emit = make_degen_recorder(tmp_path / "cascade-degeneration.rec")
    emit("npu", _result(score=0.17, degraded=True))
    r = parse_stream((tmp_path / "cascade-degeneration.rec").read_bytes())[0]
    assert r["score"] == "0.17"
    assert r["degraded"] == "true"
    assert r["trigram_repeat"] == "0.1037"
    assert r["max_sent_repeat"] == "2.00"
    assert r["distinct_sent_ratio"] == "0.8125"
    assert r["ttr"] == "0.6542"


def test_emit_reasons_round_trip_through_json(tmp_path):
    """Reasons is a tuple in the detector but must come back as a JSON list a
    JS consumer can parse without quirks."""
    reasons = ("looping: trigram_repeat=0.10 > 0.04", "tier:gpu unavailable")
    emit = make_degen_recorder(tmp_path / "cascade-degeneration.rec")
    emit("npu", _result(reasons=reasons))
    r = parse_stream((tmp_path / "cascade-degeneration.rec").read_bytes())[0]
    assert json.loads(r["reasons"]) == list(reasons)


def test_emit_creates_parent_dir(tmp_path):
    """A fresh checkout has no runs/ dir; the recorder must create it rather
    than crash on first emit."""
    target = tmp_path / "nested" / "deeper" / "cascade-degeneration.rec"
    emit = make_degen_recorder(target)
    emit("npu", _result())
    assert target.exists()


def test_emit_degraded_false_serialises_as_lowercase_string(tmp_path):
    """The TS consumer compares string equality, so the truthiness must be
    canonical 'false', not 'False' or '0'."""
    emit = make_degen_recorder(tmp_path / "cascade-degeneration.rec")
    emit("npu", _result(degraded=False, reasons=()))
    r = parse_stream((tmp_path / "cascade-degeneration.rec").read_bytes())[0]
    assert r["degraded"] == "false"
