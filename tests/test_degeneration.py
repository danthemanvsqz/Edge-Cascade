"""Tests for the PD-1 v1 degeneration detector.

The detector is a pure function over (text, tier_availability, thresholds). All
branches are exercised against handcrafted inputs; no I/O, no MCP, no model.
The `Thresholds.load` and a golden-parity check against `debate_analysis.py`
round out coverage of the shared helpers.
"""
from __future__ import annotations

import json

import pytest

from cascade.degeneration import (
    DegenerationResult,
    Thresholds,
    _count,
    _sentences,
    _words,
    check_degeneration,
    text_features,
)

# ---- metric helpers --------------------------------------------------------


def test_words_lowercases_and_tokenizes():
    assert _words("Hello, WORLD! 42") == ["hello", "world", "42"]


def test_words_empty():
    assert _words("") == []


def test_sentences_splits_on_terminators_and_normalizes_whitespace():
    assert _sentences("One.  Two!\tThree?Four") == ["one", "two", "three", "four"]


def test_sentences_drops_empties():
    assert _sentences("   .  !  ?  ") == []


def test_count_case_insensitive_overlap_safe():
    # "abab" contains "ab" twice; "AB" matches case-insensitively. The helper
    # is substring-count, not regex, so overlapping matches do NOT double-count.
    assert _count("abABab", ["ab"]) == 3


def test_count_multiple_terms_sum():
    assert _count("foo bar foo baz", ["foo", "baz"]) == 3


# ---- text_features ---------------------------------------------------------


def test_text_features_clean_prose():
    """Four distinct sentences with varied vocabulary produce no looping
    signal and high diversity."""
    text = "The cat sat on the mat. A dog ran fast. Birds fly high. Fish swim deep."
    f = text_features(text)
    assert f["distinct_sent_ratio"] == 1.0           # all sentences unique
    assert f["max_sent_repeat"] == 1.0
    assert f["trigram_repeat"] == 0.0                # no trigram seen twice
    assert 0.7 <= f["ttr"] <= 1.0                    # high diversity


def test_text_features_looping_repeated_sentence():
    """The classic small-model failure mode: the same sentence emitted many
    times — trips max_sent_repeat and tanks distinct_sent_ratio."""
    text = "I think therefore I am. " * 6
    f = text_features(text)
    assert f["max_sent_repeat"] == 6.0
    assert f["distinct_sent_ratio"] < 0.2
    assert f["trigram_repeat"] > 0.5                 # n-grams collapse hard


def test_text_features_empty_string_safe_defaults():
    """An empty draft is itself a signal; the helper must not raise."""
    f = text_features("")
    assert f == {
        "trigram_repeat": 0.0,
        "max_sent_repeat": 1.0,
        "distinct_sent_ratio": 0.0,
        "ttr": 0.0,
    }


def test_text_features_single_word_no_trigrams():
    """One token -> no trigrams; trigram_repeat falls back to 0.0."""
    f = text_features("hello")
    assert f["trigram_repeat"] == 0.0


# ---- Thresholds ------------------------------------------------------------


def test_thresholds_defaults():
    thr = Thresholds()
    assert thr.trigram_repeat_max == 0.75
    assert thr.max_sent_repeat_max == 3.0
    assert thr.distinct_sent_ratio_min == 0.5
    assert thr.ttr_min == 0.3
    assert thr.text_weight + thr.tier_weight == pytest.approx(1.0)


def test_thresholds_load_round_trip(tmp_path):
    """Persisted thresholds round-trip; unknown keys are ignored so future
    fields (and the in-band `_notes` caveat in the calibrated JSON) don't
    break older callers."""
    p = tmp_path / "thr.json"
    p.write_text(json.dumps({
        "trigram_repeat_max": 0.5,
        "ttr_min": 0.4,
        "_notes": "drop me",               # dropped
        "unknown_future_key": 999,         # dropped
    }), encoding="utf-8")
    thr = Thresholds.load(p)
    assert thr.trigram_repeat_max == 0.5
    assert thr.ttr_min == 0.4
    # Unspecified fields keep library defaults.
    assert thr.max_sent_repeat_max == 3.0


def test_thresholds_load_coerces_to_float(tmp_path):
    """Hand-edited JSON with a string-encoded number works -- the coerce step
    keeps the type contract so downstream comparisons can't silently break."""
    p = tmp_path / "thr.json"
    p.write_text(json.dumps({"trigram_repeat_max": "0.5"}), encoding="utf-8")
    thr = Thresholds.load(p)
    assert thr.trigram_repeat_max == 0.5
    assert isinstance(thr.trigram_repeat_max, float)


def test_thresholds_load_real_committed_json():
    """The committed calibration JSON loads cleanly with the in-band `_notes`
    field. Pins the contract between the calibration script and the loader."""
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parent.parent
    thr = Thresholds.load(root / "cascade" / "degeneration_thresholds.json")
    # Calibrated values are in plausible ranges; specific values would
    # over-constrain (re-calibration would force a test update).
    assert 0.0 <= thr.trigram_repeat_max <= 1.0
    assert 0.0 <= thr.ttr_min <= 1.0
    assert 0.0 <= thr.distinct_sent_ratio_min <= 1.0
    assert thr.max_sent_repeat_max >= 1.0


# ---- check_degeneration ---------------------------------------------------


def _clean_text() -> str:
    return ("The system processes input streams. Each record is validated. "
            "Failures route to repair. Success paths short-circuit early.")


def _looping_text() -> str:
    return "Hello world. " * 8


def test_check_clean_text_no_tiers_not_degraded():
    r = check_degeneration(_clean_text())
    assert isinstance(r, DegenerationResult)
    assert r.degraded is False
    assert r.reasons == ()
    assert r.score == 0.0
    # features round-trip
    assert set(r.features) == {"trigram_repeat", "max_sent_repeat",
                               "distinct_sent_ratio", "ttr"}


def test_check_empty_text_is_degraded():
    """An empty draft is itself a signal: zero distinct sentences and zero TTR
    both trip with default thresholds, so the verdict surfaces it."""
    r = check_degeneration("")
    assert r.degraded is True
    assert any("narrowing: distinct_sent_ratio=" in reason for reason in r.reasons)
    assert any("narrowing: ttr=" in reason for reason in r.reasons)


def test_check_looping_text_trips_text_metrics():
    r = check_degeneration(_looping_text())
    assert r.degraded is True
    # at least one text metric tripped; "looping" or "narrowing" prefix.
    assert any(reason.startswith(("looping:", "narrowing:")) for reason in r.reasons)
    assert r.score > 0.0


def test_check_low_ttr_trips_narrowing():
    """Lexical narrowing without sentence-level repeat: lots of unique
    sentences but same handful of words recycled."""
    text = ("a a a b. b a a c. c a b a. a b c a.")
    thr = Thresholds(ttr_min=0.9)              # force the trip
    r = check_degeneration(text, thresholds=thr)
    assert r.degraded is True
    assert any("narrowing: ttr=" in reason for reason in r.reasons)


def test_check_tier_unavailable_alone_trips():
    """No text problem, but the cascade has a tier down -> degraded."""
    r = check_degeneration(
        _clean_text(),
        tier_availability={"edge-npu": False, "edge-gpu": True},
    )
    assert r.degraded is True
    assert "tier:edge-npu unavailable" in r.reasons
    assert "tier:edge-gpu unavailable" not in r.reasons
    # 1 of 2 tiers down -> tier_score = 0.5; default tier_weight = 0.3
    # text_score = 0.0 -> final score = 0.15
    assert r.score == pytest.approx(0.3 * 0.5)


def test_check_all_tiers_healthy_no_text_problem_not_degraded():
    r = check_degeneration(
        _clean_text(),
        tier_availability={"edge-npu": True, "edge-gpu": True},
    )
    assert r.degraded is False
    assert r.reasons == ()
    assert r.score == 0.0


def test_check_tier_availability_empty_dict_skips_dimension():
    """An empty dict is the same as None: no tier reasons, no tier score."""
    r = check_degeneration(_clean_text(), tier_availability={})
    assert r.degraded is False
    assert r.score == 0.0


def test_check_reasons_accumulate_text_and_tier():
    r = check_degeneration(
        _looping_text(),
        tier_availability={"edge-npu": False},
    )
    assert r.degraded is True
    # both flavors of reason show up
    assert any(reason.startswith(("looping:", "narrowing:")) for reason in r.reasons)
    assert "tier:edge-npu unavailable" in r.reasons


def test_check_custom_thresholds_make_clean_text_trip():
    """A pathologically strict threshold trips clean prose -- proves the
    threshold parameter is honored end-to-end."""
    strict = Thresholds(distinct_sent_ratio_min=2.0)   # impossible
    r = check_degeneration(_clean_text(), thresholds=strict)
    assert r.degraded is True
    assert any("narrowing: distinct_sent_ratio=" in reason for reason in r.reasons)


# ---- DRY parity: debate_analysis.features() metrics ----------------------


# ---- calibration script pure helpers --------------------------------------


def test_youden_j_perfect_separator():
    """All positives above threshold, all negatives below -> J = 1.0."""
    from scripts.calibrate_degeneration_thresholds import youden_j
    values = [0.1, 0.2, 0.3, 0.7, 0.8, 0.9]
    labels = [False, False, False, True, True, True]
    assert youden_j(values, labels, 0.5, ">") == 1.0


def test_youden_j_random_threshold_below_perfect():
    from scripts.calibrate_degeneration_thresholds import youden_j
    values = [0.1, 0.2, 0.9]
    labels = [False, True, True]
    # threshold 0.5: TPR = 1/2 = 0.5 (0.9 > 0.5; 0.2 not), FPR = 0/1 = 0
    assert youden_j(values, labels, 0.5, ">") == 0.5


def test_youden_j_inverted_direction():
    """direction='<' for metrics where LOW = bad (ttr, distinct_sent_ratio)."""
    from scripts.calibrate_degeneration_thresholds import youden_j
    # degenerate has low values, clean has high values
    values = [0.1, 0.2, 0.8, 0.9]
    labels = [True, True, False, False]
    assert youden_j(values, labels, 0.5, "<") == 1.0


def test_youden_j_zero_denominators_safe():
    """All-positive or all-negative labels avoid divide-by-zero."""
    from scripts.calibrate_degeneration_thresholds import youden_j
    # all labels True -> no negatives, FPR has 0 denominator -> defined as 0
    assert youden_j([0.1, 0.2], [True, True], 0.5, ">") == 0.0
    # all labels False -> no positives -> TPR has 0 denominator -> 0
    assert youden_j([0.1, 0.2], [False, False], 0.5, ">") == 0.0


def test_best_threshold_picks_optimal():
    from scripts.calibrate_degeneration_thresholds import best_threshold
    values = [0.1, 0.2, 0.3, 0.7, 0.8, 0.9]
    labels = [False, False, False, True, True, True]
    thr, j = best_threshold(values, labels, ">", n_steps=20)
    assert j == 1.0
    assert 0.3 <= thr <= 0.7   # any cut in the gap works


def test_best_threshold_degenerate_input():
    """All values equal -> no discrimination; helper returns (value, 0)."""
    from scripts.calibrate_degeneration_thresholds import best_threshold
    thr, j = best_threshold([0.5, 0.5, 0.5], [True, False, True], ">")
    assert thr == 0.5
    assert j == 0.0


def test_calibrate_end_to_end_synthetic():
    """Feed two clusters of text -- the degenerate cluster all-loops, the clean
    cluster all-varied -- and assert the calibrator finds a non-trivial
    threshold for each metric in the expected direction."""
    from scripts.calibrate_degeneration_thresholds import calibrate
    looping = "Hello world. " * 8
    clean = ("The system processes input. Each record validates. "
             "Failures route through repair. Success short-circuits early.")
    pairs = [
        (looping, True), (looping, True), (looping, True),
        (clean, False), (clean, False), (clean, False),
    ]
    out = calibrate(pairs)
    # All four threshold fields present.
    assert set(out) == {"trigram_repeat_max", "max_sent_repeat_max",
                        "distinct_sent_ratio_min", "ttr_min"}
    # Calibrated thresholds load cleanly into Thresholds.
    thr = Thresholds(
        trigram_repeat_max=out["trigram_repeat_max"],
        max_sent_repeat_max=out["max_sent_repeat_max"],
        distinct_sent_ratio_min=out["distinct_sent_ratio_min"],
        ttr_min=out["ttr_min"],
    )
    # The detector with these thresholds trips on the degenerate sample and
    # NOT on the clean sample -- the J=perfect calibration target.
    assert check_degeneration(looping, thresholds=thr).degraded is True
    assert check_degeneration(clean, thresholds=thr).degraded is False


def test_write_thresholds_round_trip(tmp_path):
    """The writer adds an in-band `_notes` caveat alongside the calibrated
    floats; Thresholds.load drops `_notes` so the load path stays clean."""
    from scripts.calibrate_degeneration_thresholds import write_thresholds
    path = tmp_path / "out.json"
    payload = {"trigram_repeat_max": 0.7, "ttr_min": 0.25}
    write_thresholds(payload, path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["trigram_repeat_max"] == 0.7
    assert loaded["ttr_min"] == 0.25
    assert "_notes" in loaded and "DO NOT ship" in loaded["_notes"]
    # File ends with newline (per Edge-Cascade trim/eof hook).
    assert path.read_text(encoding="utf-8").endswith("\n")
    # Loader drops `_notes` cleanly.
    thr = Thresholds.load(path)
    assert thr.trigram_repeat_max == 0.7
    assert thr.ttr_min == 0.25


# ---- DRY parity: debate_analysis.features() metrics ----------------------


def test_debate_analysis_metric_parity():
    """The shared metric helpers must produce the same 4 numbers debate_analysis
    used before the refactor. Pin the contract so future edits to either
    consumer can't silently diverge."""
    pytest.importorskip("numpy")  # debate_analysis imports numpy
    from scripts.debate_analysis import SURNAME, Turn, features
    text = ("I think therefore I am. The categorical imperative binds all "
            "rational beings. Duty is the law. Maxim universalize.")
    t = Turn(
        run="x", idx=0, speaker="kant", opponent="singer",
        model="", size=1.5, role="opener", is_repair=False,
        text=text, think_chars=0, latency_s=0.0, tok_s=None,
    )
    feats = features(t)
    direct = text_features(text)
    # The four shared metrics must agree exactly.
    for key in ("trigram_repeat", "max_sent_repeat", "distinct_sent_ratio", "ttr"):
        assert feats[key] == direct[key], f"{key} diverged after refactor"
    # And the persona-specific keys must still be present.
    assert "own_per100w" in feats and "capture_ratio" in feats
    # Surname lookup is intact for the imported module.
    assert SURNAME["kant"] == "kant"
