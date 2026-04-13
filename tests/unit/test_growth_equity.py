import pytest
from bullbot.strategies.growth_equity import GrowthEquity
from bullbot.strategies.base import StrategySnapshot
from bullbot.data.schemas import Bar


def _make_bars(n=60, base_close=250.0):
    return [
        Bar(ticker="TSLA", timeframe="1d", ts=86400 * i,
            open=base_close, high=base_close + 5, low=base_close - 5,
            close=base_close, volume=1000000, source="uw")
        for i in range(n)
    ]


def _make_snapshot(regime="bull"):
    bars = _make_bars()
    return StrategySnapshot(
        ticker="TSLA", asof_ts=bars[-1].ts, spot=250.0,
        bars_1d=bars, indicators={"rsi_14": 55.0}, atm_greeks={},
        iv_rank=40.0, regime=regime, chain=[],
    )


def test_growth_equity_opens_in_bull():
    strat = GrowthEquity(params={"regime_filter": ["bull", "chop"]})
    snap = _make_snapshot(regime="bull")
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is not None
    assert signal.intent == "open"
    assert signal.strategy_class == "GrowthEquity"


def test_growth_equity_skips_in_bear_with_filter():
    strat = GrowthEquity(params={"regime_filter": ["bull"]})
    snap = _make_snapshot(regime="bear")
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is None


def test_growth_equity_no_filter_opens_any_regime():
    strat = GrowthEquity(params={})
    snap = _make_snapshot(regime="bear")
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is not None


def test_growth_equity_skips_when_position_open():
    strat = GrowthEquity(params={})
    snap = _make_snapshot()
    signal = strat.evaluate(snap, open_positions=[{"id": 1}])
    assert signal is None


def test_growth_equity_max_loss():
    strat = GrowthEquity(params={"stop_loss_pct": 0.15})
    assert strat.max_loss_per_contract() > 0
