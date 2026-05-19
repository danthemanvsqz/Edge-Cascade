"""Reconstruct the cascade's true ordered timeline from the structured logs.

The chat narration is unreliable about *which tier did what* (see RUNBOOK's
honesty rule). The `runs/*.rec` streams are not -- every MCP tool call and
every cli.py task appends one deterministic, length-framed record
(cascade/logfmt.py). This is a READ-ONLY viewer over those streams: merge the
five files, order them by wall-clock `ts`, split into episodes on an idle
gap, and print what actually happened. No re-run, no judgement -- just the
auditable ground truth.

Why merge + ts-sort: the four per-server files are separate single-writer
processes; `_seq` resets to 0 on every server restart, so only `ts` orders
records *across* files. Records written before the ts/run_id recorder change
have neither -- they fall back to file-append order and form a "legacy"
episode (clearly marked, not silently mis-ordered).

Sessionization is a heuristic, NOT ground truth: `run_id` is per server
*process* (the NPU server stays resident across many tasks), so it != "one
task". An episode = events on the merged stream split where the idle gap
exceeds --gap (default 30s). `cascade.rec` records are the cli.py path and DO
have exact task boundaries, so each is isolated as its own episode.

Offline dev tool, like validate_log.py -- never wired into the hot path.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from cascade.logfmt import parse_stream

ROOT = Path(__file__).parent
RUNS = ROOT / "runs"
# The five streams: four per-server MCP recorders + the cli.py orchestrator.
SERVERS = ("edge-npu", "edge-gpu", "edge-verify", "edge-cloud")
DEFAULT_GAP = 30.0


# ---- load + merge + order ---------------------------------------------------

def load_streams(runs_dir: Path) -> dict[str, str]:
    """{source_stem: raw_text} for every runs/*.rec that exists (FS touch)."""
    return {
        p.stem: p.read_text(encoding="utf-8")
        for p in sorted(runs_dir.glob("*.rec"))
        if p.stat().st_size
    }


def _ts(rec: dict) -> float | None:
    """Wall-clock seconds, or None for a legacy record written pre-ts."""
    raw = rec.get("ts")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _is_cascade(rec: dict) -> bool:
    """A cli.py orchestrator record (exact task boundary), not a tool call."""
    return rec.get("_src") == "cascade" or "final_tier" in rec


def tag_and_merge(streams: dict[str, str]) -> list[dict]:
    """Parse every stream, tag each record with its source + a global read
    index, and return one list ordered for replay. Pure (no FS).

    Order key: untimed (legacy) records FIRST -- they were written before the
    ts/run_id recorder change, so they are by definition the oldest; ordering
    them ahead of the timed records keeps the stream chronological and makes
    `--last N` return the genuinely most-recent activity. Within each group,
    ties break by read index (stable file-append order).
    """
    merged: list[dict] = []
    for src, text in sorted(streams.items()):
        for rec in parse_stream(text):
            merged.append({**rec, "_src": src, "_idx": len(merged)})
    return sorted(
        merged,
        key=lambda r: (_ts(r) is not None, _ts(r) or 0.0, r["_idx"]),
    )


# ---- episode splitting (pure; the unit-tested core) -------------------------

def _episode_break(prev: dict, cur: dict, gap: float) -> bool:
    """True when `cur` starts a new episode. Heuristic, documented as such:
    cascade.rec records are exact task boundaries (always isolated); on the
    agentic stream an idle gap > `gap` splits; legacy (untimed) records split
    when run_id changes or at the timed<->untimed seam."""
    if _is_cascade(prev) or _is_cascade(cur):
        return True
    pt, ct = _ts(prev), _ts(cur)
    if pt is None or ct is None:
        if (pt is None) != (ct is None):
            return True  # the timed -> legacy seam
        return prev.get("run_id", "?") != cur.get("run_id", "?")
    return (ct - pt) > gap


def split_episodes(records: list[dict], gap: float) -> list[list[dict]]:
    """Group the ordered stream into episodes on the break heuristic. Pure."""
    episodes: list[list[dict]] = []
    for rec in records:
        if not episodes or _episode_break(episodes[-1][-1], rec, gap):
            episodes.append([rec])
        else:
            episodes[-1].append(rec)
    return episodes


# ---- record -> human line ---------------------------------------------------

def _j(rec: dict, key: str):
    """Best-effort decode of a JSON-serialised field; raw string on failure."""
    raw = rec.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _clock(rec: dict) -> str:
    t = _ts(rec)
    return time.strftime("%H:%M:%S", time.localtime(t)) if t else "--:--:--"


def is_failure(rec: dict) -> bool:
    """A record that represents something going wrong: a tool error, or a
    verify gate that did not pass."""
    if rec.get("ok") == "false" or "error" in rec:
        return True
    if str(rec.get("tool", "")).startswith("verify"):
        res = _j(rec, "result")
        return isinstance(res, dict) and res.get("passed") is False
    return False


def _describe(rec: dict) -> str:
    """One compact line of what this record did (tier-accurate, no narration)."""
    src, tool = rec.get("_src", "?"), rec.get("tool", "")
    res = _j(rec, "result")

    if _is_cascade(rec):
        q = (rec.get("query", "") or "").splitlines()[0][:70]
        return (f'CLI TASK  q="{q}"  -> {rec.get("final_tier","?")} '
                f'({rec.get("total_latency_s","?")}s)')
    if rec.get("ok") == "false":
        return f"{src}.{tool}  ERROR: {rec.get('error','?')[:90]}"
    if tool == "repair_prompt":
        return f"repair_prompt built ({len(rec.get('result',''))} chars)"
    if not isinstance(res, dict):
        body = "" if res is None else str(res).splitlines()[0][:70]
        return f"{src}.{tool}  -> {body}" if body else f"{src}.{tool}"

    if tool == "route":
        return (f"route -> difficulty={res.get('difficulty')} "
                f"category={res.get('category')} "
                f"({res.get('device')} {res.get('latency_s')}s)")
    if tool == "draft":
        if not res.get("available"):
            return "draft UNAVAILABLE"
        return (f"draft ({res.get('device')} {res.get('latency_s')}s) "
                f"-> {len(res.get('text',''))} chars")
    if tool == "generate":
        if not res.get("available"):
            return "gpu.generate UNAVAILABLE"
        return (f"gpu.generate ({res.get('model')} {res.get('latency_s')}s, "
                f"{res.get('tokens_per_s')} tok/s) "
                f"-> {len(res.get('text',''))} chars")
    if tool == "verify_syntax":
        v = "PASS" if res.get("passed") else "FAIL"
        return f"verify_syntax {v} ({res.get('reason')})"
    if tool == "verify_functional":
        if not res.get("applicable"):
            return "verify_functional n/a (no checks.dsl symbol matched)"
        if res.get("passed"):
            return f"verify_functional PASS (checked={res.get('checked')})"
        fails = res.get("failures") or []
        first = fails[0] if fails else {}
        return (f"verify_functional FAIL: {first.get('symbol','?')} -- "
                f"{first.get('observed','?')}")
    if src == "edge-cloud":
        spent = res.get("usd_spent", res.get("est_cost_usd", 0.0))
        return (f"cloud.{tool}: ${spent} spent "
                f"(calls {res.get('calls_used','?')}/{res.get('calls_max','?')})")
    return f"{src}.{tool} ok ({rec.get('latency_ms','?')}ms)"


# ---- episode summary + render -----------------------------------------------

def episode_summary(ep: list[dict]) -> dict:
    """Machine view of one episode (also drives --failures-only / --json)."""
    times = [t for t in (_ts(r) for r in ep) if t is not None]
    runs = sorted({r.get("run_id", "?") for r in ep})
    return {
        "records": len(ep),
        "start": min(times) if times else None,
        "end": max(times) if times else None,
        "run_ids": runs,
        "legacy": not times,
        "has_failure": any(is_failure(r) for r in ep),
        "events": [
            {"src": r.get("_src"), "tool": r.get("tool"),
             "ts": _ts(r), "run_id": r.get("run_id", "?"),
             "ok": r.get("ok"), "desc": _describe(r)}
            for r in ep
        ],
    }


def render_episode(n: int, ep: list[dict]) -> str:
    s = episode_summary(ep)
    span = "legacy / pre-ts" if s["legacy"] else (
        f'{time.strftime("%H:%M:%S", time.localtime(s["start"]))}'
        f'..{time.strftime("%H:%M:%S", time.localtime(s["end"]))}')
    flag = "  [FAIL]" if s["has_failure"] else ""
    runs = ",".join(r[:8] for r in s["run_ids"])
    head = (f"=== Episode {n} === {span}  run={runs}  "
            f"{s['records']} rec{flag}")
    lines = [head, "-" * len(head)]
    for rec in ep:
        lines.append(f"  {_clock(rec)}  {_describe(rec)}")
        if _is_cascade(rec) and rec.get("trace"):
            lines += [f"      | {t}" for t in rec["trace"].splitlines()]
    return "\n".join(lines)


# ---- cli --------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Replay the cascade's true timeline from runs/*.rec")
    ap.add_argument("--last", type=int, metavar="N",
                    help="show only the last N episodes")
    ap.add_argument("--run", metavar="RUN_ID",
                    help="only records from this run_id")
    ap.add_argument("--server", metavar="NAME",
                    help=f"only this stream ({', '.join(SERVERS)}, cascade)")
    ap.add_argument("--failures-only", action="store_true",
                    help="only episodes containing a failure")
    ap.add_argument("--gap", type=float, default=DEFAULT_GAP, metavar="SEC",
                    help=f"episode idle-gap split (default {DEFAULT_GAP:g}s)")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable episode summaries")
    args = ap.parse_args()

    records = tag_and_merge(load_streams(RUNS))
    if args.server:
        records = [r for r in records if r.get("_src") == args.server]
    if args.run:
        records = [r for r in records if r.get("run_id") == args.run]

    episodes = split_episodes(records, args.gap)
    if args.failures_only:
        episodes = [e for e in episodes if any(is_failure(r) for r in e)]
    if args.last is not None:
        episodes = episodes[-args.last:]

    if args.json:
        print(json.dumps([episode_summary(e) for e in episodes], indent=2))
        return
    if not episodes:
        print("no records match (runs/*.rec empty or filtered out)")
        return
    print(f"{len(episodes)} episode(s) | gap={args.gap:g}s | "
          f"source: runs/*.rec (not chat narration)\n")
    for i, ep in enumerate(episodes, 1):
        print(render_episode(i, ep))
        print()


if __name__ == "__main__":
    main()
