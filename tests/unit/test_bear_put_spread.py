"""BearPutSpread strategy tests."""
from bullbot.data.schemas import OptionContract
from bullbot.strategies.base import StrategySnapshot
from bullbot.strategies.bear_put_spread import BearPutSpread

# ts=1700000000 => 2023-11-14; expiry ~30 days later
_TS = 1700000000
_EXPIRY = "2023-12-14"
_SPOT = 250.0


def _make_puts(spot: float = _SPOT, expiry: str = _EXPIRY) -> list[OptionContract]:
    """Build puts at $5 intervals from 200 to 295."""
    contracts = []
    for strike in range(200, 300, 5):
        contracts.append(
            OptionContract(
                ticker="SPY",
                expiry=expiry,
                strike=float(strike),
                kind="P",
                ts=_TS,
                nbbo_bid=2.00,
                nbbo_ask=2.20,
                iv=0.20,
                volume=500,
                open_interest=2000,
            )
        )
    return contracts


def _snap(
    regime: str = "bear",
    iv_rank: float = 55.0,
    chain=None,
    spot: float = _SPOT,
) -> StrategySnapshot:
    return StrategySnapshot(
        ticker="SPY",
        asof_ts=_TS,
        spot=spot,
        bars_1d=[],
        indicators={},
        atm_greeks={},
        iv_rank=iv_rank,
        regime=regime,
        chain=chain if chain is not None else _make_puts(spot=spot),
    )


# ---------------------------------------------------------------------------
# Test 1: Opens in bear regime with sufficient IV
# ---------------------------------------------------------------------------

def test_opens_in_bear_regime_with_sufficient_iv():
    # long_delta=0.42: the strategy's estimator gives delta=0.42 at strike=210
    # (formula: (210-250)/(2*250)+0.50=0.42). Short put at width=10 below => strike=200.
    strategy = BearPutSpread(params={
        "dte": 30,
        "long_delta": 0.42,
        "width": 10,
        "iv_rank_min": 30,
        "regime_filter": ["bear"],
    })
    signal = strategy.evaluate(_snap(regime="bear", iv_rank=55.0), open_positions=[])
    assert signal is not None
    assert signal.intent == "open"
    assert signal.strategy_class == "BearPutSpread"
    assert len(signal.legs) == 2
    # Long leg is the higher-strike put, short leg is 10 points lower
    long_leg = next(l for l in signal.legs if l.side == "long")
    short_leg = next(l for l in signal.legs if l.side == "short")
    assert long_leg.kind == "P"
    assert short_leg.kind == "P"
    assert long_leg.strike - short_leg.strike == 10.0


# ---------------------------------------------------------------------------
# Test 2: Skips when IV below iv_rank_min
# ---------------------------------------------------------------------------

def test_skips_when_iv_rank_below_min():
    strategy = BearPutSpread(params={
        "dte": 30,
        "long_delta": 0.40,
        "width": 10,
        "iv_rank_min": 50,
    })
    signal = strategy.evaluate(_snap(iv_rank=20.0), open_positions=[])
    assert signal is None


# ---------------------------------------------------------------------------
# Test 3: Respects regime_filter — skips when regime is not in filter
# ---------------------------------------------------------------------------

def test_respects_regime_filter_bull_blocked():
    strategy = BearPutSpread(params={
        "dte": 30,
        "long_delta": 0.42,
        "width": 10,
        "regime_filter": ["bear"],
    })
    signal = strategy.evaluate(_snap(regime="bull"), open_positions=[])
    assert signal is None


def test_respects_regime_filter_bear_allowed():
    strategy = BearPutSpread(params={
        "dte": 30,
        "long_delta": 0.42,
        "width": 10,
        "regime_filter": ["bear"],
    })
    signal = strategy.evaluate(_snap(regime="bear"), open_positions=[])
    assert signal is not None


def test_no_regime_filter_allows_all_regimes():
    strategy = BearPutSpread(params={
        "dte": 30,
        "long_delta": 0.42,
        "width": 10,
    })
    # Should open in bull regime too when no filter is set
    signal = strategy.evaluate(_snap(regime="bull"), open_positions=[])
    assert signal is not None


# ---------------------------------------------------------------------------
# Test 4: max_loss_per_contract returns width * 100
# ---------------------------------------------------------------------------

def test_max_loss_per_contract_default_width():
    strategy = BearPutSpread(params={})
    assert strategy.max_loss_per_contract() == 10 * 100  # default width=10


def test_max_loss_per_contract_custom_width():
    strategy = BearPutSpread(params={"width": 5})
    assert strategy.max_loss_per_contract() == 500.0


def test_max_loss_per_contract_large_width():
    strategy = BearPutSpread(params={"width": 25})
    assert strategy.max_loss_per_contract() == 2500.0


# ---------------------------------------------------------------------------
# Additional: skips when open positions exist
# ---------------------------------------------------------------------------

def test_skips_when_open_positions_exist():
    strategy = BearPutSpread(params={"dte": 30, "width": 10})
    signal = strategy.evaluate(_snap(), open_positions=[{"id": 1}])
    assert signal is None


# ---------------------------------------------------------------------------
# Additional: signal max_loss is at least width * 100
# ---------------------------------------------------------------------------

def test_signal_max_loss_at_least_width_times_100():
    strategy = BearPutSpread(params={
        "dte": 30,
        "long_delta": 0.42,
        "width": 10,
    })
    signal = strategy.evaluate(_snap(), open_positions=[])
    assert signal is not None
    assert signal.max_loss_per_contract >= 10 * 100
