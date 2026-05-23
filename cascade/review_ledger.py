"""Persistent review-spend ledger (SQLite) — the cross-run guards.

Enforces, across runs (the per-call credit guard is in-process; this survives):
  * a DAILY review budget (USD),
  * a per-PR round cap,
  * HEAD dedup (don't pay to re-review an unchanged commit).

SQLite (single local file, ACID) — NOT Redis: the spend ledger must be DURABLE
without a running broker. A down Redis used to silently disable every guard
(observed on PR #39). The metered API is a fixed/prepaid spend ("can't spend
what's not loaded"), so the daily cap is long-term budget *health*, not a hard
safety limit; on the rare event the DB can't be read the reads fail SOFT
(unknown/zero, don't block) and the per-call credit guard still bounds each
review. `db_path` is injectable, so the logic is 100% unit-tested against a tmp
file — no server, no network.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass
class ReviewLedger:
    db_path: str
    daily_usd: float

    def _conn(self) -> sqlite3.Connection:
        """Open the ledger DB, creating the (single) table on first use. Each
        call is a short-lived connection — the ledger is touched at most once
        per review, so simplicity + durability beats a pooled handle."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS reviews ("
            "pr TEXT NOT NULL, sha TEXT NOT NULL, cost_usd REAL NOT NULL, "
            "day TEXT NOT NULL, ts REAL NOT NULL)")
        return conn

    def spent_today(self) -> float | None:
        """Today's review spend in USD; None only if the DB can't be read."""
        try:
            with closing(self._conn()) as c:
                (total,) = c.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0.0) FROM reviews "
                    "WHERE day = ?", (_today(),)).fetchone()
            return float(total)
        except sqlite3.Error:
            return None  # fail-soft: unknown, not a crash

    def daily_ok(self) -> bool:
        """True if today's spend is under the cap. Unknown (DB unreadable) ->
        True: don't block on a missing ledger — the per-call credit guard still
        bounds each review and the metered API is a fixed/prepaid spend."""
        s = self.spent_today()
        return True if s is None else s < self.daily_usd

    def remaining_today(self) -> float | None:
        s = self.spent_today()
        return None if s is None else max(0.0, self.daily_usd - s)

    def rounds_for(self, pr: str) -> int:
        """Reviews recorded for this PR (0 if unknown / DB unreadable)."""
        try:
            with closing(self._conn()) as c:
                (n,) = c.execute(
                    "SELECT COUNT(*) FROM reviews WHERE pr = ?", (pr,)).fetchone()
            return int(n)
        except sqlite3.Error:
            return 0

    def last_sha(self, pr: str) -> str:
        """The commit SHA of the most recent review for this PR ('' if none)."""
        try:
            with closing(self._conn()) as c:
                row = c.execute(
                    "SELECT sha FROM reviews WHERE pr = ? ORDER BY ts DESC "
                    "LIMIT 1", (pr,)).fetchone()
            return row[0] if row else ""
        except sqlite3.Error:
            return ""

    def record(self, pr: str, sha: str, cost_usd: float) -> bool:
        """Persist one review (pr, sha, cost, day, ts). False if the write
        failed (the caller proceeds on the per-call guard alone)."""
        try:
            with closing(self._conn()) as c:
                c.execute(
                    "INSERT INTO reviews (pr, sha, cost_usd, day, ts) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (pr, sha, float(cost_usd), _today(), time.time()))
                c.commit()
            return True
        except sqlite3.Error:
            return False
