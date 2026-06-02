"""Repair protocol — turn validation failures into a model-legible fix request.

LLMs repair reliably when the feedback is concrete and the output contract is
unambiguous: the original task, the exact code they produced, each failed
assertion with its observed behaviour, and a strict "return one code block"
instruction. This module just formats that; the loop lives in validate_log.py.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CheckFailure:
    expr: str               # the DSL assertion that failed
    observed: str           # what actually happened (value or exception)
    requirement: str = ""   # plain-language expectation (from the DSL `:: ...`)


_PROTOCOL = """\
You are repairing code that failed automated validation. Fix it.

# TASK
{task}

# YOUR PREVIOUS CODE
```{lang}
{code}
```

# FAILED CHECKS
{note}Each item is an assertion that MUST hold true. It failed as shown.
{failures}
{degen}
# OUTPUT CONTRACT
{contract}
Rules:
- Every FAILED CHECK must pass.
- Do not break behaviour that already worked.
- No prose, no explanation, no extra code blocks. The code block only.\
"""

_CONTRACT_BY_LANG: dict[str, str] = {
    "python": (
        "Return the complete corrected program as exactly ONE Python code block:\n"
        "```python\n# full corrected code here\n```"
    ),
    "typescript": (
        "Return the complete corrected program as exactly ONE TypeScript code block:\n"
        "```typescript\n// full corrected code here\n```"
    ),
    "javascript": (
        "Return the complete corrected program as exactly ONE JavaScript code block:\n"
        "```javascript\n// full corrected code here\n```"
    ),
    "git": (
        "Return the corrected git command as exactly ONE git code block:\n"
        "```git\ngit <verb> ...\n```"
    ),
    "bash": (
        "Return the corrected shell command as exactly ONE bash code block:\n"
        "```bash\n# corrected command here\n```"
    ),
}
_CONTRACT_DEFAULT = _CONTRACT_BY_LANG["python"]


def _language_from_failures(failures: list[CheckFailure]) -> str:
    """Infer the artifact language from the first failure's expr field.

    Gate failure exprs are '<lang>-syntax' (e.g. 'git-syntax', 'python-syntax').
    Falls back to 'python' for DSL/functional failures and the empty case.
    """
    if not failures:
        return "python"
    expr = failures[0].expr
    if "-" in expr:
        lang = expr.split("-")[0]
        return lang if lang in _CONTRACT_BY_LANG else "python"
    return "python"


def build_repair_prompt(
    task: str, code: str, failures: list[CheckFailure], note: str = "",
    degen_reasons: tuple[str, ...] = (),
) -> str:
    lang = _language_from_failures(failures)
    blocks = []
    for i, f in enumerate(failures, 1):
        lines = [f"{i}. requirement: {f.requirement}"] if f.requirement else []
        lines.append(("   " if lines else f"{i}. ") + f"assert: {f.expr}")
        lines.append(f"   observed: {f.observed}")
        blocks.append("\n".join(lines))
    # When the caller sliced the program to the implicated symbols, say so --
    # otherwise the model assumes the code shown is the whole program and may
    # delete the parts it can't see.
    note_line = (
        f"NOTE: showing {note} -- the code above is the implicated part of a "
        f"larger program; fix it without dropping the rest.\n"
        if note else ""
    )
    # PD-1 v2 warn-prompt: when the prior draft tripped the degeneration
    # detector, tell the repair model what failure mode to avoid. The {degen}
    # placeholder is empty when no reasons are passed -- the resulting prompt
    # is byte-identical to the pre-v2 behaviour, so existing callers and
    # golden replay logs see no diff.
    degen_block = ""
    if degen_reasons:
        # Trailing "\n" preserves the section-header convention: every other
        # block in _PROTOCOL is preceded by a blank line. Without it, the
        # warn-prompt path glues "Avoid..." straight to "# OUTPUT CONTRACT".
        degen_block = (
            "\n# PRIOR DRAFT QUALITY SIGNAL\n"
            "The prior draft tripped these detectors:\n"
            + "\n".join(f"- {r}" for r in degen_reasons)
            + "\nAvoid repeating tokens, identifiers, or sentences in the fix.\n"
        )
    return _PROTOCOL.format(
        task=task.strip(), code=code.strip(),
        lang=lang, contract=_CONTRACT_BY_LANG.get(lang, _CONTRACT_DEFAULT),
        note=note_line, failures="\n".join(blocks),
        degen=degen_block,
    )
