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
    "  uv run python scripts/mesh_solve_canvas.py --topology balanced \"<task>\"\n"
    "On `capped->tier3` you (Tier 3) take over; surgical edits to existing code "
    "are the only by-hand exception. See /edge-cascade + CLAUDE.md."
)

# a bare logfmt ts value line, e.g. "1779847784.113"
_TS = re.compile(r"^\d{10}\.\d+$")


def _scoreboard() -> str:
    """Best-effort routing metrics from cascade.rec; never raises."""
    try:
        if not REC.exists():
            return ("Pipeline scoreboard: no routed outcomes recorded yet "
                    "(runs/cascade.rec is empty) -- route the next coding task.")
        text = REC.read_text(encoding="utf-8", errors="replace")
        total = text.count("%%END")
        wins = len(re.findall(r"done: WIN", text))
        losses = len(re.findall(r"done: LOSE", text))
        last_ts = max((float(m.group()) for ln in text.splitlines()
                       if (m := _TS.match(ln.strip()))), default=0.0)
        if last_ts:
            mins = (time.time() - last_ts) / 60.0
            ago = (f"{mins:.0f} min ago" if mins < 90
                   else f"{mins / 60:.1f} h ago")
            staleness = (" -- STALE; recent coding likely bypassed the pipeline"
                         if mins > 30 else "")
            last = f"last route {ago}{staleness}"
        else:
            last = "no timestamped route found"
        verdicts = (f", {wins}W/{losses}L logged"
                    if (wins or losses) else "")
        return f"Pipeline scoreboard: {total} routed outcomes{verdicts}; {last}."
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
