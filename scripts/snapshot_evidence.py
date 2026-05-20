"""Run-scoped evidence snapshot for cascade telemetry.

`runs/*.rec` is the recorder's ground truth, but it is append-only and gets
overwritten/extended by the next run -- if you do not snapshot now, this run's
evidence is lost the moment anything else touches the MCP tools. This script
reads `runs/*.rec`, scopes to selected episode(s), and writes
`evidence/<UTC-date>/{replay.json, dashboard.json, MANIFEST.md}` as the
committed proof of a run (what Part-C evidence is drawn from).

Offline. No model calls. Read-only over `runs/` + the git tree.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import replay as R  # noqa: E402
from dashboard import compute_metrics  # noqa: E402

EVIDENCE_ROOT = ROOT / "evidence"


# ---- pure helpers ----------------------------------------------------------

def _ts(rec: dict) -> float | None:
    raw = rec.get("ts")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _bounds(records: list[dict]) -> tuple[float | None, float | None]:
    """(min_ts, max_ts) over any timed records in the slice; None if untimed."""
    ts = [t for t in (_ts(r) for r in records) if t is not None]
    return (min(ts), max(ts)) if ts else (None, None)


def _select(
    episodes: list[list[dict]], *,
    latest: int | None, since: float | None, episode: int | None,
) -> list[list[dict]]:
    """Pick the target episodes. Mutually-exclusive selectors; default = latest 1."""
    if episode is not None:
        if not 1 <= episode <= len(episodes):
            raise SystemExit(
                f"--episode {episode} out of range (1..{len(episodes)})")
        return [episodes[episode - 1]]
    if since is not None:
        return [
            ep for ep in episodes
            if (s := _bounds(ep)[0]) is not None and s >= since
        ]
    n = latest if latest is not None else 1
    return episodes[-n:] if n > 0 else []


def _git(*args: str) -> str | None:
    """Run `git -C <repo> <args>`; return stdout stripped, or None on failure."""
    try:
        r = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return r.stdout.strip() or None
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _unique_run_ids(records: list[dict]) -> list[str]:
    return sorted({r["run_id"] for r in records if r.get("run_id")})


def _utc_date(ts: float | None) -> str:
    """UTC YYYY-MM-DD for ts, or today (UTC) if untimed."""
    t = ts if ts is not None else datetime.now(UTC).timestamp()
    return datetime.fromtimestamp(t, tz=UTC).strftime("%Y-%m-%d")


def _alloc_dir(root: Path, date: str) -> Path:
    """Return a fresh `<root>/<date>[-N]/` path; idempotent dup-suffixing."""
    base = root / date
    if not base.exists():
        return base
    i = 1
    while (cand := root / f"{date}-{i}").exists():
        i += 1
    return cand


def _format_manifest(
    selected: list[list[dict]], records: list[dict], metrics: dict,
    cascade_sha: str | None, vinyl_sha: str | None,
) -> str:
    s, e = _bounds(records)
    if s is not None and e is not None:
        span = (
            f"  {datetime.fromtimestamp(s, tz=UTC).isoformat()}\n"
            f"  → {datetime.fromtimestamp(e, tz=UTC).isoformat()}\n"
            f"  ({e - s:.1f}s)\n"
        )
    else:
        span = "  (untimed)\n"
    sp = metrics["spend"]
    clean = "✅ clean" if sp["clean"] else "🚨 NONZERO"
    per = metrics["per_server"]
    tools = "  ".join(
        f"{srv.split('-')[-1]}={per[srv]['calls']}"
        for srv in ("edge-npu", "edge-gpu", "edge-verify", "edge-cloud")
    )
    run_ids = _unique_run_ids(records)
    lines = [
        "# Cascade evidence snapshot",
        "",
        f"**Captured:** {datetime.now(UTC).isoformat()}",
        "",
        "## Scope",
        f"- Episodes selected: {len(selected)} of {metrics['episodes']}",
        f"- Records (scoped): {len(records)}",
        "- Time span:",
        span.rstrip(),
        f"- Distinct run_ids: {len(run_ids)}",
        "",
        "## Provenance",
        f"- edge-cascade HEAD: `{cascade_sha or '?'}`",
        f"- projects/vinyl last touched: `{vinyl_sha or '(not present)'}`",
        "",
        "## Spend invariant (the load-bearing one)",
        f"- edge-cloud calls: **{sp['cloud_calls']}**",
        f"- USD: **${sp['usd']:.2f}**",
        f"- Status: **{clean}**",
        "",
        "## Tool counts",
        f"  {tools}",
        "",
        "## Run IDs",
        *(f"- `{rid}`" for rid in run_ids),
        "",
        "## Files",
        "- `replay.json` — per-episode summaries (`replay.episode_summary`)",
        "- `dashboard.json` — scoped `dashboard.compute_metrics`",
        "",
    ]
    return "\n".join(lines)


# ---- snapshot ---------------------------------------------------------------

def snapshot(
    *, runs_dir: Path, out_dir: Path, gap: float,
    latest: int | None, since: float | None, episode: int | None,
) -> Path | None:
    """Read runs_dir, select episodes, write a scoped evidence dir. Returns
    the dir path on success, None on no-op (empty/no match)."""
    streams = R.load_streams(runs_dir)
    if not streams:
        print(f"no telemetry: {runs_dir}/*.rec is empty", file=sys.stderr)
        return None
    records = R.tag_and_merge(streams)
    episodes = R.split_episodes(records, gap)
    if not episodes:
        print("no episodes parsed from streams", file=sys.stderr)
        return None
    selected = _select(
        episodes, latest=latest, since=since, episode=episode)
    if not selected:
        print("no episodes match the selector", file=sys.stderr)
        return None
    scoped = [r for ep in selected for r in ep]
    metrics = compute_metrics(scoped, gap)
    summaries = [R.episode_summary(ep) for ep in selected]
    cascade_sha = _git("rev-parse", "HEAD")
    vinyl_dir = ROOT / "projects" / "vinyl"
    vinyl_sha = (
        _git("log", "-1", "--format=%H", "--", "projects/vinyl")
        if vinyl_dir.exists() else None
    )
    s0, _ = _bounds(scoped)
    out = _alloc_dir(out_dir, _utc_date(s0))
    out.mkdir(parents=True, exist_ok=True)
    (out / "replay.json").write_text(
        json.dumps(summaries, indent=2), encoding="utf-8")
    (out / "dashboard.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8")
    (out / "MANIFEST.md").write_text(
        _format_manifest(selected, scoped, metrics, cascade_sha, vinyl_sha),
        encoding="utf-8",
    )
    print(out)
    return out


# ---- cli --------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description="Snapshot scoped cascade telemetry into evidence/<UTC-date>/",
    )
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--latest", type=int, metavar="N",
                     help="capture the most recent N episodes (default 1)")
    sel.add_argument("--since", type=float, metavar="TS",
                     help="capture episodes whose start ts >= TS (unix seconds)")
    sel.add_argument("--episode", type=int, metavar="I",
                     help="capture only episode I (1-based, oldest->newest)")
    ap.add_argument("--runs-dir", type=Path, default=R.RUNS, metavar="DIR")
    ap.add_argument("--out-dir", type=Path, default=EVIDENCE_ROOT, metavar="DIR")
    ap.add_argument(
        "--gap", type=float, default=R.DEFAULT_GAP, metavar="SEC",
        help=f"episode idle-gap split (default {R.DEFAULT_GAP:g}s)")
    args = ap.parse_args(argv)
    snapshot(
        runs_dir=args.runs_dir, out_dir=args.out_dir, gap=args.gap,
        latest=args.latest, since=args.since, episode=args.episode,
    )


if __name__ == "__main__":
    main()
