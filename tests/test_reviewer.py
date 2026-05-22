"""reviewer reaches 100% with a stubbed Anthropic client (no network, no spend),
mirroring test_cloud_worker's injection pattern. Covers prompt assembly +
truncation, the cost estimate, and the success / missing-usage / APIError paths.
"""
import types

import anthropic
import httpx
import pytest

from cascade.reviewer import ReviewResult, build_prompt, est_cost_usd, review

# --- fake Anthropic client (same shape as test_cloud_worker) ---------------

class _Blk:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Stream:
    def __init__(self, msg):
        self._m = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._m


class _Messages:
    def __init__(self, msg=None, exc=None):
        self._msg, self._exc = msg, exc

    def stream(self, **kw):
        if self._exc:
            raise self._exc
        return _Stream(self._msg)


class _Client:
    def __init__(self, msg=None, exc=None):
        self.messages = _Messages(msg, exc)


# --- prompt + cost ---------------------------------------------------------

def test_build_prompt_without_title():
    p = build_prompt("a-diff")
    assert "Review this unified diff" in p and "```diff\na-diff\n```" in p
    assert not p.startswith("# PR:")


def test_build_prompt_with_title_and_body():
    p = build_prompt("d", title="My PR", body="  does X  ")
    assert p.startswith("# PR: My PR") and "does X" in p


def test_build_prompt_truncates_giant_diff():
    p = build_prompt("x" * 5000, max_diff_bytes=100)
    assert "truncated to the first 100 bytes" in p
    assert p.count("x") == 100


def test_est_cost_uses_shared_rates():
    r = ReviewResult("ok", "claude-sonnet-4-6", 1.0, 1_000_000, 1_000_000)
    assert est_cost_usd(r) == pytest.approx(18.0)            # 3 + 15
    unknown = ReviewResult("ok", "mystery", 1_000_000, 0, 1_000_000)
    # unknown model billed at the dearest known rate (never under-counted)
    assert est_cost_usd(unknown) == pytest.approx(75.0)      # 0*15 + 1*75


# --- review() with the injected stub ---------------------------------------

def test_review_success_counts_usage():
    msg = types.SimpleNamespace(
        content=[_Blk("thinking"), _Blk("text", "looks good")],
        usage=types.SimpleNamespace(
            input_tokens=20, cache_read_input_tokens=4,
            cache_creation_input_tokens=1, output_tokens=8),
    )
    r = review(_Client(msg=msg), "claude-sonnet-4-6", 1024, "prompt")
    assert r.available and r.text == "looks good"
    assert r.input_tokens == 25 and r.output_tokens == 8


def test_review_handles_missing_usage():
    r = review(
        _Client(msg=types.SimpleNamespace(content=[_Blk("text", "z")], usage=None)),
        "m", 256, "p")
    assert r.text == "z" and r.input_tokens == 0 and r.output_tokens == 0


def test_review_handles_api_error():
    err = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    r = review(_Client(exc=err), "m", 256, "p")
    assert r.available is False and r.text.startswith("[review error:")
