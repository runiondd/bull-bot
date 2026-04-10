"""Pydantic schema tests — every model roundtrips and rejects bad input."""
import pytest
from pydantic import ValidationError

from bullbot.data.schemas import (
    Bar,
    OptionContract,
    IVSurfacePoint,
    Greeks,
    Signal,
    Leg,
)


def test_bar_roundtrip():
    b = Bar(
        ticker="SPY",
        timeframe="1d",
        ts=1718395200,
        open=540.0, high=542.5, low=539.1, close=541.8,
        volume=1_234_567,
        source="uw",
    )
    assert b.ticker == "SPY"
    assert b.close == 541.8


def test_bar_rejects_negative_price():
    with pytest.raises(ValidationError):
        Bar(ticker="SPY", timeframe="1d", ts=1, open=-1.0, high=1, low=1, close=1, volume=1, source="uw")


def test_bar_rejects_unknown_source():
    with pytest.raises(ValidationError):
        Bar(ticker="SPY", timeframe="1d", ts=1, open=1, high=1, low=1, close=1, volume=1, source="robinhood")


def test_option_contract_roundtrip():
    c = OptionContract(
        ticker="SPY",
        expiry="2024-06-21",
        strike=540.0,
        kind="P",
        ts=1718395200,
        nbbo_bid=1.20,
        nbbo_ask=1.25,
        last=1.22,
        volume=5_000,
        open_interest=15_000,
        iv=0.143,
    )
    assert c.kind == "P"
    assert c.nbbo_bid == 1.20


def test_option_contract_rejects_invalid_kind():
    with pytest.raises(ValidationError):
        OptionContract(
            ticker="SPY", expiry="2024-06-21", strike=540, kind="X",
            ts=1, nbbo_bid=1, nbbo_ask=1, last=1, volume=1, open_interest=1, iv=0.1,
        )


def test_iv_surface_point():
    p = IVSurfacePoint(
        ticker="SPY",
        ts=1718395200,
        iv_rank=38.0,
        iv_percentile=42.0,
        atm_iv=0.143,
        implied_move=0.018,
    )
    assert p.iv_rank == 38.0


def test_greeks_model():
    g = Greeks(delta=0.52, gamma=0.005, theta=-0.31, vega=0.44, iv=0.143)
    assert abs(g.delta - 0.52) < 1e-9


def test_leg_and_signal():
    leg = Leg(
        option_symbol="SPY240621P00540000",
        side="short",
        quantity=1,
        strike=540.0,
        expiry="2024-06-21",
        kind="P",
    )
    signal = Signal(
        intent="open",
        strategy_class="PutCreditSpread",
        legs=[leg, Leg(option_symbol="SPY240621P00535000", side="long",
                       quantity=1, strike=535.0, expiry="2024-06-21", kind="P")],
        max_loss_per_contract=500.0,
        rationale="Short put credit spread at 25d short, 5-wide",
    )
    assert signal.intent == "open"
    assert len(signal.legs) == 2
    assert signal.max_loss_per_contract == 500.0


def test_signal_rejects_invalid_intent():
    with pytest.raises(ValidationError):
        Signal(
            intent="bogus",
            strategy_class="PutCreditSpread",
            legs=[],
            max_loss_per_contract=100.0,
            rationale="test",
        )
