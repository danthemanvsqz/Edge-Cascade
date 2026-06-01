"""Tests for cascade/js_verifier.py (VR-3 — JavaScript gate)."""
from __future__ import annotations

from types import SimpleNamespace

from cascade import js_verifier
from cascade.gate import _REGISTRY
from cascade.js_verifier import extract_js, verify_js

JS_BLOCK   = "```javascript\nconsole.log(1);\n```"
ALT_BLOCK  = "```js\nconst x = 1;\n```"
MULTI_JS   = "```js\nshort\n```\n```javascript\nconst a = 1;\nconst b = 2;\n```"
PY_BLOCK   = "```python\nx = 1\n```"
NO_FENCE   = "just plain prose"


# ---------------------------------------------------------------------------
# extract_js
# ---------------------------------------------------------------------------

def test_extract_js_javascript_fence():
    assert extract_js(JS_BLOCK) == "console.log(1);"


def test_extract_js_js_alias():
    assert extract_js(ALT_BLOCK) == "const x = 1;"


def test_extract_js_returns_longest_block():
    result = extract_js(MULTI_JS)
    assert "const a = 1;" in result
    assert "const b = 2;" in result


def test_extract_js_none_without_js_fence():
    assert extract_js(NO_FENCE) is None
    assert extract_js(PY_BLOCK) is None


# ---------------------------------------------------------------------------
# verify_js
# ---------------------------------------------------------------------------

def _fake_run(returncode: int, stderr: str = ""):
    return lambda *a, **k: SimpleNamespace(returncode=returncode, stderr=stderr)


def test_verify_js_no_fence():
    v = verify_js(NO_FENCE)
    assert v.passed is False
    assert v.has_code is False
    assert v.reason == "no fenced javascript block in response"


def test_verify_js_passes_on_clean_syntax(mocker):
    mocker.patch.object(js_verifier.subprocess, "run", _fake_run(0))
    v = verify_js(JS_BLOCK)
    assert v.passed is True
    assert v.reason == "javascript syntax valid"


def test_verify_js_fails_on_syntax_error(mocker):
    mocker.patch.object(
        js_verifier.subprocess, "run", _fake_run(1, "SyntaxError: Unexpected token")
    )
    v = verify_js(JS_BLOCK)
    assert v.passed is False
    assert v.has_code is True
    assert "SyntaxError" in v.reason


def test_verify_js_fails_soft_when_node_unavailable(mocker):
    def boom(*a, **k):
        raise OSError("node not found")

    mocker.patch.object(js_verifier.subprocess, "run", boom)
    v = verify_js(JS_BLOCK)
    assert v.passed is False
    assert "unavailable" in v.reason


# ---------------------------------------------------------------------------
# Registration check (VR-3 registers javascript)
# ---------------------------------------------------------------------------

def test_javascript_registered_after_import():
    assert "javascript" in _REGISTRY
