"""JavaScript escalation gate — the JS parity of cascade.ts_verifier.

Extracts a fenced JavaScript block and syntax-checks it via
`node --check --input-type=commonjs` stdin (no new npm deps; reuses the
same node binary as ts_verifier). Fail-soft when node is unavailable.

Registers the verifier in cascade.gate at import time (VR-3).
"""
from __future__ import annotations

import re
import subprocess

from cascade.verifier import Verdict

_JS_FENCE = re.compile(
    r"```(?:javascript|js)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE
)


def extract_js(text: str) -> str | None:
    """The longest fenced ```javascript / ```js block, or None."""
    blocks = _JS_FENCE.findall(text)
    if not blocks:
        return None
    return max(blocks, key=len).strip()


def verify_js(text: str) -> Verdict:
    """Gate a JS draft on its syntax.

    No block        → fail (untrustworthy).
    node PASS       → pass.
    node FAIL       → fail with first relevant stderr line.
    node unavailable → fail-soft (same pattern as ts_verifier).
    """
    code = extract_js(text)
    if code is None:
        return Verdict(False, False, "no fenced javascript block in response")
    try:
        proc = subprocess.run(
            ["node", "--check", "--input-type=commonjs"],
            input=code,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Verdict(False, True, f"node unavailable: {exc}")
    if proc.returncode == 0:
        return Verdict(True, True, "javascript syntax valid")
    stderr_line = proc.stderr.splitlines()[0] if proc.stderr.strip() else "syntax error"
    return Verdict(False, True, stderr_line)


# ---------------------------------------------------------------------------
# Self-registration into the gate registry (VR-3).
# ---------------------------------------------------------------------------
from cascade.gate import register  # noqa: E402

register("javascript", verify_js)
