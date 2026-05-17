from cascade.verifier import Verdict, extract_code, verify


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
