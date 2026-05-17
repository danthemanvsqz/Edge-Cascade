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
```python
{code}
```

# FAILED CHECKS
Each item is an assertion that MUST hold true. It failed as shown.
{failures}

# OUTPUT CONTRACT
Return the complete corrected program as exactly ONE Python code block:
```python
# full corrected code here
```
Rules:
- Every FAILED CHECK must pass.
- Do not break behaviour that already worked.
- No prose, no explanation, no extra code blocks. The code block only.\
"""


def build_repair_prompt(
    task: str, code: str, failures: list[CheckFailure]
) -> str:
    blocks = []
    for i, f in enumerate(failures, 1):
        lines = [f"{i}. requirement: {f.requirement}"] if f.requirement else []
        lines.append(("   " if lines else f"{i}. ") + f"assert: {f.expr}")
        lines.append(f"   observed: {f.observed}")
        blocks.append("\n".join(lines))
    return _PROTOCOL.format(
        task=task.strip(), code=code.strip(), failures="\n".join(blocks)
    )
