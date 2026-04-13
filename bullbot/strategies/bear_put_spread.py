"""BearPutSpread — defined-risk bearish debit spread."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bullbot import config
from bullbot.data.schemas import Leg, Signal
from bullbot.strategies.base import Strategy, StrategySnapshot


class BearPutSpread(Strategy):
    CLASS_NAME = "BearPutSpread"
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

        iv_rank_min = self.params.get("iv_rank_min", 0)
        if snapshot.iv_rank < iv_rank_min:
            return None

        target_dte = self.params.get("dte", 30)
        long_delta = self.params.get("long_delta", 0.40)
        width = self.params.get("width", 10)
        now_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc)

        puts = [c for c in snapshot.chain if c.kind == "P" and c.nbbo_bid > 0 and c.nbbo_ask > 0]
        if not puts:
            return None

        # Find best expiry near target DTE
        best_expiry = None
        best_dte_diff = float("inf")
        for c in puts:
            exp_dt = datetime.strptime(c.expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            dte = (exp_dt - now_dt).days
            if dte < 7:
                continue
            diff = abs(dte - target_dte)
            if diff < best_dte_diff:
                best_dte_diff = diff
                best_expiry = c.expiry

        if best_expiry is None:
            return None

        expiry_puts = sorted(
            [c for c in puts if c.expiry == best_expiry],
            key=lambda c: c.strike,
        )
        if len(expiry_puts) < 2:
            return None

        # Find long put near target delta
        best_long = None
        best_diff = float("inf")
        for c in expiry_puts:
            est_delta = max(0.01, min(0.99, (c.strike - snapshot.spot) / (2 * snapshot.spot) + 0.50))
            diff = abs(est_delta - long_delta)
            if diff < best_diff:
                best_diff = diff
                best_long = c

        if best_long is None:
            return None

        # Find short put near long_strike - width (nearest available strike at or below target)
        target_short = best_long.strike - width
        short_put = None
        best_short_diff = float("inf")
        for c in expiry_puts:
            if c.strike >= best_long.strike:
                continue
            diff = abs(c.strike - target_short)
            if diff < best_short_diff:
                best_short_diff = diff
                short_put = c

        if short_put is None:
            return None

        exp_d = datetime.strptime(best_expiry, "%Y-%m-%d").date()

        def osi(strike, kind):
            return f"{snapshot.ticker}{exp_d:%y%m%d}{kind}{int(round(strike * 1000)):08d}"

        long_leg = Leg(
            option_symbol=osi(best_long.strike, "P"), side="long", quantity=1,
            strike=best_long.strike, expiry=best_expiry, kind="P",
        )
        short_leg = Leg(
            option_symbol=osi(short_put.strike, "P"), side="short", quantity=1,
            strike=short_put.strike, expiry=best_expiry, kind="P",
        )

        net_debit = ((best_long.nbbo_ask + best_long.nbbo_bid) / 2
                     - (short_put.nbbo_bid + short_put.nbbo_ask) / 2)
        max_loss = net_debit * 100

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[long_leg, short_leg],
            max_loss_per_contract=max(max_loss, width * 100),
            rationale=f"Bear put spread {best_long.strike}/{short_put.strike}P exp {best_expiry}",
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE),
        )

    def max_loss_per_contract(self) -> float:
        return self.params.get("width", 10) * 100
