"""
Iron condor strategy — short put spread + short call spread simultaneously.
Profits when the underlying stays inside the short strikes at expiry.

Parameters:
  - dte: target days-to-expiry
  - wing_delta: target absolute delta for both short legs (e.g., 0.20)
  - wing_width: strike width for each spread wing in dollars
  - iv_rank_min: minimum IV rank (0-100) required to open
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from bullbot.data.schemas import Leg, OptionContract, Signal
from bullbot.strategies.base import Strategy, StrategySnapshot
from bullbot.features.greeks import compute_greeks
from bullbot import config
from bullbot.strategies.put_credit_spread import _make_osi


def _pick_by_delta(
    chain: list[OptionContract],
    spot: float,
    t_years: float,
    is_put: bool,
    target_abs_delta: float,
) -> OptionContract | None:
    """Return the contract whose absolute delta is closest to target_abs_delta."""
    best = None
    best_gap = float("inf")
    for c in chain:
        if c.iv is None or c.iv <= 0:
            continue
        g = compute_greeks(
            spot=spot,
            strike=c.strike,
            t_years=t_years,
            r=config.RISK_FREE_RATE,
            sigma=c.iv,
            is_put=is_put,
        )
        abs_delta = abs(g.delta)
        gap = abs(abs_delta - target_abs_delta)
        if gap < best_gap:
            best_gap = gap
            best = c
    return best


class IronCondor(Strategy):
    CLASS_NAME = "IronCondor"
    CLASS_VERSION = 1

    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
    ) -> Signal | None:
        if any(p for p in open_positions if p):
            return None

        iv_rank_min = float(self.params.get("iv_rank_min", 60))
        if snapshot.iv_rank < iv_rank_min:
            return None

        target_dte = int(self.params.get("dte", 21))
        wing_delta = float(self.params.get("wing_delta", 0.20))
        wing_width = float(self.params.get("wing_width", 5))

        asof_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc).date()
        target_exp = asof_dt + timedelta(days=target_dte)

        candidates = [
            c for c in snapshot.chain
            if abs((datetime.strptime(c.expiry, "%Y-%m-%d").date() - target_exp).days) <= 3
        ]
        if not candidates:
            return None

        by_exp: dict[str, list] = {}
        for c in candidates:
            by_exp.setdefault(c.expiry, []).append(c)
        chosen_expiry = min(
            by_exp.keys(),
            key=lambda e: abs(
                (datetime.strptime(e, "%Y-%m-%d").date() - target_exp).days
            ),
        )
        expiry_chain = by_exp[chosen_expiry]

        t_years = (
            (datetime.strptime(chosen_expiry, "%Y-%m-%d").date() - asof_dt).days / 365.0
        )
        if t_years <= 0:
            return None

        puts = [c for c in expiry_chain if c.kind == "P"]
        calls = [c for c in expiry_chain if c.kind == "C"]

        short_put = _pick_by_delta(puts, snapshot.spot, t_years, True, wing_delta)
        short_call = _pick_by_delta(calls, snapshot.spot, t_years, False, wing_delta)

        if short_put is None or short_call is None:
            return None

        long_put_strike = short_put.strike - wing_width
        long_call_strike = short_call.strike + wing_width

        long_put = next(
            (p for p in puts if abs(p.strike - long_put_strike) < 0.01), None
        )
        long_call = next(
            (c for c in calls if abs(c.strike - long_call_strike) < 0.01), None
        )
        if long_put is None or long_call is None:
            return None

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[
                Leg(
                    option_symbol=_make_osi(snapshot.ticker, chosen_expiry, short_put.strike, "P"),
                    side="short",
                    quantity=1,
                    strike=short_put.strike,
                    expiry=chosen_expiry,
                    kind="P",
                ),
                Leg(
                    option_symbol=_make_osi(snapshot.ticker, chosen_expiry, long_put.strike, "P"),
                    side="long",
                    quantity=1,
                    strike=long_put.strike,
                    expiry=chosen_expiry,
                    kind="P",
                ),
                Leg(
                    option_symbol=_make_osi(snapshot.ticker, chosen_expiry, short_call.strike, "C"),
                    side="short",
                    quantity=1,
                    strike=short_call.strike,
                    expiry=chosen_expiry,
                    kind="C",
                ),
                Leg(
                    option_symbol=_make_osi(snapshot.ticker, chosen_expiry, long_call.strike, "C"),
                    side="long",
                    quantity=1,
                    strike=long_call.strike,
                    expiry=chosen_expiry,
                    kind="C",
                ),
            ],
            max_loss_per_contract=wing_width * 100,
            rationale=(
                f"IC {chosen_expiry}: "
                f"{long_put.strike}P/{short_put.strike}P/{short_call.strike}C/{long_call.strike}C "
                f"(width={wing_width}, iv_rank={snapshot.iv_rank:.0f})"
            ),
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=int(self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE)),
        )

    def max_loss_per_contract(self) -> float:
        wing_width = float(self.params.get("wing_width", 5))
        return wing_width * 100
