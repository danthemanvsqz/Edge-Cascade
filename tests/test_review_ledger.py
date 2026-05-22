"""ReviewLedger logic at 100% with a fake Redis client (no server/network).
The prod lazy-import branch (`client is None`) is pragma-excluded — tests always
inject. Covers daily budget, per-PR rounds, last-sha dedup, and the fail-soft
paths (Redis down -> unknown/zero, never a crash)."""
from cascade.review_ledger import ReviewLedger


class FakeRedis:
    """Minimal in-memory stand-in for the bits the ledger uses."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v):
        self.store[k] = str(v)

    def incrbyfloat(self, k, amt):
        self.store[k] = str(float(self.store.get(k, 0)) + float(amt))
        return self.store[k]

    def incr(self, k):
        self.store[k] = str(int(self.store.get(k, 0)) + 1)
        return self.store[k]

    def expire(self, k, ttl):
        self.expires[k] = ttl


class BoomRedis:
    """Every op raises — simulates Redis being unreachable."""

    def get(self, *a):
        raise RuntimeError("redis down")

    def incrbyfloat(self, *a):
        raise RuntimeError("redis down")


def _ledger(client, daily=5.0):
    return ReviewLedger(url="redis://x", daily_usd=daily, client=client)


def test_empty_ledger_is_under_budget():
    g = _ledger(FakeRedis())
    assert g.spent_today() == 0.0
    assert g.daily_ok() is True
    assert g.remaining_today() == 5.0
    assert g.rounds_for("33") == 0
    assert g.last_sha("33") == ""


def test_record_then_reads_reflect_it():
    fake = FakeRedis()
    g = _ledger(fake)
    assert g.record("33", "abc123", 0.20) is True
    assert g.record("33", "def456", 0.30) is True
    assert g.spent_today() == 0.5
    assert g.remaining_today() == 4.5
    assert g.rounds_for("33") == 2
    assert g.last_sha("33") == "def456"
    assert fake.expires  # the day key got a TTL


def test_daily_cap_trips_when_exhausted():
    g = _ledger(FakeRedis(), daily=0.50)
    g.record("33", "s", 0.50)              # hit the cap exactly
    assert g.daily_ok() is False
    assert g.remaining_today() == 0.0


def test_redis_down_is_fail_soft():
    g = _ledger(BoomRedis())
    assert g.spent_today() is None         # unknown, not a crash
    assert g.daily_ok() is True            # don't block on a down ledger
    assert g.remaining_today() is None
    assert g.rounds_for("33") == 0
    assert g.last_sha("33") == ""
    assert g.record("33", "s", 0.1) is False
