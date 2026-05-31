from cascade.verifier import Verdict, dsl_from_cases, extract_code, verify


def test_extract_code_none_when_no_fence():
    assert extract_code("just prose, no code") is None


def test_extract_code_single_block():
    assert extract_code("```python\nx = 1\n```").strip() == "x = 1"


def test_extract_code_picks_longest_block():
    text = "```py\na=1\n```\nmid\n```python\nlong = 1\nlong2 = 2\n```"
    assert extract_code(text) == "long = 1\nlong2 = 2"


def test_verify_no_code():
    v = verify("no code here")
    assert v == Verdict(False, False, "no fenced code block in response")


def test_verify_passes_on_valid_code():
    v = verify("```python\ndef f():\n    return 1\n```")
    assert v.passed and v.has_code and v.reason == "code parses"


def test_verify_fails_on_syntax_error():
    v = verify("```python\ndef f(:\n```")
    assert v.passed is False
    assert v.has_code is True
    assert v.reason.startswith("syntax error:")


# dsl_from_cases -----------------------------------------------------------

def test_dsl_from_cases_basic():
    dsl = dsl_from_cases("add", [((1, 2), 3), ((0, 0), 0)])
    assert dsl == "assert add(1, 2) == 3\nassert add(0, 0) == 0"


def test_dsl_from_cases_single_arg_bare_value():
    dsl = dsl_from_cases("neg", [(5, -5), (0, 0)])
    assert dsl == "assert neg(5) == -5\nassert neg(0) == 0"


def test_dsl_from_cases_none_expected_uses_is_none():
    dsl = dsl_from_cases("find", [(("x",), None)])
    assert dsl == "assert find('x') is None"


def test_dsl_from_cases_single_case():
    assert dsl_from_cases("f", [((1,), 2)]) == "assert f(1) == 2"


def test_dsl_from_cases_string_expected():
    dsl = dsl_from_cases("greet", [(("Alice",), "Hello Alice")])
    assert dsl == "assert greet('Alice') == 'Hello Alice'"


def test_dsl_from_cases_empty_returns_empty_string():
    assert dsl_from_cases("f", []) == ""
