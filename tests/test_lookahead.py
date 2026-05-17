"""Mock-free regression net for look-ahead: the pure agreement metric, the
speedup summary, and the Step record. The trust/credit-guard controller needs
live workers and is covered by the live smoke runs (it was exercised there),
not by simulated mocks."""
from cascade.lookahead import LookAheadResult, Step, _agreement


def test_agreement_is_normalized():
    assert _agreement("abc", "abc") == 1.0
    assert _agreement("a  b", "a b") == 1.0          # whitespace-normalized
    assert _agreement("totally", "different!!") < 0.5
    assert 0.0 <= _agreement("x", "y") <= 1.0        # bounded


def test_speedup_note_counts_solo():
    steps = [
        Step("t", "npu-solo", "npu", 1.0, 0.1, True, 1),
        Step("t", "verified", "gpu", 0.3, 0.2, True, 0),
        Step("t", "npu-solo", "npu", 1.0, 0.1, True, 2),
    ]
    assert LookAheadResult(steps).speedup_note == \
        "2/3 tasks answered NPU-solo (GPU calls skipped: 2)"


def test_speedup_note_empty_uses_one_denominator():
    assert LookAheadResult().speedup_note == \
        "0/1 tasks answered NPU-solo (GPU calls skipped: 0)"


def test_step_record_fields():
    s = Step("task", "verified", "gpu", 0.42, 1.2, False, 0)
    assert s.task == "task" and s.answerer == "gpu"
    assert s.agreement == 0.42 and s.ok is False
