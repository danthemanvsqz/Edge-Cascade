"""TypeScript escalation gate -- the TS parity of cascade.verifier (BACKLOG #7).

The Python gate (cascade.verifier) extracts a fenced Python block and AST-
compiles it: a SYNTAX check, not name resolution. This is the TS equivalent --
extract a fenced TypeScript block and run it through the TS compiler's single-
file transpile (dashboard/scripts/ts-syntax-check.mjs), which reports SYNTACTIC
errors only. Unresolved imports/types PASS, exactly as the Python gate ignores
undefined names -- so a self-contained drafted snippet that references an
out-of-scope type still gates on its own syntax.

Closes the edge-verify TS gap: TS drafts could never pass the Python-only gate,
so every TS route capped (0/3 this session). Shells to Node (the TS compiler
lives in dashboard/node_modules); it transpiles, never execs, the candidate.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from cascade.verifier import Verdict

# A fenced ```typescript / ```ts block (the Python verifier's _FENCE matches
# ```python/```py/bare, deliberately NOT this -- the language dispatch is the
# point). Module-level so it compiles once.
_TS_FENCE = re.compile(r"```(?:typescript|ts)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

_ROOT = Path(__file__).resolve().parent.parent
# The checker sits under dashboard/ so Node resolves `typescript` from
# dashboard/node_modules via its own module-location chain (ESM ignores cwd).
_CHECKER = _ROOT / "dashboard" / "scripts" / "ts-syntax-check.mjs"


def extract_ts(text: str) -> str | None:
    """The longest fenced ```typescript / ```ts block, or None."""
    blocks = _TS_FENCE.findall(text)
    if not blocks:
        return None
    return max(blocks, key=len).strip()


def is_typescript(text: str) -> bool:
    """True iff the text carries a fenced TypeScript block (the gate-dispatch
    signal: a TS draft must be gated as TS, not run through the Python AST)."""
    return _TS_FENCE.search(text) is not None


def verify_ts(text: str) -> Verdict:
    """Gate a TS draft on its syntax. No block => fail (untrustworthy); a Node/
    checker failure => fail-soft with the reason (never raises)."""
    code = extract_ts(text)
    if code is None:
        return Verdict(False, False, "no fenced TypeScript block in response")
    try:
        proc = subprocess.run(
            ["node", str(_CHECKER)],
            input=code,
            capture_output=True,
            text=True,
            cwd=str(_CHECKER.parent),
            timeout=20,
        )
        result = json.loads(proc.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return Verdict(False, True, f"ts checker unavailable: {exc}")
    if result.get("passed"):
        return Verdict(True, True, "code parses")
    return Verdict(False, True, f"syntax error: {result.get('reason', '')}")
