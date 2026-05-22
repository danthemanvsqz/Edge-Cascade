"""Persistent review-spend ledger (Redis) — the cross-run guards.

Enforces, across runs (the per-call credit guard is in-process; this survives):
  * a DAILY review budget (USD),
  * a per-PR round cap,
  * HEAD dedup (don't pay to re-review an unchanged commit).

Fail-soft by design: if Redis is unreachable the reads return unknown/zero and
the caller proceeds on the per-call credit guard alone — a down broker must not
block reviewing. An *exhausted* budget fails CLOSED (the caller skips), which is
the graceful "no tokens left" path. The Redis client is injectable so the logic
is 100% unit-tested with a fake — no server, no network.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

_PREFIX = "edge-review"
_DAY_TTL_S = 60 * 60 * 48  # day spend counters self-expire after 2 days


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass
class ReviewLedger:
    url: str
    daily_usd: float
    client: object | None = None  # injected in tests; lazily made in prod

    def _c(self):
        if self.client is None:  # pragma: no cover - prod path; tests inject
            import redis  # ships with the `celery` extra
            self.client = redis.from_url(
                self.url, socket_connect_timeout=2, socket_timeout=2,
                decode_responses=True)
        return self.client

    def spent_today(self) -> float | None:
        """Today's review spend in USD; None if Redis is unreachable."""
        try:
            v = self._c().get(f"{_PREFIX}:spend:{_today()}")
            return float(v) if v else 0.0
        except Exception:
            return None  # fail-soft: unknown, not a crash

    def daily_ok(self) -> bool:
        """True if today's spend is under the cap. Unknown (Redis down) -> True
        (don't block on a missing ledger; the per-call guard still bounds it)."""
        s = self.spent_today()
        return True if s is None else s < self.daily_usd

    def remaining_today(self) -> float | None:
        s = self.spent_today()
        return None if s is None else max(0.0, self.daily_usd - s)

    def rounds_for(self, pr: str) -> int:
        """Reviews recorded for this PR (0 if unknown / Redis down)."""
        try:
            v = self._c().get(f"{_PREFIX}:rounds:{pr}")
            return int(v) if v else 0
        except Exception:
            return 0

    def last_sha(self, pr: str) -> str:
        """The commit SHA of the most recent review for this PR ('' if none)."""
        try:
            return self._c().get(f"{_PREFIX}:lastsha:{pr}") or ""
        except Exception:
            return ""

    def record(self, pr: str, sha: str, cost_usd: float) -> bool:
        """Persist one review: bump the day spend (+TTL), the per-PR round
        counter, and the last-reviewed SHA. False if Redis was unreachable."""
        try:
            c = self._c()
            day_key = f"{_PREFIX}:spend:{_today()}"
            c.incrbyfloat(day_key, float(cost_usd))
            c.expire(day_key, _DAY_TTL_S)
            c.incr(f"{_PREFIX}:rounds:{pr}")
            c.set(f"{_PREFIX}:lastsha:{pr}", sha)
            return True
        except Exception:
            return False
