"""
Put credit spread strategy — sell a nearer-dated OTM put, buy a further OTM
put as the long wing for defined risk.

Parameters:
  - dte: target days-to-expiry for the short leg
  - short_delta: target absolute delta for the short leg (e.g., 0.25 = 25-delta put)
  - width: strike distance between short and long legs in dollars
  - iv_rank_min: minimum IV rank (0-100) required to open
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from bullbot.data.schemas import Leg, Signal
from bullbot.strategies.base import Strategy, StrategySnapshot
from bullbot.features.greeks import compute_greeks
from bullbot import config


class PutCreditSpread(Strategy):
    CLASS_NAME = "PutCreditSpread"
    CLASS_VERSION = 1

    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Signal | None:
        if any(p for p in open_positions if p):
            return None

        iv_rank_min = float(self.params.get("iv_rank_min", 50))
        if snapshot.iv_rank < iv_rank_min:
            return None

        target_dte = int(self.params.get("dte", 14))
        short_delta = float(self.params.get("short_delta", 0.25))
        width = float(self.params.get("width", 5))

        asof_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc).date()
        target_exp = asof_dt + timedelta(days=target_dte)
        candidates_p = [
            c for c in snapshot.chain
            if c.kind == "P"
            and abs((datetime.strptime(c.expiry, "%Y-%m-%d").date() - target_exp).days) <= 3
        ]
        if not candidates_p:
            return None

        by_exp: dict[str, list] = {}
        for c in candidates_p:
            by_exp.setdefault(c.expiry, []).append(c)
        chosen_expiry = min(
            by_exp.keys(),
            key=lambda e: abs(
                (datetime.strptime(e, "%Y-%m-%d").date() - target_exp).days
            ),
        )
        expiry_puts = by_exp[chosen_expiry]

        t_years = (
            (datetime.strptime(chosen_expiry, "%Y-%m-%d").date() - asof_dt).days / 365.0
        )
        if t_years <= 0:
            return None

        best = None
        best_gap = float("inf")
        for p in expiry_puts:
            if p.iv is None or p.iv <= 0:
                continue
            g = compute_greeks(
                spot=snapshot.spot,
                strike=p.strike,
                t_years=t_years,
                r=config.RISK_FREE_RATE,
                sigma=p.iv,
                is_put=True,
            )
            gap = abs(g.delta - (-short_delta))
            if gap < best_gap:
                best_gap = gap
                best = p
        if best is None:
            return None

        long_strike = best.strike - width
        long_leg = next(
            (p for p in expiry_puts if abs(p.strike - long_strike) < 0.01),
            None,
        )
        if long_leg is None:
            return None

        short_option = _make_osi(snapshot.ticker, chosen_expiry, best.strike, "P")
        long_option = _make_osi(snapshot.ticker, chosen_expiry, long_leg.strike, "P")

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[
                Leg(
                    option_symbol=short_option,
                    side="short",
                    quantity=1,
                    strike=best.strike,
                    expiry=chosen_expiry,
                    kind="P",
                ),
                Leg(
                    option_symbol=long_option,
                    side="long",
                    quantity=1,
                    strike=long_leg.strike,
                    expiry=chosen_expiry,
                    kind="P",
                ),
            ],
            max_loss_per_contract=width * 100,
            rationale=(
                f"Short {best.strike}P / Long {long_leg.strike}P {chosen_expiry} "
                f"(width={width}, iv_rank={snapshot.iv_rank:.0f})"
            ),
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=int(self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE)),
        )

    def max_loss_per_contract(self) -> float:
        width = float(self.params.get("width", 5))
        return width * 100


def _make_osi(ticker: str, expiry: str, strike: float, kind: str) -> str:
    from datetime import datetime as _dt
    d = _dt.strptime(expiry, "%Y-%m-%d").date()
    return f"{ticker}{d:%y%m%d}{kind}{int(round(strike * 1000)):08d}"
