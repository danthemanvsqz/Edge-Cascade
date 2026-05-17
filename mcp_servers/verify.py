"""edge-verify MCP server -- the deterministic gate (Intel CPU, NO model).

Pillar 3 of the architecture made concrete: a non-LLM, 100%-reproducible gate
every local answer must pass before the agent trusts it. Breaks the
self-correction blind spot -- the compiler grades, not a model.

Tools:
  verify_syntax     fast AST/compile gate (cascade.verifier, never exec)
  verify_functional checks.dsl assertions, run in a killed subprocess sandbox
  repair_prompt     format the model-legible fix request (cascade.feedback)

Run:  python -m mcp_servers.verify        (stdio transport)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ._rec import make_recorder, recorded

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade.feedback import CheckFailure, build_repair_prompt  # noqa: E402
from cascade.verifier import verify  # noqa: E402

mcp = FastMCP("edge-verify")
_REC = make_recorder("edge-verify")

# Hard cap so a pathological/looping candidate can never wedge the gate. The
# sandbox is a separate process; on timeout we kill it and report a failure.
_FUNC_TIMEOUT_S = 20


@mcp.tool()
@recorded(_REC)
def verify_syntax(text: str) -> dict:
    """Fast gate: extract the fenced block and AST-compile it (never exec).

    Returns {passed, has_code, reason}. A syntax error or a missing code block
    means the tier's answer is untrustworthy and the cascade must escalate.
    """
    v = verify(text)
    return {"passed": v.passed, "has_code": v.has_code, "reason": v.reason}


@mcp.tool()
@recorded(_REC)
def verify_functional(text: str, dsl: str | None = None) -> dict:
    """Functional gate: run checks.dsl assertions against the candidate.

    The candidate is exec()'d in a throwaway subprocess (killed on a
    20s timeout) -- never in this server process. Pass `dsl` to override the
    repo's checks.dsl. Returns {ran, applicable, passed, checked, failures[]}.
    `applicable=False` means no DSL block matched; treat as syntax-gate-only.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "mcp_servers._funcverify_child"],
            input=json.dumps({"text": text, "dsl": dsl}),
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=_FUNC_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {
            "ran": True, "applicable": True, "passed": False, "checked": 0,
            "failures": [{"symbol": "<sandbox>", "expr": "completes",
                          "observed": f"timed out after {_FUNC_TIMEOUT_S}s",
                          "requirement": "candidate must terminate"}],
        }
    if proc.returncode != 0 or not proc.stdout.strip():
        return {
            "ran": False, "applicable": False, "passed": False, "checked": 0,
            "failures": [{"symbol": "<sandbox>", "expr": "exits cleanly",
                          "observed": (proc.stderr or "no output").strip()[:500],
                          "requirement": "sandbox must run"}],
        }
    return json.loads(proc.stdout)


@mcp.tool()
@recorded(_REC)
def repair_prompt(task: str, code: str, failures: list[dict]) -> str:
    """Build the model-legible repair request from validation failures.

    `failures`: [{expr, observed, requirement?}] -- e.g. the `failures` array
    returned by verify_functional, or a syntax failure shaped the same way.
    """
    fs = [
        CheckFailure(
            expr=f.get("expr", ""),
            observed=f.get("observed", ""),
            requirement=f.get("requirement", ""),
        )
        for f in failures
    ]
    return build_repair_prompt(task, code, fs)


if __name__ == "__main__":
    mcp.run()
