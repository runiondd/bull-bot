import pytest

from bullbot.leaderboard.scoring import compute_score_a


def test_30_day_options_trade():
    # $50 pnl on $500 BP for 30 days
    # raw = 50/500 = 0.10. annualized = 0.10 * (365/30) = 1.2167
    s = compute_score_a(pnl=50, max_bp_held=500, days_held=30)
    assert s == pytest.approx(1.2167, abs=0.001)


def test_2_year_equity_trade():
    # $12,000 pnl on $50,000 BP for 730 days
    # raw = 0.24. annualized = 0.24 * (365/730) = 0.12
    s = compute_score_a(pnl=12_000, max_bp_held=50_000, days_held=730)
    assert s == pytest.approx(0.12, abs=0.001)


def test_zero_bp_returns_zero():
    s = compute_score_a(pnl=100, max_bp_held=0, days_held=30)
    assert s == 0.0


def test_zero_days_returns_zero():
    s = compute_score_a(pnl=100, max_bp_held=500, days_held=0)
    assert s == 0.0
