"""Unit tests for cascade.ts_verifier (BACKLOG #7 -- the TS syntax gate).

The Node subprocess is mocked: this covers the dispatch + result mapping (the
logic the 100% gate guards). The real Node transpile path is live-validated by
routing a TS task through the pipeline (see the slice's demo), not unit-cov'd.
"""
from __future__ import annotations

from types import SimpleNamespace

from cascade import ts_verifier
from cascade.ts_verifier import extract_ts, is_typescript, verify_ts

TS = "```typescript\nexport const x: number = 1;\n```"
PY = "```python\nx = 1\n```"


def test_extract_ts_returns_longest_block():
    text = "```ts\nshort\n```\n```typescript\nthe longer snippet\n```"
    assert extract_ts(text) == "the longer snippet"


def test_extract_ts_none_without_a_ts_block():
    assert extract_ts(PY) is None
    assert extract_ts("no fences at all") is None


def test_is_typescript_distinguishes_ts_from_python():
    assert is_typescript(TS) is True
    assert is_typescript(PY) is False


def test_verify_ts_fails_when_no_block():
    v = verify_ts("prose with no fenced code")
    assert v.passed is False
    assert v.has_code is False


def _fake_run(stdout):
    return lambda *a, **k: SimpleNamespace(stdout=stdout, returncode=0)


def test_verify_ts_passes_on_clean_transpile(monkeypatch):
    monkeypatch.setattr(ts_verifier.subprocess, "run", _fake_run('{"passed": true}'))
    assert verify_ts(TS).passed is True


def test_verify_ts_fails_with_reason_on_syntax_error(monkeypatch):
    monkeypatch.setattr(
        ts_verifier.subprocess, "run", _fake_run('{"passed": false, "reason": "\':\' expected."}')
    )
    v = verify_ts(TS)
    assert v.passed is False
    assert "expected" in v.reason


def test_verify_ts_fails_soft_when_checker_unavailable(monkeypatch):
    def boom(*a, **k):
        raise OSError("node not found")

    monkeypatch.setattr(ts_verifier.subprocess, "run", boom)
    v = verify_ts(TS)
    assert v.passed is False
    assert "unavailable" in v.reason
