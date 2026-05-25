"""Regression: a candidate that prints to stdout must not corrupt the gate result.

The funcverify child exec()s the untrusted candidate and writes its result as
JSON to stdout. If the candidate itself prints, that pollutes the stream and the
parent's json.loads crashes (observed 2026-05-25 on a topological_sort candidate).
The child now redirects the candidate's stdout so only the JSON result is emitted.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run_child(text: str, dsl: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "mcp_servers._funcverify_child"],
        input=json.dumps({"text": text, "dsl": dsl}),
        capture_output=True, text=True, cwd=str(ROOT), timeout=20)


def test_candidate_stdout_does_not_corrupt_result():
    text = ("```python\n"
            "print('debug noise from the candidate')\n"
            "def f(x):\n    return x + 1\n```")
    dsl = "when f\n  assert f(1) == 2\n"
    p = _run_child(text, dsl)
    assert p.returncode == 0
    out = json.loads(p.stdout)          # would raise pre-fix (stdout polluted)
    assert out["applicable"] is True
    assert out["passed"] is True


def test_clean_candidate_still_works():
    text = "```python\ndef f(x):\n    return x + 1\n```"
    p = _run_child(text, "when f\n  assert f(1) == 2\n")
    assert json.loads(p.stdout)["passed"] is True
