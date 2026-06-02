from cascade.feedback import CheckFailure, _language_from_failures, build_repair_prompt


def test_checkfailure_default_requirement():
    f = CheckFailure(expr="x == 1", observed="got 2")
    assert f.requirement == ""


def test_build_prompt_covers_both_requirement_branches():
    failures = [
        CheckFailure("drone_ok(d)", "KeyError: 'E'",
                     requirement="must return 11"),   # with requirement
        CheckFailure("add(1,2) == 3", "evaluated False"),  # without
    ]
    out = build_repair_prompt("  TASK TEXT  ", "  CODE  ", failures)

    assert "# TASK\nTASK TEXT" in out          # task stripped + placed
    assert "```python\nCODE\n```" in out       # code stripped + fenced
    assert "1. requirement: must return 11" in out
    assert "   assert: drone_ok(d)" in out     # indented (had requirement)
    assert "2. assert: add(1,2) == 3" in out   # numbered (no requirement)
    assert "observed: KeyError: 'E'" in out
    assert "OUTPUT CONTRACT" in out
    assert "NOTE:" not in out                  # no note by default


def test_build_prompt_note_renders_when_given():
    f = [CheckFailure("x == 1", "got 2")]
    out = build_repair_prompt("t", "c", f, note="only the 1 symbol (foo)")
    assert "NOTE: showing only the 1 symbol (foo)" in out
    assert "fix it without dropping the rest" in out
    # the note sits in the FAILED CHECKS section, before the assertion list
    assert out.index("NOTE:") < out.index("1. assert: x == 1")


def test_build_prompt_degen_reasons_render_when_given():
    """PD-1 v2 warn-prompt: when the prior draft tripped the degen detector,
    the reasons are rendered between FAILED CHECKS and OUTPUT CONTRACT."""
    f = [CheckFailure("x == 1", "got 2")]
    out = build_repair_prompt(
        "t", "c", f,
        degen_reasons=("looping: trigram_repeat=0.20 > 0.14",
                       "narrowing: ttr=0.30 < 0.32"),
    )
    assert "# PRIOR DRAFT QUALITY SIGNAL" in out
    assert "- looping: trigram_repeat=0.20 > 0.14" in out
    assert "- narrowing: ttr=0.30 < 0.32" in out
    assert "Avoid repeating tokens, identifiers, or sentences" in out
    # Block sits AFTER failed checks, BEFORE output contract.
    assert out.index("# FAILED CHECKS") < out.index("# PRIOR DRAFT QUALITY SIGNAL")
    assert out.index("# PRIOR DRAFT QUALITY SIGNAL") < out.index("# OUTPUT CONTRACT")


def test_build_prompt_no_degen_block_when_empty():
    """When degen_reasons is empty, the prompt is byte-identical to the
    pre-v2 rendering. Snapshot-pinned -- if _PROTOCOL drifts in a way that
    breaks the contract for callers / golden replay logs, this fails loudly
    instead of silently passing a self-comparison."""
    f = [CheckFailure("x == 1", "got 2")]
    # Pre-v2 snapshot (build_repair_prompt('t', 'c', [CheckFailure('x == 1',
    # 'got 2')]) on main @ 67c6fac, before PD-1 v2 warn-prompt landed).
    expected = (
        "You are repairing code that failed automated validation. Fix it.\n"
        "\n"
        "# TASK\nt\n"
        "\n"
        "# YOUR PREVIOUS CODE\n```python\nc\n```\n"
        "\n"
        "# FAILED CHECKS\n"
        "Each item is an assertion that MUST hold true. It failed as shown.\n"
        "1. assert: x == 1\n"
        "   observed: got 2\n"
        "\n"
        "# OUTPUT CONTRACT\n"
        "Return the complete corrected program as exactly ONE Python code block:\n"
        "```python\n# full corrected code here\n```\n"
        "Rules:\n"
        "- Every FAILED CHECK must pass.\n"
        "- Do not break behaviour that already worked.\n"
        "- No prose, no explanation, no extra code blocks. The code block only."
    )
    assert build_repair_prompt("t", "c", f, degen_reasons=()) == expected
    assert build_repair_prompt("t", "c", f) == expected     # default kwarg
    assert "PRIOR DRAFT QUALITY SIGNAL" not in expected


# ---------------------------------------------------------------------------
# VR-5: language-keyed OUTPUT CONTRACT
# ---------------------------------------------------------------------------

def test_language_from_failures_empty_list():
    assert _language_from_failures([]) == "python"


def test_language_from_failures_python_syntax():
    assert _language_from_failures([CheckFailure("python-syntax", "err")]) == "python"


def test_language_from_failures_git_syntax():
    assert _language_from_failures([CheckFailure("git-syntax", "err")]) == "git"


def test_language_from_failures_no_hyphen_falls_back():
    assert _language_from_failures([CheckFailure("x == 1", "got 2")]) == "python"


def test_build_prompt_git_failures_uses_git_contract():
    f = [CheckFailure("git-syntax", "expected 'git <verb>', got: 'echo hi'",
                      requirement="fenced git block that parses")]
    out = build_repair_prompt("stage all files", "echo hi", f)
    assert "```git\ngit <verb>" in out
    assert "```git\necho hi\n```" in out       # code block uses git fence


def test_build_prompt_bash_failures_uses_bash_contract():
    f = [CheckFailure("bash-syntax", "syntax error: ...",
                      requirement="fenced bash block that parses")]
    out = build_repair_prompt("run a script", "if [ ; then", f)
    assert "```bash\n# corrected command" in out


def test_build_prompt_typescript_failures_uses_ts_contract():
    f = [CheckFailure("typescript-syntax", "': expected'",
                      requirement="fenced TypeScript block that parses")]
    out = build_repair_prompt("add a function", "const x:", f)
    assert "```typescript\n// full corrected" in out


def test_build_prompt_degen_reasons_preserve_blank_line_before_output_contract():
    """When degen_reasons is non-empty, the rendered block must end with a
    newline so the section-header convention (every header preceded by a
    blank line) is preserved into OUTPUT CONTRACT. Catches the
    'glued sections' regression flagged in the review."""
    f = [CheckFailure("x == 1", "got 2")]
    out = build_repair_prompt(
        "t", "c", f, degen_reasons=("looping: trigram_repeat=0.20 > 0.14",),
    )
    # exactly two newlines (one blank line) between "in the fix." and "# OUTPUT CONTRACT"
    assert "in the fix.\n\n# OUTPUT CONTRACT" in out
    assert "in the fix.\n# OUTPUT CONTRACT" not in out.replace(
        "in the fix.\n\n# OUTPUT CONTRACT", ""
    )
