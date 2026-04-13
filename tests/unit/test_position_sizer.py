"""Position sizer — separate account sizing for income ($50k) and growth ($215k)."""
from bullbot.engine import position_sizer


def test_basic_2_percent_sizing():
    # Income: full equity pool = 50k, 2% = $1000, max_loss=500 -> 2 contracts
    n = position_sizer.size_position(equity=50_000, max_loss_per_contract=500.0)
    assert n == 2


def test_rounds_down_not_up():
    # Income: 2% of 50k = $1000, max_loss=300 -> floor(1000/300) = 3
    n = position_sizer.size_position(equity=50_000, max_loss_per_contract=300.0)
    assert n == 3


def test_returns_zero_when_one_contract_exceeds_cap():
    # Income: 2% of 50k = $1000, max_loss=1500 -> 0
    n = position_sizer.size_position(equity=50_000, max_loss_per_contract=1500.0)
    assert n == 0


def test_scales_with_equity_growth():
    # Income: 2% of 75k = $1500, max_loss=500 -> 3 (capped at MAX_POSITIONS_PER_TICKER)
    n = position_sizer.size_position(equity=75_000, max_loss_per_contract=500.0)
    assert n == 3


def test_respects_max_per_ticker_cap(monkeypatch):
    from bullbot import config
    monkeypatch.setattr(config, "MAX_POSITIONS_PER_TICKER", 3)
    n = position_sizer.size_position(equity=1_000_000, max_loss_per_contract=500.0)
    assert n == 3
