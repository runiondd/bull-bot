"""Position sizer — fixed 2% of equity at risk per position."""
from bullbot.engine import position_sizer


def test_basic_2_percent_sizing():
    # $50k equity × 2% = $1000 risk. Max loss per contract $500 → 2 contracts.
    n = position_sizer.size_position(equity=50_000, max_loss_per_contract=500.0)
    assert n == 2


def test_rounds_down_not_up():
    # $50k × 2% = $1000 risk. Max loss $300 → floor(1000/300) = 3, not 4
    n = position_sizer.size_position(equity=50_000, max_loss_per_contract=300.0)
    assert n == 3


def test_returns_zero_when_one_contract_exceeds_cap():
    # Max loss $1500 > $1000 risk budget → cannot size any contracts
    n = position_sizer.size_position(equity=50_000, max_loss_per_contract=1500.0)
    assert n == 0


def test_scales_with_equity_growth():
    # Equity drifts up → more contracts
    n = position_sizer.size_position(equity=75_000, max_loss_per_contract=500.0)
    assert n == 3   # $1500 budget / $500 = 3


def test_respects_max_per_ticker_cap(monkeypatch):
    from bullbot import config
    monkeypatch.setattr(config, "MAX_POSITIONS_PER_TICKER", 3)
    # Raw math would say 20 contracts, but cap limits to 3
    n = position_sizer.size_position(equity=1_000_000, max_loss_per_contract=500.0)
    assert n == 3
