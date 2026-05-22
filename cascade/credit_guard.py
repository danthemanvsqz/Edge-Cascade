"""Credit guard for the paid tier — the single source of the spend ceiling.

Lifted out of mcp_servers/cloud.py so the `edge-cloud` server AND the PR
reviewer (cascade.reviewer) enforce the SAME limits: config carries the limits,
this enforces them. Pure + total (no I/O) → 100% unit-tested, because this is
the mechanism that keeps paid spend bounded. The cascade build path stays $0;
this gate bounds the *sanctioned* spend lanes (cloud escalation, PR review).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CreditGuard:
    """Per-run paid-spend accumulator + ceiling. `max_calls`/`usd_budget` are the
    hard limits (from config); `enabled` reflects key-present + opt-in. The guard
    trips when EITHER ceiling is reached; a call is `allowed` only when enabled
    and not tripped. `charge()` records one paid call's (estimated) cost."""

    max_calls: int
    usd_budget: float
    enabled: bool = True
    calls_used: int = 0
    usd_spent: float = 0.0

    @property
    def tripped(self) -> bool:
        return (self.calls_used >= self.max_calls
                or self.usd_spent >= self.usd_budget)

    @property
    def allowed(self) -> bool:
        return self.enabled and not self.tripped

    def charge(self, cost_usd: float) -> None:
        self.calls_used += 1
        self.usd_spent += cost_usd

    def state(self) -> dict:
        return {
            "calls_used": self.calls_used,
            "calls_max": self.max_calls,
            "usd_spent": round(self.usd_spent, 6),
            "usd_budget": self.usd_budget,
            "guard_tripped": self.tripped,
            "enabled": self.enabled,
            "allowed": self.allowed,
        }
