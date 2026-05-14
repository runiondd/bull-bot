from dataclasses import dataclass
import pytest
from bullbot.risk.sizing import size_strategy, SizingResult


@dataclass
class FakeStrategy:
    class_name: str
    max_loss_per_contract: float
    is_equity: bool = False
    stop_loss_pct: float | None = None
    spot: float | None = None


def test_put_credit_spread_at_350_max_loss():
    strat = FakeStrategy(class_name="PutCreditSpread", max_loss_per_contract=350.0)
    res = size_strategy(strat, portfolio_value=265_000, max_loss_pct=0.02)
    # 2% of $265k = $5300. 5300 / 350 = 15.14 -> floor to 15
    assert res.size_units == 15
    assert res.worst_case_loss == 5250.0
    assert res.passes_gate


def test_equity_with_stop_loss_at_20pct():
    strat = FakeStrategy(class_name="GrowthEquity", max_loss_per_contract=0,
                         is_equity=True, stop_loss_pct=0.20, spot=500.0)
    res = size_strategy(strat, portfolio_value=265_000, max_loss_pct=0.02)
    # max loss per share = 500 * 0.20 = $100. shares allowed = 5300 / 100 = 53
    assert res.size_units == 53
    assert res.worst_case_loss == pytest.approx(5300, abs=10)
    assert res.passes_gate


def test_equity_with_no_stop_loss_sized_tiny():
    strat = FakeStrategy(class_name="GrowthEquity", max_loss_per_contract=0,
                         is_equity=True, stop_loss_pct=None, spot=500.0)
    res = size_strategy(strat, portfolio_value=265_000, max_loss_pct=0.02)
    # no stop loss -> assume 100% loss possible -> shares = 5300 / 500 = 10
    assert res.size_units == 10
    assert res.passes_gate


def test_strategy_whose_min_contract_exceeds_cap():
    # 1 contract loses $10k, cap is $5300 -> fail gate, size=0
    strat = FakeStrategy(class_name="LongCall", max_loss_per_contract=10_000)
    res = size_strategy(strat, portfolio_value=265_000, max_loss_pct=0.02)
    assert res.size_units == 0
    assert not res.passes_gate
