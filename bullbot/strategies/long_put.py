"""
Long put strategy — buy a directional ITM/ATM put for bearish exposure or
portfolio hedging. No IV rank gate.

Parameters:
  - dte: target days-to-expiry
  - delta: target absolute delta for the long put (e.g., 0.60 = moderately ITM)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from bullbot.data.schemas import Leg, Signal
from bullbot.strategies.base import Strategy, StrategySnapshot
from bullbot.strategies.iron_condor import _pick_by_delta
from bullbot.strategies.put_credit_spread import _make_osi


class LongPut(Strategy):
    CLASS_NAME = "LongPut"
    CLASS_VERSION = 1

    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
    ) -> Signal | None:
        if any(p for p in open_positions if p):
            return None

        target_dte = int(self.params.get("dte", 45))
        target_delta = float(self.params.get("delta", 0.60))

        asof_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc).date()
        target_exp = asof_dt + timedelta(days=target_dte)

        candidates_p = [
            c for c in snapshot.chain
            if c.kind == "P"
            and abs((datetime.strptime(c.expiry, "%Y-%m-%d").date() - target_exp).days) <= 5
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

        best = _pick_by_delta(expiry_puts, snapshot.spot, t_years, True, target_delta)
        if best is None:
            return None

        option_symbol = _make_osi(snapshot.ticker, chosen_expiry, best.strike, "P")

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[
                Leg(
                    option_symbol=option_symbol,
                    side="long",
                    quantity=1,
                    strike=best.strike,
                    expiry=chosen_expiry,
                    kind="P",
                ),
            ],
            max_loss_per_contract=self.max_loss_per_contract(),
            rationale=(
                f"Long {best.strike}P {chosen_expiry} "
                f"(delta~{target_delta}, regime={snapshot.regime})"
            ),
        )

    def max_loss_per_contract(self) -> float:
        # Estimate: premium paid, approximately $10/contract
        return 1000.0
