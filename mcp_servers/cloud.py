"""DEPRECATED -- edge-cloud MCP server (retired; inference now via Canvas pipeline).
Removed from .mcp.json 2026-05-31. See mcp_servers/gpu.py for details.

edge-cloud MCP server -- Tier 4, the paid Opus backstop (credit-guarded).

The only server that crosses the machine boundary and the only one that costs
money. Used when the agent is absent/throttled, or to break a deadlock with a
clean context window. Wraps cascade.cloud_worker (network/spend path is its
tested, stubbable code) and adds the credit-guard ACCOUNTING the architecture
assigns to this tier -- config carries the limits; nothing enforced them until
here. State is per-process: a server lifetime == a "pipeline run".

Tools:
  budget    credit-guard state; consult BEFORE escalate
  escalate  paid escalation. mode=repair (default) | critic (clean-context)

Run:  python -m mcp_servers.cloud        (stdio; reads ANTHROPIC_API_KEY)
"""
from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ._rec import make_recorder, recorded

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade.cloud_worker import est_cost_usd, make_cloud_worker  # noqa: E402
from cascade.config import CONFIG  # noqa: E402
from cascade.credit_guard import CreditGuard  # noqa: E402

mcp = FastMCP("edge-cloud")
_REC = make_recorder("edge-cloud")

# Paid tier ON here by design: this server *is* Tier 4. The credit guard +
# explicit deadlock-only invocation by the skill are the safety, not an
# off-by-default flag. Without a key CloudWorker still safely no-ops.
_worker = make_cloud_worker(enabled=True)

# Per-process credit guard (== one pipeline run). The single trusted gate,
# shared with the PR reviewer (cascade.credit_guard) so both enforce identically.
_guard = CreditGuard(
    max_calls=CONFIG.cloud_max_calls,
    usd_budget=CONFIG.cloud_usd_budget,
    enabled=_worker.enabled,
)

# A fresh-context preamble for critic mode: drop the deadlocked reasoning, do
# not anchor on prior attempts -- this is the consensus-inertia breaker.
_CRITIC_PREAMBLE = (
    "You are a fresh, independent reviewer. Earlier attempts on this task "
    "deadlocked (the same error kept recurring). Ignore how they framed it; "
    "re-derive the solution from first principles and state the root cause "
    "the earlier attempts missed.\n\n--- TASK ---\n"
)


def _budget_state() -> dict:
    s = _guard.state()
    return {
        "calls_used": s["calls_used"],
        "calls_max": s["calls_max"],
        "usd_spent": s["usd_spent"],
        "usd_budget": s["usd_budget"],
        "guard_tripped": s["guard_tripped"],
        "worker_enabled": _worker.enabled,
        "status": _worker.status,
        # allowed == a paid call would actually be attempted right now.
        "allowed": s["allowed"],
    }


@mcp.tool()
@recorded(_REC)
def budget() -> dict:
    """Credit-guard state for this run. Consult before every escalate().

    `allowed` is true only if the worker is enabled AND neither the call cap
    (cloud_max_calls) nor the USD ceiling (cloud_usd_budget, conservative
    estimate) has been reached.
    """
    return _budget_state()


@mcp.tool()
@recorded(_REC)
def escalate(
    query: str,
    prior_attempt: str | None = None,
    verifier_reason: str | None = None,
    mode: str = "repair",
) -> dict:
    """Paid escalation to Tier 4. The credit guard is the OUTERMOST gate.

    mode="repair"  (default): diagnose+fix, prior_attempt fed as context.
    mode="critic": clean context -- prior_attempt is DROPPED and a fresh
      reviewer preamble is prepended (the deadlock breaker).

    Refuses without spending if the guard is tripped. Returns
    {ok, refused, disabled, text, model, est_cost_usd, in_tok, out_tok,
     budget}.
    """
    # 1. Credit guard FIRST -- the outermost safety. Trips with no key/spend
    #    too (e.g. CASCADE_CLOUD_MAX_CALLS=0), so the budget ceiling can never
    #    be bypassed by ordering.
    state = _budget_state()
    if state["guard_tripped"]:
        return {
            "ok": False, "refused": True, "disabled": False,
            "text": "[refused: credit guard tripped -- not calling the paid "
                    "tier]",
            "model": CONFIG.cloud_model, "est_cost_usd": 0.0,
            "in_tok": 0, "out_tok": 0, "budget": state,
        }

    # 2. No key / not enabled -> safe no-op (mirrors CloudWorker).
    if not _worker.enabled:
        return {
            "ok": False, "refused": False, "disabled": True,
            "text": "[paid cloud tier disabled -- no ANTHROPIC_API_KEY]",
            "model": CONFIG.cloud_model, "est_cost_usd": 0.0,
            "in_tok": 0, "out_tok": 0, "budget": state,
        }

    # 3. Compose per mode, then spend.
    if mode == "critic":
        user_query, prior = _CRITIC_PREAMBLE + query, None
    else:
        user_query, prior = query, prior_attempt
    if verifier_reason:
        user_query += f"\n\n[automated verifier rejected the prior attempt: " \
                      f"{verifier_reason}]"

    res = _worker.generate(user_query, prior_attempt=prior)
    cost = est_cost_usd(res)
    _guard.charge(cost)
    return {
        "ok": res.available, "refused": False, "disabled": False,
        "text": res.text, "model": res.model,
        "est_cost_usd": round(cost, 6),
        "in_tok": res.input_tokens, "out_tok": res.output_tokens,
        "budget": _budget_state(),
    }


if __name__ == "__main__":
    mcp.run()
