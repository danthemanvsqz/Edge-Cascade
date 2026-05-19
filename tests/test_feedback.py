from cascade.feedback import CheckFailure, build_repair_prompt


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
