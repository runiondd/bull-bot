"""PutCreditSpread strategy tests."""
from datetime import date, datetime, timedelta, timezone

from bullbot.data.schemas import OptionContract
from bullbot.strategies.base import StrategySnapshot
from bullbot.strategies.put_credit_spread import PutCreditSpread


def _chain_puts(expiry: str, strikes_with_delta: list[tuple[float, float]]) -> list[OptionContract]:
    """Build a list of put OptionContracts for a given expiry."""
    out = []
    for strike, _ in strikes_with_delta:
        out.append(
            OptionContract(
                ticker="SPY",
                expiry=expiry,
                strike=strike,
                kind="P",
                ts=1718395200,
                nbbo_bid=1.20,
                nbbo_ask=1.30,
                iv=0.15,
                volume=1000,
                open_interest=5000,
            )
        )
    return out


def test_evaluate_opens_when_conditions_met():
    snap = StrategySnapshot(
        ticker="SPY",
        asof_ts=1718395200,
        spot=582.0,
        bars_1d=[],
        indicators={"rsi_14": 55.0},
        atm_greeks={"delta": 0.50},
        iv_rank=60.0,  # above iv_rank_min=50
        regime="bull",
        chain=_chain_puts("2024-06-28", [(570, -0.25), (565, -0.20)]),
    )
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50
    })
    signal = strategy.evaluate(snap, open_positions=[])
    assert signal is not None
    assert signal.intent == "open"
    assert signal.strategy_class == "PutCreditSpread"
    assert len(signal.legs) == 2


def test_evaluate_returns_none_when_iv_rank_below_min():
    snap = StrategySnapshot(
        ticker="SPY",
        asof_ts=1718395200,
        spot=582.0,
        bars_1d=[],
        indicators={},
        atm_greeks={},
        iv_rank=30.0,  # below min 50
        regime="bull",
        chain=_chain_puts("2024-06-28", [(570, -0.25)]),
    )
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50
    })
    assert strategy.evaluate(snap, []) is None


def test_max_loss_equals_width_minus_credit():
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50
    })
    assert strategy.max_loss_per_contract() == 500.0


def test_does_not_open_if_already_have_position():
    snap = StrategySnapshot(
        ticker="SPY", asof_ts=1718395200, spot=582.0,
        bars_1d=[], indicators={}, atm_greeks={}, iv_rank=60.0, regime="bull",
        chain=_chain_puts("2024-06-28", [(570, -0.25)]),
    )
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50
    })
    open_positions = [{"id": 1, "strategy_id": 42}]
    assert strategy.evaluate(snap, open_positions) is None
