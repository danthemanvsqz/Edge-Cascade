"""Tests for cascade/gate.py — language-keyed verifier registry (VR-1).

Covers: detect_language dispatch, gate() all branches, gate_any() OR semantics,
pre-registration state, and the register() → gate() roundtrip.
"""
from __future__ import annotations

import pytest

from cascade.gate import (
    _LANG_MAP,
    _REGISTRY,
    detect_language,
    gate,
    gate_any,
    register,
)
from cascade.verifier import Verdict

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

PY_BLOCK = "```python\ndef f(x):\n    return x + 1\n```"
PY_BAD   = "```python\ndef f(:\n    pass\n```"
TS_BLOCK = "```typescript\nconst x: number = 1;\n```"
BARE     = "```\nx = 1\n```"
GIT_BLOCK = "```git\ngit status\n```"
BASH_BLOCK = "```bash\necho hello\n```"
JS_BLOCK = "```javascript\nconsole.log(1);\n```"
NO_FENCE = "just plain prose with no code block"


@pytest.fixture
def temp_lang():
    """Register a throw-away language and clean up after the test."""
    registered: list[str] = []
    mapped: list[str] = []

    def _register(tag: str, canon: str, fn):
        _LANG_MAP[tag] = canon
        register(canon, fn)
        registered.append(canon)
        mapped.append(tag)

    yield _register

    for c in registered:
        _REGISTRY.pop(c, None)
    for t in mapped:
        _LANG_MAP.pop(t, None)


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------

def test_detect_python_fence():
    assert detect_language(PY_BLOCK) == "python"


def test_detect_py_alias():
    assert detect_language("```py\nx=1\n```") == "python"


def test_detect_typescript_fence():
    assert detect_language(TS_BLOCK) == "typescript"


def test_detect_ts_alias():
    assert detect_language("```ts\nconst x = 1;\n```") == "typescript"


def test_detect_git_fence():
    assert detect_language(GIT_BLOCK) == "git"


def test_detect_bash_fence():
    assert detect_language(BASH_BLOCK) == "bash"


def test_detect_sh_alias():
    assert detect_language("```sh\necho hi\n```") == "bash"


def test_detect_shell_alias():
    assert detect_language("```shell\necho hi\n```") == "bash"


def test_detect_javascript_fence():
    assert detect_language(JS_BLOCK) == "javascript"


def test_detect_js_alias():
    assert detect_language("```js\nconsole.log(1);\n```") == "javascript"


def test_detect_bare_fence_defaults_python():
    assert detect_language(BARE) == "python"


def test_detect_no_fence_returns_ambiguous():
    assert detect_language(NO_FENCE) == "ambiguous"


def test_detect_unknown_tag():
    assert detect_language("```ruby\nputs 'hi'\n```") == "unknown-ruby"


def test_detect_case_insensitive_python():
    assert detect_language("```Python\nx=1\n```") == "python"


def test_detect_case_insensitive_typescript():
    assert detect_language("```TypeScript\nconst x=1;\n```") == "typescript"


# ---------------------------------------------------------------------------
# gate() — Python path
# ---------------------------------------------------------------------------

def test_gate_valid_python_passes():
    passed, failures = gate(PY_BLOCK, dsl=None)
    assert passed is True
    assert failures == []


def test_gate_invalid_python_fails():
    passed, failures = gate(PY_BAD, dsl=None)
    assert passed is False
    assert len(failures) == 1


def test_gate_failure_has_language_key():
    _, failures = gate(PY_BAD, dsl=None)
    assert failures[0]["language"] == "python"


def test_gate_failure_has_required_fields():
    _, failures = gate(PY_BAD, dsl=None)
    f = failures[0]
    assert "expr" in f
    assert "observed" in f
    assert "requirement" in f


def test_gate_bare_fence_uses_python_gate():
    passed, _ = gate(BARE, dsl=None)
    assert passed is True


# ---------------------------------------------------------------------------
# gate() — git/bash: in _LANG_MAP but not registered
# ---------------------------------------------------------------------------

def test_gate_git_not_registered_returns_no_verifier():
    assert "git" in _LANG_MAP.values()
    assert "git" not in _REGISTRY
    passed, failures = gate(GIT_BLOCK, dsl=None)
    assert passed is False
    assert failures[0]["expr"] == "no-verifier"
    assert failures[0]["language"] == "git"


def test_gate_bash_not_registered_returns_no_verifier():
    assert "bash" not in _REGISTRY
    passed, failures = gate(BASH_BLOCK, dsl=None)
    assert passed is False
    assert failures[0]["expr"] == "no-verifier"


# ---------------------------------------------------------------------------
# gate() — unknown tag
# ---------------------------------------------------------------------------

def test_gate_unknown_tag_fails():
    passed, failures = gate("```ruby\nputs 'hi'\n```", dsl=None)
    assert passed is False
    f = failures[0]
    assert f["expr"] == "unknown-language"
    assert f["language"] == "unknown-ruby"
    assert "register" in f["requirement"]


# ---------------------------------------------------------------------------
# gate() — ambiguous (no fence) delegates to gate_any
# ---------------------------------------------------------------------------

def test_gate_ambiguous_calls_gate_any(mocker):
    mock = mocker.patch("cascade.gate.gate_any", return_value=(True, []))
    passed, _ = gate(NO_FENCE, dsl=None)
    assert passed is True
    mock.assert_called_once_with(
        NO_FENCE, ["python", "typescript", "javascript"]
    )


# ---------------------------------------------------------------------------
# gate() — DSL path
# ---------------------------------------------------------------------------

def test_gate_dsl_calls_verify_functional(mocker):
    mock = mocker.patch(
        "cascade.tasks.verify_functional",
        return_value={"passed": True, "failures": []},
    )
    passed, failures = gate(PY_BLOCK, dsl="assert f(1) == 2")
    assert passed is True
    assert failures == []
    mock.assert_called_once()


def test_gate_dsl_fail_propagates_failures(mocker):
    mocker.patch(
        "cascade.tasks.verify_functional",
        return_value={
            "passed": False,
            "failures": [{"expr": "assert f(1) == 2", "observed": "3"}],
        },
    )
    passed, failures = gate(PY_BLOCK, dsl="assert f(1) == 2")
    assert passed is False
    assert len(failures) == 1


def test_gate_dsl_ignores_language_tag(mocker):
    # DSL branch is language-agnostic — routes to verify_functional regardless.
    mock = mocker.patch(
        "cascade.tasks.verify_functional",
        return_value={"passed": True, "failures": []},
    )
    passed, _ = gate(TS_BLOCK, dsl="assert f(1) == 2")
    assert passed is True
    mock.assert_called_once()


def test_gate_dsl_malformed_response_safe(mocker):
    # verify_functional returns a dict missing 'passed'/'failures' — must not raise.
    mocker.patch("cascade.tasks.verify_functional", return_value={})
    passed, failures = gate(PY_BLOCK, dsl="assert f(1) == 2")
    assert passed is False
    assert failures == []


# ---------------------------------------------------------------------------
# gate_any()
# ---------------------------------------------------------------------------

def test_gate_any_passes_when_any_verifier_passes(temp_lang):
    temp_lang("_tag_pass", "_lang_pass", lambda _: Verdict(True, True, "ok"))
    temp_lang("_tag_fail", "_lang_fail", lambda _: Verdict(False, True, "bad"))
    passed, failures = gate_any("anything", ["_lang_fail", "_lang_pass"])
    assert passed is True
    assert failures == []


def test_gate_any_fails_with_combined_list_when_all_fail(temp_lang):
    temp_lang("_tag_a", "_lang_a", lambda _: Verdict(False, True, "reason A"))
    temp_lang("_tag_b", "_lang_b", lambda _: Verdict(False, True, "reason B"))
    passed, failures = gate_any("anything", ["_lang_a", "_lang_b"])
    assert passed is False
    assert len(failures) == 2
    langs = {f["language"] for f in failures}
    assert langs == {"_lang_a", "_lang_b"}


def test_gate_any_failure_dicts_have_language_key(temp_lang):
    temp_lang("_tag_c", "_lang_c", lambda _: Verdict(False, True, "oops"))
    _, failures = gate_any("anything", ["_lang_c"])
    assert failures[0]["language"] == "_lang_c"


def test_gate_any_skips_unregistered_languages(temp_lang):
    temp_lang("_tag_ok", "_lang_ok", lambda _: Verdict(True, True, "ok"))
    passed, _ = gate_any("anything", ["_lang_ok", "nonexistent_lang_xyz"])
    assert passed is True


def test_gate_any_no_registered_languages_returns_failure():
    passed, failures = gate_any("anything", ["nonexistent_xyz", "nonexistent_abc"])
    assert passed is False
    assert failures[0]["expr"] == "no-registered-verifiers"


# ---------------------------------------------------------------------------
# Pre-registration state
# ---------------------------------------------------------------------------

def test_python_pre_registered():
    assert "python" in _REGISTRY


def test_typescript_pre_registered():
    assert "typescript" in _REGISTRY


def test_git_not_pre_registered():
    assert "git" not in _REGISTRY


def test_bash_not_pre_registered():
    assert "bash" not in _REGISTRY


def test_javascript_not_pre_registered():
    assert "javascript" not in _REGISTRY


# ---------------------------------------------------------------------------
# register() → gate() roundtrip
# ---------------------------------------------------------------------------

def test_register_roundtrip(temp_lang):
    temp_lang("testlang", "testlang", lambda _: Verdict(True, True, "ok"))
    passed, _ = gate("```testlang\nsome code\n```", dsl=None)
    assert passed is True


def test_register_fail_roundtrip(temp_lang):
    temp_lang("baddlang", "baddlang", lambda _: Verdict(False, True, "nope"))
    passed, failures = gate("```baddlang\nsome code\n```", dsl=None)
    assert passed is False
    assert failures[0]["language"] == "baddlang"
