"""GrowthLEAPS — buy long-dated calls for directional growth exposure."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bullbot import config
from bullbot.data.schemas import Leg, Signal
from bullbot.data.synthetic_chain import bs_delta, realized_vol
from bullbot.strategies.base import Strategy, StrategySnapshot


class GrowthLEAPS(Strategy):
    CLASS_NAME = "GrowthLEAPS"
    CLASS_VERSION = 1

    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
    ) -> Signal | None:
        if open_positions:
            return None

        regime_filter = self.params.get("regime_filter")
        if regime_filter and snapshot.regime not in regime_filter:
            return None

        iv_rank_max = self.params.get("iv_rank_max", 100)
        if snapshot.iv_rank > iv_rank_max:
            return None

        target_delta = self.params.get("target_delta", 0.70)
        min_dte = self.params.get("min_dte", 180)
        max_dte = self.params.get("max_dte", 365)
        now_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc)

        best = None
        best_delta_diff = float("inf")

        for c in snapshot.chain:
            if c.kind != "C":
                continue
            exp_dt = datetime.strptime(c.expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            dte = (exp_dt - now_dt).days
            if dte < min_dte or dte > max_dte:
                continue
            if c.nbbo_bid <= 0 or c.nbbo_ask <= 0:
                continue

            vol = c.iv if c.iv else realized_vol(snapshot.bars_1d)
            est_delta = bs_delta(snapshot.spot, c.strike, dte / 365.0, vol, config.RISK_FREE_RATE, "C")
            delta_diff = abs(est_delta - target_delta)
            if delta_diff < best_delta_diff:
                best_delta_diff = delta_diff
                best = c

        if best is None:
            return None

        exp_d = datetime.strptime(best.expiry, "%Y-%m-%d").date()
        osi = f"{best.ticker}{exp_d:%y%m%d}C{int(round(best.strike * 1000)):08d}"

        leg = Leg(
            option_symbol=osi, side="long", quantity=1,
            strike=best.strike, expiry=best.expiry, kind="C",
        )

        mid = (best.nbbo_bid + best.nbbo_ask) / 2.0
        max_loss = mid * 100

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[leg],
            max_loss_per_contract=max_loss,
            rationale=f"LEAPS call {best.strike}C exp {best.expiry}, est delta ~{target_delta:.2f}",
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE),
        )

    def max_loss_per_contract(self) -> float:
        return 5000.0
