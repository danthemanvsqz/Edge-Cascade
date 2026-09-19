"""reviewer reaches 100% with a stubbed Anthropic client (no network, no spend),
mirroring test_cloud_worker's injection pattern. Covers prompt assembly +
truncation, the cost estimate, and the success / missing-usage / APIError paths.
"""
import types

import anthropic
import httpx
import pytest

from cascade.reviewer import (
    _REVIEW_SYSTEM,
    ReviewResult,
    affordable_max_tokens,
    build_prompt,
    est_cost_usd,
    est_input_tokens,
    review,
)

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


# --- review focus (the tuned system prompt) --------------------------------

def test_review_focus_priority_order():
    # deficiencies first, then security, then agent maintainability
    s = _REVIEW_SYSTEM.lower()
    assert s.index("deficiencies") < s.index("security") < s.index("agent maintainability")


def test_review_focus_security_is_egregious_only_for_sandbox():
    s = _REVIEW_SYSTEM.lower()
    assert "egregious only" in s and "sandbox" in s


def test_review_focus_targets_agent_not_human_maintainability():
    s = _REVIEW_SYSTEM.lower()
    assert "agent maintainability" in s and "not human aesthetics" in s


def test_review_focus_keeps_injection_guard_and_verdict_line():
    assert "untrusted DATA" in _REVIEW_SYSTEM
    assert _REVIEW_SYSTEM.rstrip().endswith(
        "VERDICT: APPROVE | APPROVE WITH NITS | REQUEST CHANGES.")


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


def test_est_cost_prices_fable_at_its_own_rate():
    r = ReviewResult("ok", "claude-fable-5-1", 1.0, 1_000_000, 1_000_000)
    assert est_cost_usd(r) == pytest.approx(60.0)            # 10 + 50


FABLE = (10.0, 50.0)


@pytest.mark.parametrize("in_tok, cap, budget, want", [
    (0, 16000, 0.50, 10000),        # output alone: $0.50 / $50 per 1M
    (0, 4000, 0.50, 4000),          # never above the configured cap
    (30000, 16000, 0.50, 4000),     # $0.30 of input leaves $0.20 of output
    (60000, 16000, 0.50, 0),        # input alone uses the whole budget
    (10**9, 16000, 0.50, 0),        # input far over budget
    (0, 16000, 0.0, 0),             # nothing left today
])
def test_affordable_max_tokens_keeps_worst_case_in_budget(in_tok, cap, budget, want):
    got = affordable_max_tokens(in_tok, cap, budget, FABLE)
    assert got == want
    assert in_tok / 1e6 * FABLE[0] + got / 1e6 * FABLE[1] <= budget + 1e-9 or got == 0


def test_est_input_tokens_is_pessimistic_and_counts_the_system_prompt():
    sys_bytes = len(_REVIEW_SYSTEM.encode("utf-8"))
    assert est_input_tokens("") == sys_bytes * 2 // 5
    # 2.5 bytes/token: denser than real code even on the Fable tokenizer
    assert est_input_tokens("x" * 5000) == (sys_bytes + 5000) * 2 // 5
    assert est_input_tokens("x" * 5000) - est_input_tokens("") in (2000, 2001)


def test_review_truncated_at_max_tokens_is_unavailable_but_still_costed():
    msg = types.SimpleNamespace(
        content=[_Blk("thinking"), _Blk("text", "half a revi")],
        stop_reason="max_tokens",
        usage=types.SimpleNamespace(input_tokens=50, output_tokens=2000))
    r = review(_Client(msg=msg), "claude-fable-5-1", 2000, "p")
    assert r.available is False
    assert r.text == "[review truncated at max_tokens=2000]"
    assert r.output_tokens == 2000


@pytest.mark.parametrize("details, label", [
    (types.SimpleNamespace(category="cyber"), "cyber"),
    (None, "unspecified"),
])
def test_review_refusal_is_unavailable_but_still_costed(details, label):
    msg = types.SimpleNamespace(
        content=[_Blk("text", "partial")], stop_reason="refusal",
        stop_details=details,
        usage=types.SimpleNamespace(input_tokens=10, output_tokens=3))
    r = review(_Client(msg=msg), "claude-fable-5-1", 16000, "p")
    assert r.available is False and r.text == f"[review refused: {label}]"
    assert r.input_tokens == 10 and r.output_tokens == 3


def test_review_handles_api_error():
    err = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    r = review(_Client(exc=err), "m", 256, "p")
    assert r.available is False and r.text.startswith("[review error:")
