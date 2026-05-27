"""Calibrate PD-1 v1 thresholds from persona-debate evidence.

For each of the four text metrics in cascade.degeneration, sweep threshold
candidates and pick the one maximizing Youden's J (TPR - FPR) for the binary
"is 1.5B" label. The 1.5B turns are known-degeneration-rich
(`docs/FINDINGS-persona-debate-OVERVIEW.md`: r=-0.92 vs capacity on looping,
r=+0.89 on TTR); the 14B turns are the clean reference.

Writes `cascade/degeneration_thresholds.json` — committed so the live detector
reuses the same calibration in production. Run from the main tree (worktrees
typically lack `runs/experiment-debate/`).

Usage:
    uv run python scripts/calibrate_degeneration_thresholds.py
"""
from __future__ import annotations

import json
from pathlib import Path

from cascade.degeneration import text_features

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "cascade" / "degeneration_thresholds.json"

# Each metric trips in the *bad* direction. ">" means "high value = bad" (more
# repetition); "<" means "low value = bad" (less diversity).
DIRECTIONS = {
    "trigram_repeat": ">",
    "max_sent_repeat": ">",
    "distinct_sent_ratio": "<",
    "ttr": "<",
}
# Map metric -> Thresholds field name. Mirrors the dataclass in
# cascade.degeneration so the JSON keys line up with `Thresholds.load`.
FIELDS = {
    "trigram_repeat": "trigram_repeat_max",
    "max_sent_repeat": "max_sent_repeat_max",
    "distinct_sent_ratio": "distinct_sent_ratio_min",
    "ttr": "ttr_min",
}


def youden_j(
    values: list[float], labels: list[bool], threshold: float, direction: str
) -> float:
    """TPR - FPR at this threshold. `direction` is ">" or "<"."""
    tp = fp = fn = tn = 0
    for v, lab in zip(values, labels, strict=True):
        tripped = (v > threshold) if direction == ">" else (v < threshold)
        if lab and tripped:
            tp += 1
        elif lab and not tripped:
            fn += 1
        elif tripped:
            fp += 1
        else:
            tn += 1
    tpr = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    return tpr - fpr


def best_threshold(
    values: list[float], labels: list[bool], direction: str, n_steps: int = 200
) -> tuple[float, float]:
    """Grid-sweep candidates between min and max; return (threshold, J).

    Degenerate input (all values equal) returns (value, 0.0) — no discrimination
    possible at that metric, downstream calibration just keeps the default."""
    lo, hi = min(values), max(values)
    if lo == hi:
        return lo, 0.0
    candidates = [lo + (hi - lo) * i / n_steps for i in range(n_steps + 1)]
    scores = [(thr, youden_j(values, labels, thr, direction)) for thr in candidates]
    return max(scores, key=lambda pair: pair[1])


def calibrate(pairs: list[tuple[str, bool]]) -> dict[str, float]:
    """`pairs` = [(text, is_degenerate), ...]. Returns {Thresholds-field: value}.

    Decoupled from the persona-debate Turn dataclass so the function is
    testable on synthetic data and reusable for future calibration corpora."""
    labels = [is_degen for _, is_degen in pairs]
    feats_cache = [text_features(text) for text, _ in pairs]
    out: dict[str, float] = {}
    for metric, direction in DIRECTIONS.items():
        values = [f[metric] for f in feats_cache]
        thr, _ = best_threshold(values, labels, direction)
        out[FIELDS[metric]] = round(thr, 4)
    return out


def write_thresholds(thresholds: dict[str, float], path: Path) -> None:
    path.write_text(
        json.dumps(thresholds, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:  # pragma: no cover - live-evidence launcher
    from scripts.debate_analysis import load
    turns = load()
    pairs = [(t.text, t.size < 2.0) for t in turns]
    thresholds = calibrate(pairs)
    write_thresholds(thresholds, OUT)
    print(f"Wrote {OUT.relative_to(ROOT)}: {thresholds}")


if __name__ == "__main__":
    main()
