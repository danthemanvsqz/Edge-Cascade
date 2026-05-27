"""PD-1 v1 degeneration detector — pure function, no I/O.

A cheap quality signal over a model's text output: looping (trigram + sentence
repetition) and lexical narrowing (type-token ratio, distinct-sentence ratio).
Plus tier availability: any `False` in `tier_availability` contributes a reason.

Background (`docs/FINDINGS-persona-debate-OVERVIEW.md`): capacity correlates
strongly with looping (r = -0.92) and TTR (r = +0.89), so these metrics are
sharp class separators between small (1.5B) and large (14B) draft outputs.
Vocab density does *not* track capacity and is intentionally omitted.

v1 is TELEMETRY-ONLY: `cascade.mesh.solve` emits a trace line per draft, no
behavior change. Acting on the signal (skip repair / escalate / warn) is v2.

Note: thresholds calibrated on PROSE (persona-debate state.json turns). Code
outputs may show different baselines (e.g. `_words` regex picks up Python
identifiers fine, but `_sentences` splits on `.!?` which over-splits method
calls). Calibration on code corpus is a v2 task; for v1 we record the score and
observe.

Metric helpers (_words/_sentences/_count) are imported by
`scripts/debate_analysis.py` so the persona-debate analyzer and the live
detector share one source of truth.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


def _words(t: str) -> list[str]:
    return re.findall(r"\w+", t.lower())


def _sentences(t: str) -> list[str]:
    return [re.sub(r"\s+", " ", s).strip().lower()
            for s in re.split(r"[.!?]+", t) if s.strip()]


def _count(text: str, terms: list[str]) -> int:
    low = text.lower()
    return sum(low.count(term) for term in terms)


@dataclass(frozen=True)
class Thresholds:
    """Per-metric trip points. A metric trips when it crosses its threshold in
    the *bad* direction: trigram_repeat / max_sent_repeat HIGH = bad;
    distinct_sent_ratio / ttr LOW = bad. Defaults are placeholders until the
    calibration script writes `cascade/degeneration_thresholds.json`."""

    trigram_repeat_max: float = 0.75
    max_sent_repeat_max: float = 3.0
    distinct_sent_ratio_min: float = 0.5
    ttr_min: float = 0.3
    text_weight: float = 0.7      # blend weight for text score in aggregate
    tier_weight: float = 0.3      # blend weight for tier score in aggregate

    @classmethod
    def load(cls, path: Path) -> Thresholds:
        """Read calibrated thresholds from JSON.

        Unknown keys are dropped so future fields don't break older files
        (and so the calibration JSON can carry an in-band `_notes` field).
        Values are coerced to `float` so a hand-edited string like "0.5"
        doesn't propagate as a non-numeric into the detector and explode at
        comparison time, far from the source."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        valid = set(cls.__dataclass_fields__)
        return cls(**{k: float(v) for k, v in raw.items() if k in valid})


@dataclass(frozen=True)
class DegenerationResult:
    """Detector verdict. `score` ∈ [0, 1] is a blended quality signal;
    `degraded` is the boolean trip; `text_reasons` lists draft-quality trips
    (looping/narrowing); `tier_reasons` lists tier-unavailability trips
    (cascade health, not draft quality); `features` carries the raw metrics
    for downstream telemetry. Pure data — no I/O on the object.

    `reasons` is exposed as a property combining the two disjoint tuples so
    callers / golden-replay records that consume the merged view stay working
    (recorder, trace strings). Consumers that want only one half should read
    `text_reasons` or `tier_reasons` directly -- the producer's contract is
    the split, not the merged string."""

    degraded: bool
    score: float
    text_reasons: tuple[str, ...] = ()
    tier_reasons: tuple[str, ...] = ()
    features: dict[str, float] = field(default_factory=dict)

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.text_reasons + self.tier_reasons


def text_features(text: str) -> dict[str, float]:
    """Compute the four text metrics on `text`. Returns 0.0/1.0 defaults for an
    empty input so the detector never raises on empty strings — an empty draft
    is itself a signal a tier produced nothing."""
    words = _words(text)
    sents = _sentences(text)
    nw = max(len(words), 1)
    ns = max(len(sents), 1)
    tris = list(zip(words, words[1:], words[2:], strict=False))
    sent_counts = Counter(sents)
    return {
        "trigram_repeat": (1 - len(set(tris)) / len(tris)) if tris else 0.0,
        "max_sent_repeat": float(max(sent_counts.values()) if sents else 1),
        "distinct_sent_ratio": len(set(sents)) / ns,
        "ttr": len(set(words)) / nw,
    }


def _text_reasons(
    feats: dict[str, float], thr: Thresholds
) -> tuple[tuple[str, ...], float]:
    """Compare each metric to its threshold; return tripped reasons + a 0..1
    text score = fraction of metrics tripped (4 metrics -> 0, 0.25, 0.5, 0.75, 1.0)."""
    checks = (
        ("trigram_repeat", feats["trigram_repeat"], thr.trigram_repeat_max, ">"),
        ("max_sent_repeat", feats["max_sent_repeat"], thr.max_sent_repeat_max, ">"),
        ("distinct_sent_ratio", feats["distinct_sent_ratio"],
         thr.distinct_sent_ratio_min, "<"),
        ("ttr", feats["ttr"], thr.ttr_min, "<"),
    )
    reasons: list[str] = []
    for name, val, threshold, op in checks:
        tripped = (val > threshold) if op == ">" else (val < threshold)
        if tripped:
            reasons.append(
                f"looping: {name}={val:.2f} {op} {threshold:.2f}"
                if op == ">"
                else f"narrowing: {name}={val:.2f} {op} {threshold:.2f}"
            )
    return tuple(reasons), len(reasons) / len(checks)


def _tier_reasons(
    tier_availability: dict[str, bool] | None,
) -> tuple[tuple[str, ...], float]:
    """Any tier flagged `available:false` becomes a reason. The tier score is
    the fraction of named tiers that are down, so the signal scales with how
    much of the cascade is degraded."""
    if not tier_availability:
        return (), 0.0
    down = [name for name, ok in tier_availability.items() if not ok]
    if not down:
        return (), 0.0
    reasons = tuple(f"tier:{name} unavailable" for name in down)
    return reasons, len(down) / len(tier_availability)


def check_degeneration(
    text: str,
    tier_availability: dict[str, bool] | None = None,
    thresholds: Thresholds | None = None,
) -> DegenerationResult:
    """Pure detector: text + tier health -> verdict. No I/O.

    - `text` is the model's output (a draft or a repair).
    - `tier_availability` maps tier name -> `True` if healthy; `None`/empty
      skips the tier dimension (callers who don't surface tier status get a
      text-only verdict).
    - `thresholds` defaults to library values; production callers should pass
      `Thresholds.load(<json path>)` so the live calibration is in force.

    `degraded` is `True` if ANY text metric trips OR any tier is unavailable.
    `score` is a weighted blend of text and tier sub-scores (0 = clean, 1 =
    every metric tripped + every tier down)."""
    thr = thresholds or Thresholds()
    feats = text_features(text)
    text_reasons, text_score = _text_reasons(feats, thr)
    tier_reasons, tier_score = _tier_reasons(tier_availability)
    score = thr.text_weight * text_score + thr.tier_weight * tier_score
    return DegenerationResult(
        degraded=bool(text_reasons) or bool(tier_reasons),
        score=score,
        text_reasons=text_reasons,
        tier_reasons=tier_reasons,
        features=feats,
    )
