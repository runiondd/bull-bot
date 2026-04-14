"""GrowthEquity — buy shares for long-term growth."""
from __future__ import annotations

from typing import Any

from bullbot.data.schemas import Leg, Signal
from bullbot.strategies.base import Strategy, StrategySnapshot


class GrowthEquity(Strategy):
    CLASS_NAME = "GrowthEquity"
    CLASS_VERSION = 1

    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Signal | None:
        if open_positions:
            return None

        regime_filter = self.params.get("regime_filter")
        if regime_filter and snapshot.regime not in regime_filter:
            return None

        stop_loss_pct = self.params.get("stop_loss_pct", 0.10)
        max_loss = snapshot.spot * stop_loss_pct

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[],
            max_loss_per_contract=max_loss,
            rationale=f"Buy {snapshot.ticker} shares at {snapshot.spot:.2f}, stop {stop_loss_pct:.0%}",
            profit_target_pct=self.params.get("profit_target_pct"),
            stop_loss_mult=self.params.get("stop_loss_mult"),
            min_dte_close=None,
        )

    def max_loss_per_contract(self) -> float:
        return self.params.get("stop_loss_pct", 0.10) * 250.0 * 100
