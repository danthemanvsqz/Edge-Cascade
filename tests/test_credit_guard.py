"""CreditGuard — the single trusted spend gate. Pure, so it reaches 100% with
plain assertions: every trip path (call cap, USD ceiling), the enabled flag,
charge accumulation, and the state dict."""
from cascade.credit_guard import CreditGuard


def test_allowed_when_under_both_limits():
    g = CreditGuard(max_calls=3, usd_budget=0.50)
    assert g.allowed is True and g.tripped is False


def test_trips_on_call_cap():
    g = CreditGuard(max_calls=1, usd_budget=0.50)
    g.charge(0.0)                      # 1 call, $0 spent
    assert g.calls_used == 1
    assert g.tripped is True and g.allowed is False


def test_trips_on_usd_ceiling():
    g = CreditGuard(max_calls=10, usd_budget=0.10)
    g.charge(0.10)                     # hits the USD ceiling exactly
    assert g.tripped is True and g.allowed is False


def test_disabled_is_never_allowed_even_when_under_limits():
    g = CreditGuard(max_calls=3, usd_budget=0.50, enabled=False)
    assert g.tripped is False and g.allowed is False


def test_zero_call_cap_trips_before_any_spend():
    # CASCADE_CLOUD_MAX_CALLS=0 must refuse without spending (ordering can't
    # bypass the ceiling) — the same guarantee the edge-cloud server relies on.
    g = CreditGuard(max_calls=0, usd_budget=0.50)
    assert g.tripped is True and g.allowed is False


def test_charge_accumulates_and_state_reports():
    g = CreditGuard(max_calls=3, usd_budget=1.0)
    g.charge(0.2)
    g.charge(0.3)
    s = g.state()
    assert s == {
        "calls_used": 2, "calls_max": 3, "usd_spent": 0.5, "usd_budget": 1.0,
        "guard_tripped": False, "enabled": True, "allowed": True,
    }
