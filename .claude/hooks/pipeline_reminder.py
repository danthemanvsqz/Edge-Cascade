"""UserPromptSubmit hook: keep the route-every-line-of-code rule + a live
routing scoreboard in front of the agent every turn.

Why this exists: the rule ("every line of code goes through the Canvas pipeline
first; NPU-first, win/lose logger last") lived only in memory, which is advisory
recall the model can skip under shipping momentum -- and the skip was invisible.
This hook is deterministic (the harness runs it) and surfaces *how recently* the
pipeline actually ran, so forgetting shows up as a stale scoreboard instead of
going unnoticed.

Advisory by design: it injects context, never blocks a prompt. It is wrapped so
any failure degrades to a bare reminder (or silence) -- a metrics hiccup must
never wedge prompt submission.

Reads runs/cascade.rec (the win/lose logger's lane). Stdlib only.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

# repo root = <root>/.claude/hooks/pipeline_reminder.py -> parents[2]
ROOT = Path(__file__).resolve().parents[2]
REC = ROOT / "runs" / "cascade.rec"

RULE = (
    "Edge-cascade routing rule: every line of code goes through the Canvas "
    "pipeline FIRST (NPU-first -> draft -> gate -> bounded GPU repair -> "
    "win/lose logger LAST). Do not hand-write from-scratch code that skips it.\n"
    "Route with one call:\n"
    "  uv run python scripts/mesh_solve_canvas.py --topology budget \"<task>\"\n"
    "For large multi-part tasks, decompose FIRST (you reason the sub-tasks), "
    "then fan-out:\n"
    "  uv run python scripts/mesh_solve_canvas.py --topology budget_fanout "
    "\"sub1\" \"sub2\" \"sub3\"\n"
    "On `capped->tier3` you (Tier 3) take over; surgical edits to existing code "
    "are the only by-hand exception. See /edge-cascade + CLAUDE.md."
)

# a bare logfmt ts value line, e.g. "1779847784.113"
_TS = re.compile(r"^\d{10}\.\d+$")


def _scoreboard() -> str:
    """Best-effort routing metrics from cascade.rec; never raises.

    Shows all-time totals alongside this session's W/L. A "session" is records
    after the most recent gap of > 30 minutes between consecutive timestamps.
    """
    try:
        if not REC.exists():
            return ("Pipeline scoreboard: no routed outcomes recorded yet "
                    "(runs/cascade.rec is empty) -- route the next coding task.")
        text = REC.read_text(encoding="utf-8", errors="replace")

        # Parse records: each %%END-delimited block carries a ts line + outcome.
        records: list[tuple[float, str | None]] = []
        for block in text.split("%%END"):
            block_ts: float | None = None
            for ln in block.splitlines():
                m = _TS.match(ln.strip())
                if m:
                    block_ts = float(m.group())
            if block_ts is None:
                continue
            if "done: WIN" in block:
                outcome: str | None = "WIN"
            elif "done: LOSE" in block:
                outcome = "LOSE"
            else:
                outcome = None
            records.append((block_ts, outcome))

        records.sort()
        total = len(records)
        wins_all = sum(1 for _, o in records if o == "WIN")
        losses_all = sum(1 for _, o in records if o == "LOSE")

        # Session: records after the most recent gap > 1800 s.
        session_start = 0
        for i in range(1, len(records)):
            if records[i][0] - records[i - 1][0] > 1800:
                session_start = i
        session = records[session_start:]
        wins_s = sum(1 for _, o in session if o == "WIN")
        losses_s = sum(1 for _, o in session if o == "LOSE")

        last_ts = records[-1][0] if records else 0.0
        if last_ts:
            mins = (time.time() - last_ts) / 60.0
            ago = f"{mins:.0f} min ago" if mins < 90 else f"{mins / 60:.1f} h ago"
            staleness = (" -- STALE; recent coding likely bypassed the pipeline"
                         if mins > 30 else "")
            last = f"last route {ago}{staleness}"
        else:
            last = "no timestamped route found"

        verdicts_all = f", {wins_all}W/{losses_all}L" if (wins_all or losses_all) else ""
        if wins_s + losses_s > 0:
            pct = round(wins_s / (wins_s + losses_s) * 100)
            session_str = f"session: {wins_s}W/{losses_s}L {pct}%"
        else:
            session_str = "session: no routes yet"

        return (f"Pipeline scoreboard: {total} routed outcomes{verdicts_all} "
                f"({session_str}); {last}.")
    except Exception:  # noqa: BLE001 -- metrics must never break prompt submit
        return ""


def main() -> None:
    parts = [RULE]
    board = _scoreboard()
    if board:
        parts.append(board)
    context = "\n\n".join(parts)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 -- last-resort guard; exit clean, no block
        sys.exit(0)
