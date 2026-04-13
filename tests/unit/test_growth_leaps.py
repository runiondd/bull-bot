import pytest
from bullbot.strategies.growth_leaps import GrowthLEAPS
from bullbot.strategies.base import StrategySnapshot
from bullbot.data.schemas import Bar, OptionContract


def _make_bars(n=60, base_close=250.0):
    return [
        Bar(ticker="TSLA", timeframe="1d", ts=86400 * i,
            open=base_close, high=base_close + 5, low=base_close - 5,
            close=base_close, volume=1000000, source="uw")
        for i in range(n)
    ]


def _make_chain(spot=250.0, expiry="1970-11-06", ts=86400 * 59):
    # expiry is ~250 days after the last bar (ts=86400*59 = 1970-03-01),
    # ensuring DTE falls within min_dte=180 / max_dte=365
    contracts = []
    for strike in range(200, 350, 10):
        mid = max(1.0, (spot - strike) + 30.0) if strike < spot else max(0.50, 30.0 - (strike - spot) * 0.15)
        contracts.append(OptionContract(
            ticker="TSLA", expiry=expiry, strike=float(strike), kind="C",
            ts=ts, nbbo_bid=mid - 0.50, nbbo_ask=mid + 0.50,
            volume=100, open_interest=500, iv=0.45,
        ))
    return contracts


def _make_snapshot(regime="bull", iv_rank=40.0):
    bars = _make_bars()
    chain = _make_chain(ts=bars[-1].ts)
    return StrategySnapshot(
        ticker="TSLA", asof_ts=bars[-1].ts, spot=250.0,
        bars_1d=bars, indicators={"rsi_14": 55.0}, atm_greeks={},
        iv_rank=iv_rank, regime=regime, chain=chain,
    )


def test_growth_leaps_opens_in_bull():
    strat = GrowthLEAPS(params={
        "target_delta": 0.70, "min_dte": 180, "max_dte": 365,
        "iv_rank_max": 60, "profit_target_pct": 1.0,
        "stop_loss_mult": 0.50, "min_dte_close": 30,
    })
    snap = _make_snapshot(regime="bull", iv_rank=40.0)
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is not None
    assert signal.intent == "open"
    assert len(signal.legs) == 1
    assert signal.legs[0].kind == "C"
    assert signal.legs[0].side == "long"


def test_growth_leaps_skips_in_bear_with_regime_filter():
    strat = GrowthLEAPS(params={
        "target_delta": 0.70, "min_dte": 180, "max_dte": 365,
        "iv_rank_max": 60, "regime_filter": ["bull", "chop"],
        "profit_target_pct": 1.0, "stop_loss_mult": 0.50, "min_dte_close": 30,
    })
    snap = _make_snapshot(regime="bear")
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is None


def test_growth_leaps_skips_high_iv():
    strat = GrowthLEAPS(params={
        "target_delta": 0.70, "min_dte": 180, "max_dte": 365,
        "iv_rank_max": 30, "profit_target_pct": 1.0,
        "stop_loss_mult": 0.50, "min_dte_close": 30,
    })
    snap = _make_snapshot(iv_rank=50.0)
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is None


def test_growth_leaps_skips_when_position_open():
    strat = GrowthLEAPS(params={
        "target_delta": 0.70, "min_dte": 180, "max_dte": 365,
        "iv_rank_max": 60, "profit_target_pct": 1.0,
        "stop_loss_mult": 0.50, "min_dte_close": 30,
    })
    snap = _make_snapshot()
    signal = strat.evaluate(snap, open_positions=[{"id": 1}])
    assert signal is None


def test_growth_leaps_max_loss():
    strat = GrowthLEAPS(params={
        "target_delta": 0.70, "min_dte": 180, "max_dte": 365,
        "iv_rank_max": 60,
    })
    assert strat.max_loss_per_contract() > 0
