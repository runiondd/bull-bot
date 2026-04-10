"""Strategy base class shape tests."""
import pytest

from bullbot.strategies.base import Strategy, StrategySnapshot
from bullbot.data.schemas import Bar


def test_strategy_is_abstract():
    with pytest.raises(TypeError):
        Strategy(params={})


def test_strategy_subclass_implements_evaluate():
    class Noop(Strategy):
        CLASS_NAME = "Noop"
        CLASS_VERSION = 1

        def evaluate(self, snapshot, open_positions):
            return None

        def max_loss_per_contract(self) -> float:
            return 100.0

    s = Noop(params={})
    assert s.CLASS_NAME == "Noop"
    assert s.evaluate(None, []) is None
    assert s.max_loss_per_contract() == 100.0


def test_strategy_snapshot_fields():
    snap = StrategySnapshot(
        ticker="SPY",
        asof_ts=1718395200,
        spot=582.14,
        bars_1d=[],
        indicators={"sma_20": 578.45, "rsi_14": 58.4},
        atm_greeks={"delta": 0.52, "iv": 0.143},
        iv_rank=34.0,
        regime="bull",
        chain=[],
    )
    assert snap.ticker == "SPY"
    assert snap.regime == "bull"
