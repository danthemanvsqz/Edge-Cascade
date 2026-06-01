"""Tests for cascade/shell_verifier.py (VR-2 — git + bash gates)."""
from __future__ import annotations

from types import SimpleNamespace

from cascade import shell_verifier
from cascade.gate import _REGISTRY
from cascade.shell_verifier import (
    extract_git,
    extract_shell,
    verify_git,
    verify_shell,
)

GIT_BLOCK   = "```git\ngit status\n```"
MULTI_GIT   = "```git\nshort\n```\n```git\ngit commit -m 'fix'\ngit push\n```"
BASH_BLOCK  = "```bash\necho hello\n```"
SH_BLOCK    = "```sh\necho hi\n```"
SHELL_BLOCK = "```shell\nls -la\n```"
MULTI_BASH  = "```bash\nx=1\n```\n```shell\nexport A=1\nexport B=2\n```"
PY_BLOCK    = "```python\nx = 1\n```"
NO_FENCE    = "just plain prose"


# ---------------------------------------------------------------------------
# extract_git
# ---------------------------------------------------------------------------

def test_extract_git_returns_block():
    assert extract_git(GIT_BLOCK) == "git status"


def test_extract_git_returns_longest_block():
    result = extract_git(MULTI_GIT)
    assert "git commit" in result
    assert "git push" in result


def test_extract_git_none_without_git_fence():
    assert extract_git(NO_FENCE) is None
    assert extract_git(PY_BLOCK) is None
    assert extract_git(BASH_BLOCK) is None


# ---------------------------------------------------------------------------
# extract_shell
# ---------------------------------------------------------------------------

def test_extract_shell_bash_block():
    assert extract_shell(BASH_BLOCK) == "echo hello"


def test_extract_shell_sh_alias():
    assert extract_shell(SH_BLOCK) == "echo hi"


def test_extract_shell_shell_alias():
    assert extract_shell(SHELL_BLOCK) == "ls -la"


def test_extract_shell_returns_longest_block():
    result = extract_shell(MULTI_BASH)
    assert "export A=1" in result
    assert "export B=2" in result


def test_extract_shell_none_without_bash_fence():
    assert extract_shell(NO_FENCE) is None
    assert extract_shell(PY_BLOCK) is None
    assert extract_shell(GIT_BLOCK) is None


# ---------------------------------------------------------------------------
# verify_git
# ---------------------------------------------------------------------------

def test_verify_git_no_fence():
    v = verify_git(NO_FENCE)
    assert v.passed is False
    assert v.has_code is False
    assert v.reason == "no fenced git block in response"


def test_verify_git_valid_status():
    v = verify_git(GIT_BLOCK)
    assert v.passed is True
    assert v.has_code is True
    assert v.reason == "git command valid"


def test_verify_git_valid_commit():
    v = verify_git("```git\ngit commit -m 'refactor gate'\n```")
    assert v.passed is True


def test_verify_git_valid_multiline():
    v = verify_git(MULTI_GIT)
    assert v.passed is True


def test_verify_git_invalid_command():
    v = verify_git("```git\necho not-a-git-cmd\n```")
    assert v.passed is False
    assert v.has_code is True
    assert "expected" in v.reason


def test_verify_git_empty_block():
    v = verify_git("```git\n\n```")
    assert v.passed is False
    assert v.has_code is True


# ---------------------------------------------------------------------------
# verify_shell
# ---------------------------------------------------------------------------

def _fake_run(returncode: int, stderr: str = ""):
    return lambda *a, **k: SimpleNamespace(returncode=returncode, stderr=stderr)


def test_verify_shell_no_fence():
    v = verify_shell(NO_FENCE)
    assert v.passed is False
    assert v.has_code is False
    assert v.reason == "no fenced bash block in response"


def test_verify_shell_passes_on_clean_syntax(mocker):
    mocker.patch.object(shell_verifier.subprocess, "run", _fake_run(0))
    v = verify_shell(BASH_BLOCK)
    assert v.passed is True
    assert v.reason == "shell syntax valid"


def test_verify_shell_fails_on_syntax_error(mocker):
    mocker.patch.object(
        shell_verifier.subprocess, "run", _fake_run(1, "bash: line 1: syntax error")
    )
    v = verify_shell(BASH_BLOCK)
    assert v.passed is False
    assert v.has_code is True
    assert "syntax error" in v.reason


def test_verify_shell_fails_soft_when_bash_unavailable(mocker):
    def boom(*a, **k):
        raise OSError("bash not found")

    mocker.patch.object(shell_verifier.subprocess, "run", boom)
    v = verify_shell(BASH_BLOCK)
    assert v.passed is False
    assert "unavailable" in v.reason


# ---------------------------------------------------------------------------
# Registration check (VR-2 registers git + bash)
# ---------------------------------------------------------------------------

def test_git_registered_after_import():
    assert "git" in _REGISTRY


def test_bash_registered_after_import():
    assert "bash" in _REGISTRY
