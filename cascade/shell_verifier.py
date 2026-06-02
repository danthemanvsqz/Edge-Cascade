"""Shell / git escalation gate — the bash+git parity of cascade.verifier.

Extracts fenced git / bash blocks from LLM output and verifies them:
  - git: structural regex check (must start with `git <verb>`)
  - bash: subprocess `bash -n` stdin syntax check (fail-soft if bash absent)

Registers both verifiers in cascade.gate at import time (VR-2).
"""
from __future__ import annotations

import re
import subprocess

from cascade.verifier import Verdict

_GIT_FENCE = re.compile(r"```(?:git)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_BASH_FENCE = re.compile(
    r"```(?:bash|sh|shell)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE
)
_GIT_CMD = re.compile(r"^git\s+\S+")


def extract_git(text: str) -> str | None:
    """The longest fenced ```git block, or None."""
    blocks = _GIT_FENCE.findall(text)
    if not blocks:
        return None
    return max(blocks, key=len).strip()


def extract_shell(text: str) -> str | None:
    """The longest fenced ```bash / ```sh / ```shell block, or None."""
    blocks = _BASH_FENCE.findall(text)
    if not blocks:
        return None
    return max(blocks, key=len).strip()


def verify_git(text: str) -> Verdict:
    """Gate a git draft on its structure.

    No block   → fail (untrustworthy; the model didn't produce a git command).
    Bad format → fail with reason.
    Starts with ``git <verb>`` → pass.
    """
    code = extract_git(text)
    if code is None:
        return Verdict(False, False, "no fenced git block in response")
    first_line = code.splitlines()[0] if code else ""
    if not _GIT_CMD.match(first_line):
        return Verdict(
            False, True, f"expected 'git <verb>', got: {first_line!r}"
        )
    return Verdict(True, True, "git command valid")


def verify_shell(text: str) -> Verdict:
    """Gate a bash draft via `bash -n` stdin check.

    No block        → fail (untrustworthy).
    bash -n PASS    → pass.
    bash -n FAIL    → fail with first stderr line.
    bash unavailable → fail-soft (same pattern as ts_verifier).
    """
    code = extract_shell(text)
    if code is None:
        return Verdict(False, False, "no fenced bash block in response")
    try:
        proc = subprocess.run(
            ["bash", "-n"],
            input=code,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Verdict(False, True, f"bash unavailable: {exc}")
    if proc.returncode != 0:
        stderr_line = proc.stderr.splitlines()[0] if proc.stderr.strip() else "syntax error"
        return Verdict(False, True, f"syntax error: {stderr_line}")
    return Verdict(True, True, "shell syntax valid")


# ---------------------------------------------------------------------------
# Self-registration into the gate registry (VR-2).
# ---------------------------------------------------------------------------
from cascade.gate import register  # noqa: E402

register("git", verify_git)
register("bash", verify_shell)
