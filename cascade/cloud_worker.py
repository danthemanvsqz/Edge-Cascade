"""Tier 3 — cloud backstop via the Anthropic API (Claude Sonnet 4.6).

Invoked only for queries the local NPU/GPU tiers couldn't handle. The API key
is read from ANTHROPIC_API_KEY (env or .env); if it's absent the tier no-ops
so the NPU->GPU cascade still works.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .config import CONFIG

# Frozen system prompt — kept stable (no timestamps/IDs) so it forms a cacheable
# prefix. Caching only actually engages once this exceeds Sonnet 4.6's minimum
# cacheable prefix (~2048 tokens); below that it's a silent no-op, which is fine.
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


# claude-sonnet-4-6 list price; ignore cache discounts so the estimate is
# conservative (slightly high) -- the safe bias for a credit guard.
_USD_PER_1M_IN = 3.0
_USD_PER_1M_OUT = 15.0


@dataclass
class CloudResult:
    text: str
    latency_s: float
    model: str
    available: bool = True
    input_tokens: int = 0
    output_tokens: int = 0

    def reason_note(self) -> str:
        if self.available:
            return "ok"
        return self.text  # carries the disabled/error message

    def est_cost_usd(self) -> float:
        return (self.input_tokens / 1e6 * _USD_PER_1M_IN
                + self.output_tokens / 1e6 * _USD_PER_1M_OUT)


def _compose_user(query: str, prior_attempt: str | None) -> str:
    """Build the user turn; on a retry, include the failed lower-tier answer."""
    if not prior_attempt:
        return query
    return (
        f"{query}\n\n--- A lower tier produced the following answer, "
        f"which failed verification. Diagnose and correct it: ---\n"
        f"{prior_attempt}"
    )


class CloudWorker:
    def __init__(self, enabled: bool = False) -> None:
        # PAID tier: requires the explicit opt-in AND a key. Neither alone is
        # enough — a key sitting in .env must not silently incur cost.
        self._has_key = bool(CONFIG.anthropic_api_key)
        self._enabled = enabled and self._has_key
        self._model = CONFIG.cloud_model
        self._client = None
        if self._enabled:
            import anthropic

            self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    def status(self) -> str:
        if self._enabled:
            return f"enabled ({self._model})"
        if self._has_key:
            return "disabled (key present; pass --cloud / enable_cloud=True)"
        return "disabled (no ANTHROPIC_API_KEY)"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def generate(self, query: str, prior_attempt: str | None = None) -> CloudResult:
        if not self._enabled:
            return CloudResult(
                "[paid cloud tier disabled]", 0.0, self._model, False
            )

        import anthropic

        user_content = _compose_user(query, prior_attempt)

        t0 = time.perf_counter()
        try:
            with self._client.messages.stream(
                model=self._model,
                max_tokens=CONFIG.cloud_max_tokens,
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
                f"[cloud error: {e}]", time.perf_counter() - t0, self._model, False
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
        return CloudResult(text, dt, self._model, True, in_tok, out_tok)
