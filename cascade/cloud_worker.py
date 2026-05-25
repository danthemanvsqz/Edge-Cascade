"""Tier 3 — cloud backstop via the Anthropic API (default Claude Opus 4.7).

Invoked only for queries the local NPU/GPU tiers couldn't handle. The API key
is read from ANTHROPIC_API_KEY (env or .env); if it's absent the tier no-ops
so the NPU->GPU cascade still works.

No worker *object*: the tier is set-once config with no mutable state, so it
is a closure. `make_cloud_worker()` returns an immutable `CloudWorker` value
object carrying the resolved config plus a bound `generate` closure. The
network call lives in `_generate`, which takes the client as a parameter --
pure w.r.t. the network, so tests inject a stub instead of monkeypatching.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from .config import CONFIG

# Frozen system prompt — kept stable (no timestamps/IDs) so it forms a cacheable
# prefix. Caching only actually engages once this exceeds the model's minimum
# cacheable prefix; below that it's a silent no-op, which is fine.
_SYSTEM = """You are an expert software engineer acting as the final escalation \
tier in a local-first code-assistant cascade. A small edge model and a 14B local \
model already tried and either failed verification or flagged the task as too hard. \
Deliver a correct, complete, production-quality answer.

Guidelines:
- Prioritise correctness and robustness over brevity.
- When the task is code, return runnable code in a single fenced block.
- If a prior attempt is supplied, diagnose why it was insufficient and fix the \
root cause — don't just restate it.
- State key assumptions explicitly; do not ask clarifying questions — make a \
reasonable decision and note it."""


# Per-model list price, USD per 1M tokens (input, output). Cache discounts are
# ignored so the estimate is conservative (slightly high) -- the safe bias for
# a credit guard. Keyed by model-id PREFIX so version bumps (…-4-6 -> …-4-7)
# don't silently fall back. ORDER MATTERS: longest/most-specific prefixes first.
#
# This replaces the old hardcoded Sonnet constants. The latent bug it fixes:
# pointing the cloud tier at Opus while the cost math stayed on Sonnet rates
# made the guard under-count spend ~5x and silently blow cloud_usd_budget.
# Model id, price, and budget now move together; an UNKNOWN model is costed at
# the most expensive known rate (see _price_for) so a new model can never be
# under-counted.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus":   (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku":  (1.0, 5.0),
    "claude-3-opus":   (15.0, 75.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-5-haiku":  (1.0, 5.0),
}
# Conservative fallback for an unrecognised model: the dearest known rate.
_MAX_PRICE: tuple[float, float] = max(_PRICES.values())


def _price_for(model: str) -> tuple[float, float]:
    """(in, out) USD/1M for `model` by id-prefix; dearest known rate if unknown.

    Unknown -> dearest is deliberate: a credit guard must never under-count,
    so an unrecognised/new model is billed pessimistically rather than cheaply.
    """
    for prefix, price in _PRICES.items():
        if model.startswith(prefix):
            return price
    return _MAX_PRICE


@dataclass
class CloudResult:
    text: str
    latency_s: float
    model: str
    available: bool = True
    input_tokens: int = 0
    output_tokens: int = 0


def reason_note(result: CloudResult) -> str:
    """'ok' on success; otherwise the disabled/error message it carries.
    Was CloudResult.reason_note() -- behavior on data moved to a function."""
    if result.available:
        return "ok"
    return result.text


def est_cost_usd(result: CloudResult) -> float:
    """Conservative USD estimate for one result. Was CloudResult.est_cost_usd()."""
    in_rate, out_rate = _price_for(result.model)
    return (result.input_tokens / 1e6 * in_rate
            + result.output_tokens / 1e6 * out_rate)


def _compose_user(query: str, prior_attempt: str | None) -> str:
    """Build the user turn; on a retry, include the failed lower-tier answer."""
    if not prior_attempt:
        return query
    return (
        f"{query}\n\n--- A lower tier produced the following answer, "
        f"which failed verification. Diagnose and correct it: ---\n"
        f"{prior_attempt}"
    )


def cloud_status(*, enabled: bool, has_key: bool, model: str) -> str:
    """Human-readable tier state. Pure -- derived only from its args."""
    if enabled:
        return f"enabled ({model})"
    if has_key:
        return "disabled (key present; pass --cloud / enable_cloud=True)"
    return "disabled (no ANTHROPIC_API_KEY)"


def _generate(
    client, model: str, max_tokens: int,
    query: str, prior_attempt: str | None = None,
) -> CloudResult:
    """The actual API call + parse. `client` is injected (a real
    anthropic.Anthropic in production, a stub in tests), so this is pure with
    respect to the network and needs no monkeypatching to test."""
    import anthropic

    user_content = _compose_user(query, prior_attempt)

    t0 = time.perf_counter()
    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.APIError as e:
        return CloudResult(
            f"[cloud error: {e}]", time.perf_counter() - t0, model, False
        )

    dt = time.perf_counter() - t0
    text = "".join(
        b.text for b in msg.content if b.type == "text"
    ).strip()
    u = getattr(msg, "usage", None)
    in_tok = (getattr(u, "input_tokens", 0)
              + getattr(u, "cache_read_input_tokens", 0)
              + getattr(u, "cache_creation_input_tokens", 0)) if u else 0
    out_tok = getattr(u, "output_tokens", 0) if u else 0
    return CloudResult(text, dt, model, True, in_tok, out_tok)


@dataclass(frozen=True)
class CloudWorker:
    """Immutable Tier-3 handle: resolved config + a bound `generate` closure.
    Pure data -- `status` is a precomputed string, `generate` a closure."""

    enabled: bool
    model: str
    status: str
    generate: Callable[..., CloudResult]


def make_cloud_worker(enabled: bool = False) -> CloudWorker:
    """Resolve the paid tier. PAID: requires the explicit opt-in AND a key --
    neither alone is enough (a key sitting in .env must not silently spend)."""
    has_key = bool(CONFIG.anthropic_api_key)
    is_on = enabled and has_key
    model = CONFIG.cloud_model
    status = cloud_status(enabled=is_on, has_key=has_key, model=model)

    if not is_on:
        def generate(
            query: str, prior_attempt: str | None = None
        ) -> CloudResult:
            return CloudResult(
                "[paid cloud tier disabled]", 0.0, model, False
            )

        return CloudWorker(False, model, status, generate)

    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    generate = partial(_generate, client, model, CONFIG.cloud_max_tokens)
    return CloudWorker(True, model, status, generate)
