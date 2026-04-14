"""CoveredCallOverlay — sell short-dated OTM calls against long LEAPS and shares."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from bullbot import config
from bullbot.data.long_inventory import total_coverable_contracts
from bullbot.data.schemas import Leg, Signal
from bullbot.data.synthetic_chain import bs_delta, realized_vol
from bullbot.strategies.base import Strategy, StrategySnapshot


def _make_osi(ticker: str, expiry: str, strike: float, kind: str) -> str:
    d = datetime.strptime(expiry, "%Y-%m-%d").date()
    return f"{ticker}{d:%y%m%d}{kind}{int(round(strike * 1000)):08d}"


class CoveredCallOverlay(Strategy):
    CLASS_NAME = "CoveredCallOverlay"
    CLASS_VERSION = 1

    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Signal | None:
        conn = kwargs.get("conn")
        if conn is None:
            return None

        # 1. How many contracts can we cover?
        coverable = total_coverable_contracts(conn, snapshot.ticker)
        if coverable <= 0:
            return None

        # 2. Count existing open short call positions for this ticker
        existing = 0
        for pos in open_positions:
            legs_raw = pos.get("legs", "[]")
            if isinstance(legs_raw, str):
                try:
                    legs = json.loads(legs_raw)
                except (json.JSONDecodeError, TypeError):
                    legs = []
            else:
                legs = legs_raw
            for leg in legs:
                if (
                    leg.get("side") == "short"
                    and leg.get("kind") == "C"
                ):
                    existing += pos.get("contracts", 1)
                    break

        # 3. Apply coverage_ratio
        coverage_ratio = self.params.get("coverage_ratio", 0.50)
        max_short = math.floor(coverable * coverage_ratio)
        if existing >= max_short:
            return None

        # 4. Timing filters
        rsi = snapshot.indicators.get("rsi_14", 0)
        if rsi < self.params.get("min_rsi", 50):
            return None

        bars = snapshot.bars_1d
        if len(bars) >= 2:
            prev_close = bars[-2].close
            day_return = (snapshot.spot - prev_close) / prev_close if prev_close > 0 else 0.0
        else:
            day_return = 0.0

        if day_return < self.params.get("min_day_return", 0.01):
            return None

        if snapshot.iv_rank < self.params.get("iv_rank_min", 30):
            return None

        # 5. Find best OTM call
        short_delta_target = self.params.get("short_delta", 0.20)
        dte_min = self.params.get("dte_min", 20)
        dte_max = self.params.get("dte_max", 60)
        now_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc)

        best = None
        best_delta_diff = float("inf")

        for c in snapshot.chain:
            if c.kind != "C":
                continue
            if c.strike <= snapshot.spot:
                continue
            if c.nbbo_bid <= 0 or c.nbbo_ask <= 0:
                continue

            exp_dt = datetime.strptime(c.expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            dte = (exp_dt - now_dt).days
            if dte < dte_min or dte > dte_max:
                continue

            vol = c.iv if c.iv else realized_vol(snapshot.bars_1d)
            est_delta = bs_delta(
                snapshot.spot, c.strike, dte / 365.0, vol,
                config.RISK_FREE_RATE, "C",
            )
            delta_diff = abs(est_delta - short_delta_target)
            if delta_diff < best_delta_diff:
                best_delta_diff = delta_diff
                best = c

        if best is None:
            return None

        # 6. Build Signal
        osi = _make_osi(snapshot.ticker, best.expiry, best.strike, "C")
        mid = (best.nbbo_bid + best.nbbo_ask) / 2.0

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[Leg(
                option_symbol=osi,
                side="short",
                quantity=1,
                strike=best.strike,
                expiry=best.expiry,
                kind="C",
            )],
            max_loss_per_contract=mid * 100,
            rationale=(
                f"Selling {best.strike}C exp {best.expiry} against LEAPS inventory. "
                f"Delta ~{short_delta_target:.2f}, RSI {rsi:.0f}, day return {day_return:.1%}."
            ),
            profit_target_pct=self.params.get("profit_target_pct", 0.50),
            stop_loss_mult=self.params.get("roll_itm_delta", 0.70),
            min_dte_close=self.params.get("roll_dte", 5),
        )

    def max_loss_per_contract(self) -> float:
        return 2000.0
