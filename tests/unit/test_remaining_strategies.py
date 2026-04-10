"""Smoke tests for the 5 remaining seed strategies — each must construct,
return CLASS_NAME, compute max_loss_per_contract, and evaluate to either a
Signal or None without raising on a well-formed snapshot."""
from datetime import datetime, timedelta, timezone

import pytest

from bullbot.data.schemas import OptionContract
from bullbot.strategies.base import StrategySnapshot
from bullbot.strategies.call_credit_spread import CallCreditSpread
from bullbot.strategies.iron_condor import IronCondor
from bullbot.strategies.cash_secured_put import CashSecuredPut
from bullbot.strategies.long_call import LongCall
from bullbot.strategies.long_put import LongPut


def _snap(iv_rank: float = 60.0, regime: str = "bull") -> StrategySnapshot:
    ts = int(datetime(2024, 6, 14, 14, 0, tzinfo=timezone.utc).timestamp())
    expiry_dt = (datetime(2024, 6, 14) + timedelta(days=21)).strftime("%Y-%m-%d")
    chain = []
    for strike in [560, 565, 570, 575, 580, 585, 590, 595, 600]:
        for kind in ("P", "C"):
            chain.append(OptionContract(
                ticker="SPY", expiry=expiry_dt, strike=strike, kind=kind,
                ts=ts, nbbo_bid=1.20, nbbo_ask=1.30, iv=0.18, volume=1000, open_interest=5000,
            ))
    return StrategySnapshot(
        ticker="SPY", asof_ts=ts, spot=580.0, bars_1d=[],
        indicators={"rsi_14": 55.0}, atm_greeks={"delta": 0.5},
        iv_rank=iv_rank, regime=regime, chain=chain,
    )


@pytest.mark.parametrize("cls,params", [
    (CallCreditSpread, {"dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50}),
    (IronCondor, {"dte": 21, "wing_delta": 0.20, "wing_width": 5, "iv_rank_min": 60}),
    (CashSecuredPut, {"dte": 30, "target_delta": 0.30, "iv_rank_min": 40}),
    (LongCall, {"dte": 45, "delta": 0.60}),
    (LongPut, {"dte": 45, "delta": 0.60}),
])
def test_strategy_smoke(cls, params):
    s = cls(params=params)
    assert s.CLASS_NAME == cls.__name__
    assert s.max_loss_per_contract() > 0
    result = s.evaluate(_snap(), [])
    assert result is None or result.intent == "open"
