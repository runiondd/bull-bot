"""Unit tests for bullbot.v2.risk — deterministic max-loss math + caps."""
from __future__ import annotations

import pytest

from bullbot.v2 import risk
from bullbot.v2.positions import OptionLeg


def _call(action, strike, premium, qty=1, expiry="2026-06-19"):
    return OptionLeg(
        action=action, kind="call", strike=strike,
        expiry=expiry, qty=qty, entry_price=premium,
    )


def _put(action, strike, premium, qty=1, expiry="2026-06-19"):
    return OptionLeg(
        action=action, kind="put", strike=strike,
        expiry=expiry, qty=qty, entry_price=premium,
    )


def _shares(action, price, qty=100):
    return OptionLeg(
        action=action, kind="share", strike=None, expiry=None,
        qty=qty, entry_price=price,
    )


def test_max_loss_long_call_equals_premium_paid():
    leg = _call("buy", strike=190.0, premium=2.50, qty=1)
    # 1 contract × $2.50 premium × 100 multiplier = $250
    assert risk.compute_max_loss([leg], spot=190.0) == 250.0


def test_max_loss_long_call_scales_with_qty():
    leg = _call("buy", strike=190.0, premium=2.50, qty=3)
    assert risk.compute_max_loss([leg], spot=190.0) == 750.0


def test_max_loss_long_put_equals_premium_paid():
    leg = _put("buy", strike=180.0, premium=1.75, qty=2)
    assert risk.compute_max_loss([leg], spot=190.0) == 350.0


def test_max_loss_long_shares_uses_15pct_safety_stop():
    """Phase C safety stop is 15% adverse from entry (design §4.7).
    Max loss is the dollar size of that worst-case move."""
    leg = _shares("buy", price=100.0, qty=100)
    # 100 shares × $100 entry × 15% = $1500
    assert risk.compute_max_loss([leg], spot=100.0) == 1500.0


def test_max_loss_short_shares_uses_15pct_safety_stop():
    leg = _shares("sell", price=100.0, qty=50)
    # 50 shares × $100 entry × 15% = $750
    assert risk.compute_max_loss([leg], spot=100.0) == 750.0


def test_max_loss_short_put_csp_is_strike_minus_credit_per_contract():
    """CSP max loss = (strike − credit) × 100 × qty. Strike $100, credit $2,
    1 contract → $9,800 (the price you'd pay if assigned at zero)."""
    leg = _put("sell", strike=100.0, premium=2.00, qty=1)
    assert risk.compute_max_loss([leg], spot=100.0) == pytest.approx(9800.0)


def test_max_loss_short_call_naked_is_unbounded_returns_inf():
    """Naked short call has theoretically infinite loss. We return inf so
    risk caps will always reject it. (covered_call is handled separately —
    that's a multi-leg structure tested in Task 7.)"""
    leg = _call("sell", strike=110.0, premium=1.50, qty=1)
    assert risk.compute_max_loss([leg], spot=100.0) == float("inf")
