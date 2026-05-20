"""scripts/snapshot_evidence.py — selection, output structure, and idempotent
dup-suffixing. Synthetic .rec via the real dump_record; no hardware / no
model calls."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/ is not a package; load by path.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import snapshot_evidence as S  # noqa: E402

sys.path.pop(0)

from cascade.logfmt import dump_record  # noqa: E402


def _write(runs: Path, stem: str, recs: list[dict]) -> None:
    p = runs / f"{stem}.rec"
    with open(p, "ab") as fh:
        for i, r in enumerate(recs):
            fh.write(dump_record(i, r))


def _t(tool: str, ts: str, run_id: str = "r", result: str = '{"ok": true}',
       latency_ms: str = "10.0") -> dict:
    return {"tool": tool, "ok": "true", "ts": ts, "run_id": run_id,
            "latency_ms": latency_ms, "result": result}


def _snap(tmp: Path, **kw) -> Path:
    runs, out = tmp / "runs", tmp / "out"
    runs.mkdir(exist_ok=True)
    base = {"runs_dir": runs, "out_dir": out, "gap": 30.0,
            "latest": None, "since": None, "episode": None}
    base.update(kw)
    p = S.snapshot(**base)
    assert p is not None, "snapshot should have produced a dir"
    return p


def test_latest_default_picks_most_recent_episode(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    # Two episodes split by gap > 30s (90s gap between 110 and 500).
    _write(runs, "edge-npu", [
        _t("route", "100.0"), _t("draft", "110.0"),
        _t("route", "500.0"), _t("draft", "510.0"),
    ])
    out_dir = tmp_path / "out"
    res = S.snapshot(runs_dir=runs, out_dir=out_dir, gap=30.0,
                     latest=1, since=None, episode=None)
    assert res is not None and res.exists()
    m = json.loads((res / "dashboard.json").read_text(encoding="utf-8"))
    # Only the second episode (ts 500/510) included.
    assert m["per_server"]["edge-npu"]["calls"] == 2
    assert m["episodes"] == 1
    summaries = json.loads((res / "replay.json").read_text(encoding="utf-8"))
    assert len(summaries) == 1


def test_episode_selector_picks_by_one_based_index(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    _write(runs, "edge-npu", [_t("route", "100.0"), _t("route", "500.0")])
    res = S.snapshot(runs_dir=runs, out_dir=tmp_path / "out", gap=30.0,
                     latest=None, since=None, episode=1)
    m = json.loads((res / "dashboard.json").read_text(encoding="utf-8"))
    assert m["per_server"]["edge-npu"]["calls"] == 1     # only the oldest


def test_since_filters_by_episode_start_ts(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    _write(runs, "edge-npu", [
        _t("route", "100.0"),
        _t("route", "500.0"),
        _t("route", "1000.0"),
    ])
    res = S.snapshot(runs_dir=runs, out_dir=tmp_path / "out", gap=30.0,
                     latest=None, since=400.0, episode=None)
    m = json.loads((res / "dashboard.json").read_text(encoding="utf-8"))
    assert m["per_server"]["edge-npu"]["calls"] == 2      # 500 + 1000


def test_manifest_records_spend_invariant_and_runids(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    _write(runs, "edge-npu", [_t("route", "100.0", run_id="aaa")])
    _write(runs, "edge-gpu", [_t("generate", "110.0", run_id="bbb")])
    res = S.snapshot(runs_dir=runs, out_dir=tmp_path / "out", gap=30.0,
                     latest=1, since=None, episode=None)
    text = (res / "MANIFEST.md").read_text(encoding="utf-8")
    assert "edge-cloud calls" in text
    assert "$0.00" in text
    assert "clean" in text.lower()
    assert "aaa" in text and "bbb" in text                 # run_ids listed


def test_idempotent_suffix_on_duplicate(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    _write(runs, "edge-npu", [_t("route", "100.0")])
    out = tmp_path / "out"
    first = S.snapshot(runs_dir=runs, out_dir=out, gap=30.0,
                       latest=1, since=None, episode=None)
    second = S.snapshot(runs_dir=runs, out_dir=out, gap=30.0,
                        latest=1, since=None, episode=None)
    assert first is not None and second is not None and first != second
    assert second.name.startswith(first.name) and second.name.endswith("-1")


def test_empty_runs_returns_none(tmp_path: Path, capsys) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    res = S.snapshot(runs_dir=runs, out_dir=tmp_path / "out", gap=30.0,
                     latest=1, since=None, episode=None)
    assert res is None
    err = capsys.readouterr().err
    assert "no telemetry" in err


def test_episode_out_of_range_raises_systemexit(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    _write(runs, "edge-npu", [_t("route", "100.0")])
    with pytest.raises(SystemExit):
        S.snapshot(runs_dir=runs, out_dir=tmp_path / "out", gap=30.0,
                   latest=None, since=None, episode=5)
