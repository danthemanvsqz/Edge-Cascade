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
