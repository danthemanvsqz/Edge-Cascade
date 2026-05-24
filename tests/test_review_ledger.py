"""ReviewLedger logic at 100% against a real SQLite tmp file (no server/network).
Covers the daily budget, per-PR rounds, last-sha dedup, durability across
instances, and the fail-soft paths (DB unreadable -> unknown/zero, never a
crash). SQLite replaced Redis so the spend guards persist without a broker."""
from cascade.review_ledger import ReviewLedger


def _ledger(tmp_path, daily=5.0):
    return ReviewLedger(db_path=str(tmp_path / "review-ledger.db"), daily_usd=daily)


def test_empty_ledger_is_under_budget(tmp_path):
    g = _ledger(tmp_path)
    assert g.spent_today() == 0.0
    assert g.daily_ok() is True
    assert g.remaining_today() == 5.0
    assert g.rounds_for("33") == 0
    assert g.last_sha("33") == ""


def test_record_then_reads_reflect_it(tmp_path):
    g = _ledger(tmp_path)
    assert g.record("33", "abc123", 0.20) is True
    assert g.record("33", "def456", 0.30) is True
    assert g.spent_today() == 0.5
    assert g.remaining_today() == 4.5
    assert g.rounds_for("33") == 2
    assert g.last_sha("33") == "def456"        # most recent SHA wins (dedup)


def test_per_pr_rounds_and_last_sha_are_scoped(tmp_path):
    g = _ledger(tmp_path)
    g.record("33", "a", 0.1)
    g.record("40", "b", 0.1)
    assert g.rounds_for("33") == 1
    assert g.rounds_for("40") == 1
    assert g.last_sha("40") == "b"
    assert g.last_sha("99") == ""              # unknown PR -> no row


def test_daily_cap_trips_when_exhausted(tmp_path):
    g = _ledger(tmp_path, daily=0.50)
    g.record("33", "s", 0.50)                  # hit the cap exactly
    assert g.daily_ok() is False
    assert g.remaining_today() == 0.0


def test_durable_across_instances(tmp_path):
    # A fresh ReviewLedger on the same file sees prior spend -> the durability
    # that Redis-down used to lose. This is the reason for the migration.
    db = str(tmp_path / "review-ledger.db")
    assert ReviewLedger(db, 5.0).record("33", "s", 0.40) is True
    assert ReviewLedger(db, 5.0).spent_today() == 0.40


def test_creates_missing_parent_dir(tmp_path):
    # Fresh-checkout durability: a clone with no runs/ dir must still record.
    # Without the parent-dir mkdir, sqlite3.connect raises, every read fails
    # soft, and the ledger silently never records -- the exact hole #40 closes.
    db = str(tmp_path / "runs" / "review-ledger.db")   # parent doesn't exist
    g = ReviewLedger(db_path=db, daily_usd=5.0)
    assert g.record("33", "s", 0.25) is True
    assert g.spent_today() == 0.25


def test_db_error_is_fail_soft(tmp_path):
    # Point at a directory -> sqlite can't open a DB there -> every op fails
    # soft (unknown/zero/False), never a crash, so a hosed ledger can't block
    # a review (the per-call credit guard still bounds spend).
    g = ReviewLedger(db_path=str(tmp_path), daily_usd=5.0)
    assert g.spent_today() is None
    assert g.daily_ok() is True
    assert g.remaining_today() is None
    assert g.rounds_for("33") == 0
    assert g.last_sha("33") == ""
    assert g.record("33", "s", 0.1) is False
