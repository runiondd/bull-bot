"""Fill model tests — mid ± half-spread ± one tick slippage, commissions."""
import pytest

from bullbot.data.schemas import Leg
from bullbot.engine import fill_model


def _chain_row(bid: float, ask: float) -> dict:
    return {"nbbo_bid": bid, "nbbo_ask": ask, "last": (bid + ask) / 2}


def test_short_leg_sells_below_mid():
    bid, ask = 1.20, 1.30
    fill = fill_model.simulate_leg_open(
        leg=Leg(option_symbol="X", side="short", quantity=1,
                strike=500, expiry="2024-01-01", kind="P"),
        chain_row=_chain_row(bid, ask),
    )
    mid = (bid + ask) / 2
    assert fill < mid
    assert abs(fill - (mid - 0.01)) < 1e-9


def test_long_leg_pays_above_mid():
    bid, ask = 1.20, 1.30
    fill = fill_model.simulate_leg_open(
        leg=Leg(option_symbol="X", side="long", quantity=1,
                strike=500, expiry="2024-01-01", kind="P"),
        chain_row=_chain_row(bid, ask),
    )
    mid = (bid + ask) / 2
    assert fill > mid
    assert abs(fill - (mid + 0.01)) < 1e-9


def test_rejects_zero_bid():
    with pytest.raises(fill_model.FillRejected):
        fill_model.simulate_leg_open(
            leg=Leg(option_symbol="X", side="short", quantity=1,
                    strike=500, expiry="2024-01-01", kind="P"),
            chain_row=_chain_row(0.0, 1.30),
        )


def test_rejects_wide_spread_beyond_cap():
    with pytest.raises(fill_model.FillRejected):
        fill_model.simulate_leg_open(
            leg=Leg(option_symbol="X", side="short", quantity=1,
                    strike=500, expiry="2024-01-01", kind="P"),
            chain_row=_chain_row(0.80, 1.60),
        )


def test_accepts_spread_below_cap():
    fill_model.simulate_leg_open(
        leg=Leg(option_symbol="X", side="short", quantity=1,
                strike=500, expiry="2024-01-01", kind="P"),
        chain_row=_chain_row(1.10, 1.30),
    )


def test_commission_scales_with_legs_and_contracts():
    cost = fill_model.commission(contracts=3, n_legs=4)
    assert cost == pytest.approx(3 * 4 * 0.65)


def test_net_open_credit_credit_spread():
    legs = [
        Leg(option_symbol="A", side="short", quantity=1, strike=540, expiry="2024-01-01", kind="P"),
        Leg(option_symbol="B", side="long", quantity=1, strike=535, expiry="2024-01-01", kind="P"),
    ]
    chain_rows = {
        "A": _chain_row(2.15, 2.25),
        "B": _chain_row(0.95, 1.05),
    }
    net, legs_filled = fill_model.simulate_open_multi_leg(legs, chain_rows, contracts=1)
    # Short leg fills at mid (2.20) - 0.01 = 2.19 (received)
    # Long leg fills at mid (1.00) + 0.01 = 1.01 (paid)
    # Net per share = 1.01 - 2.19 = -1.18, times 100 = -118.0
    assert abs(net - (-118.0)) < 1e-9    # negative = credit received
