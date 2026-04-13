"""Position sizer — category-aware sizing against income or growth capital pool."""
from bullbot.engine import position_sizer


def test_basic_2_percent_sizing():
    # Bull regime, income category (default): income pool = 60% of 50k = 30k
    # 2% of 30k = $600 risk budget, max_loss=500 -> 1 contract
    n = position_sizer.size_position(equity=50_000, max_loss_per_contract=500.0)
    assert n == 1


def test_rounds_down_not_up():
    # Bull regime, income: 2% of 30k = $600. Max loss $300 -> floor(600/300) = 2
    n = position_sizer.size_position(equity=50_000, max_loss_per_contract=300.0)
    assert n == 2


def test_returns_zero_when_one_contract_exceeds_cap():
    # Bull regime, income: 2% of 30k = $600. Max loss $1500 > $600 -> 0 contracts
    n = position_sizer.size_position(equity=50_000, max_loss_per_contract=1500.0)
    assert n == 0


def test_scales_with_equity_growth():
    # Bull regime, income: income pool = 60% of 75k = 45k
    # 2% of 45k = $900, max_loss=500 -> floor(900/500) = 1
    n = position_sizer.size_position(equity=75_000, max_loss_per_contract=500.0)
    assert n == 1


def test_respects_max_per_ticker_cap(monkeypatch):
    from bullbot import config
    monkeypatch.setattr(config, "MAX_POSITIONS_PER_TICKER", 3)
    # Raw math would say many contracts, but cap limits to 3
    n = position_sizer.size_position(equity=1_000_000, max_loss_per_contract=500.0)
    assert n == 3
