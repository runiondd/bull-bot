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


def test_max_loss_bull_call_spread_is_width_minus_credit():
    """Bull call spread: buy 190 call @ $4, sell 200 call @ $1.50.
    Net debit = $2.50. Width = $10. Max loss = net debit = $250 per contract."""
    legs = [
        _call("buy", strike=190.0, premium=4.00, qty=1),
        _call("sell", strike=200.0, premium=1.50, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=190.0) == pytest.approx(250.0)


def test_max_loss_bear_put_spread_is_net_debit():
    """Buy 190 put @ $3, sell 180 put @ $1. Net debit = $2 → max loss $200."""
    legs = [
        _put("buy", strike=190.0, premium=3.00, qty=1),
        _put("sell", strike=180.0, premium=1.00, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=190.0) == pytest.approx(200.0)


def test_max_loss_bull_put_credit_spread_is_width_minus_credit():
    """Sell 190 put @ $3, buy 180 put @ $1. Width $10, credit $2.
    Max loss = (width - credit) × 100 = $800 per contract."""
    legs = [
        _put("sell", strike=190.0, premium=3.00, qty=1),
        _put("buy", strike=180.0, premium=1.00, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=190.0) == pytest.approx(800.0)


def test_max_loss_bear_call_credit_spread_is_width_minus_credit():
    legs = [
        _call("sell", strike=200.0, premium=2.50, qty=1),
        _call("buy", strike=210.0, premium=0.75, qty=1),
    ]
    # width $10, credit $1.75 → max loss $825
    assert risk.compute_max_loss(legs, spot=190.0) == pytest.approx(825.0)


def test_max_loss_iron_condor_is_max_wing_width_minus_credit():
    """IC: sell 110c@$2 / buy 115c@$0.50 / sell 90p@$2 / buy 85p@$0.50.
    Credit per side = $1.50; total credit = $3.00. Each wing is $5 wide.
    Max loss on either side = ($5 − $3) × 100 = $200 per contract."""
    legs = [
        _call("sell", strike=110.0, premium=2.00, qty=1),
        _call("buy", strike=115.0, premium=0.50, qty=1),
        _put("sell", strike=90.0, premium=2.00, qty=1),
        _put("buy", strike=85.0, premium=0.50, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=100.0) == pytest.approx(200.0)


def test_max_loss_long_call_butterfly_is_net_debit():
    """Buy 1× 95c @ $6, sell 2× 100c @ $3, buy 1× 105c @ $1.
    Net debit = $6 − 2($3) + $1 = $1 → max loss $100 per contract."""
    legs = [
        _call("buy", strike=95.0, premium=6.00, qty=1),
        _call("sell", strike=100.0, premium=3.00, qty=2),
        _call("buy", strike=105.0, premium=1.00, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=100.0) == pytest.approx(100.0)


def test_max_loss_scaled_butterfly_returns_proportional_loss():
    """Butterfly with qty 5:10:5 (5x scale of 1:2:1) should return 5x the
    max loss of a single butterfly. Bug fix: detector previously hardcoded
    qty == 2 for middle leg."""
    legs = [
        _call("buy", strike=95.0, premium=6.00, qty=5),
        _call("sell", strike=100.0, premium=3.00, qty=10),
        _call("buy", strike=105.0, premium=1.00, qty=5),
    ]
    # Single-unit max loss = ($6 - 2*$3 + $1) * 100 = $100
    # 5-unit max loss = $500
    assert risk.compute_max_loss(legs, spot=100.0) == pytest.approx(500.0)


def test_max_loss_covered_call_is_share_safety_stop_minus_call_credit():
    """Long 100 shares @ $100 + short 105 call @ $1.50.
    Share safety stop = 100 × 100 × 15% = $1500. Call credit = $150.
    Max loss = $1500 − $150 = $1350. (The short call caps the upside but
    bounds the downside only by the premium received.)"""
    legs = [
        _shares("buy", price=100.0, qty=100),
        _call("sell", strike=105.0, premium=1.50, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=100.0) == pytest.approx(1350.0)


def test_max_loss_returns_inf_for_unrecognized_multi_leg_shape():
    """A leg combo we don't have a rule for falls back to inf so risk caps
    reject it. (validate_structure_sanity in C.3 rejects nonsense at LLM
    output time, before max_loss is ever called.)"""
    legs = [
        _call("buy", strike=100.0, premium=2.0, qty=1),
        _put("buy", strike=110.0, premium=1.0, qty=1),
        _call("sell", strike=120.0, premium=0.5, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=100.0) == float("inf")


def test_size_position_returns_qty_fitting_max_loss_cap_long_call():
    """Cap = 2% of $50,000 NAV = $1,000. A single contract at $2.50 premium
    risks $250. 4 contracts risks $1000. Helper returns 4."""
    leg_template = _call("buy", strike=190.0, premium=2.50, qty=1)
    sized_qty = risk.size_position(
        leg_template=leg_template, nav=50_000.0, max_loss_pct=0.02, spot=190.0,
    )
    assert sized_qty == 4


def test_size_position_rounds_down_never_exceeds_cap():
    """Cap = $1000. Single contract risks $300 (3.00 premium). Should return
    3 contracts ($900 risk), not 4 ($1200)."""
    leg_template = _call("buy", strike=190.0, premium=3.00, qty=1)
    sized_qty = risk.size_position(
        leg_template=leg_template, nav=50_000.0, max_loss_pct=0.02, spot=190.0,
    )
    assert sized_qty == 3


def test_size_position_returns_zero_when_single_contract_exceeds_cap():
    """Premium $15 → $1500 per contract. Cap $1000. Returns 0 → caller emits
    skipped_max_loss_cap."""
    leg_template = _call("buy", strike=190.0, premium=15.00, qty=1)
    sized_qty = risk.size_position(
        leg_template=leg_template, nav=50_000.0, max_loss_pct=0.02, spot=190.0,
    )
    assert sized_qty == 0


def test_size_position_returns_zero_for_unbounded_loss():
    leg_template = _call("sell", strike=110.0, premium=1.5, qty=1)
    sized_qty = risk.size_position(
        leg_template=leg_template, nav=50_000.0, max_loss_pct=0.02, spot=100.0,
    )
    assert sized_qty == 0


def test_evaluate_caps_passes_when_all_three_satisfied():
    legs = [_call("buy", strike=190.0, premium=2.50, qty=2)]
    result = risk.evaluate_caps(
        legs=legs, spot=190.0, nav=50_000.0,
        per_trade_pct=0.02, per_ticker_pct=0.15, max_open_positions=12,
        current_ticker_concentration_dollars=0.0,
        current_open_positions=5,
    )
    assert result.ok is True
    assert result.reason is None


def test_evaluate_caps_fails_on_per_trade_overflow():
    legs = [_call("buy", strike=190.0, premium=8.00, qty=2)]
    # max_loss = $1600; cap = $1000.
    result = risk.evaluate_caps(
        legs=legs, spot=190.0, nav=50_000.0,
        per_trade_pct=0.02, per_ticker_pct=0.15, max_open_positions=12,
        current_ticker_concentration_dollars=0.0,
        current_open_positions=5,
    )
    assert result.ok is False
    assert result.reason == "skipped_max_loss_cap"


def test_evaluate_caps_fails_on_ticker_concentration():
    legs = [_call("buy", strike=190.0, premium=2.50, qty=1)]
    # max_loss = $250. Ticker cap = 15% of $50k = $7500. We already have
    # $7400 deployed in this ticker → $7650 > $7500 → reject.
    result = risk.evaluate_caps(
        legs=legs, spot=190.0, nav=50_000.0,
        per_trade_pct=0.02, per_ticker_pct=0.15, max_open_positions=12,
        current_ticker_concentration_dollars=7400.0,
        current_open_positions=5,
    )
    assert result.ok is False
    assert result.reason == "skipped_ticker_concentration"


def test_evaluate_caps_fails_on_max_open_positions():
    legs = [_call("buy", strike=190.0, premium=2.50, qty=1)]
    result = risk.evaluate_caps(
        legs=legs, spot=190.0, nav=50_000.0,
        per_trade_pct=0.02, per_ticker_pct=0.15, max_open_positions=12,
        current_ticker_concentration_dollars=0.0,
        current_open_positions=12,
    )
    assert result.ok is False
    assert result.reason == "skipped_max_positions"


def test_evaluate_caps_checks_in_order_per_trade_first():
    """If both per-trade and ticker would fail, per-trade is reported (it's
    the cheapest to fix at the prompt level)."""
    # Premium $11 → max_loss = $1100, exceeds per-trade cap of $1000.
    # Ticker concentration: $7400 + $1100 = $8500, also exceeds $7500.
    # Per-trade should be reported first.
    legs = [_call("buy", strike=190.0, premium=11.00, qty=1)]
    result = risk.evaluate_caps(
        legs=legs, spot=190.0, nav=50_000.0,
        per_trade_pct=0.02, per_ticker_pct=0.15, max_open_positions=12,
        current_ticker_concentration_dollars=7400.0,
        current_open_positions=12,
    )
    assert result.ok is False
    assert result.reason == "skipped_max_loss_cap"
