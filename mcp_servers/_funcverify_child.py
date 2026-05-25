"""Sandbox child for edge-verify.verify_functional.

validate_log.py deliberately exec()s model output and is documented as an
OFFLINE tool that must NEVER run in the cascade hot path. The MCP server
honours that boundary by never importing it: it spawns THIS module as a
throwaway subprocess (timeout-bounded by the parent) so the untrusted exec
happens in a process that is killed, not in the long-lived server.

Protocol:  stdin  = JSON {"text": <model answer>, "dsl": <optional override>}
           stdout = JSON {"ran", "applicable", "passed", "checked", "failures"}
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import validate_log as vl  # noqa: E402  (path set above)


def _evaluate(text: str, dsl_override: str | None) -> dict:
    """Run the gate and return the result dict. The `vl.run` call below exec()s
    the untrusted candidate, which may itself print() -- callers MUST run this
    with stdout redirected so those prints don't corrupt the JSON result stream."""
    code = vl.extract_code(text)
    if code is None:
        return {"ran": False, "applicable": False, "passed": False,
                "checked": 0, "failures": [],
                "reason": "no usable code block in answer"}

    dsl_text = dsl_override if dsl_override else vl.DSL.read_text(encoding="utf-8")
    blocks = vl.parse_dsl(dsl_text)
    checks = vl.run(code, blocks)  # this is the exec; isolated to this process

    failures = [
        {"symbol": c.sym, "expr": c.expr,
         "observed": c.observed, "requirement": c.requirement}
        for c in checks if not c.ok
    ]
    applicable = len(checks) > 0
    return {
        "ran": True,
        "applicable": applicable,
        # passed only if something actually exercised the code AND nothing
        # failed. No matching DSL block => not applicable, not "passed".
        "passed": applicable and not failures,
        "checked": len(checks),
        "failures": failures,
    }


def main() -> None:
    req = json.load(sys.stdin)
    real_stdout = sys.stdout
    # The candidate exec'd inside _evaluate can print() to stdout; capture that
    # into a throwaway buffer so ONLY the result JSON reaches the real stdout
    # (otherwise the parent's json.loads sees "<candidate noise>{json}" and
    # crashes -- observed on a topological_sort candidate, 2026-05-25).
    with contextlib.redirect_stdout(io.StringIO()):
        result = _evaluate(req.get("text", ""), req.get("dsl"))
    json.dump(result, real_stdout)


if __name__ == "__main__":
    main()
