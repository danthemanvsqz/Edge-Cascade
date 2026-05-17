"""Objective escalation gate.

For a code assistant the cheapest reliable signal is: does the produced code
parse? We extract the fenced Python block and compile it (AST only — we never
exec model output). A syntax failure or a missing code block means the tier's
answer is not trustworthy and the cascade should escalate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class Verdict:
    passed: bool
    has_code: bool
    reason: str


def extract_code(text: str) -> str | None:
    blocks = _FENCE.findall(text)
    if not blocks:
        return None
    # Use the longest block — usually the full solution rather than a snippet.
    return max(blocks, key=len).strip()


def verify(text: str) -> Verdict:
    code = extract_code(text)
    if code is None:
        return Verdict(False, False, "no fenced code block in response")
    try:
        compile(code, "<candidate>", "exec")
    except SyntaxError as e:
        return Verdict(False, True, f"syntax error: {e.msg} (line {e.lineno})")
    return Verdict(True, True, "code parses")
