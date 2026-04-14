"""
Cash-secured put strategy — sell a naked OTM put, secured by cash held
in the account. Generates income in neutral-to-bullish markets.

Parameters:
  - dte: target days-to-expiry
  - target_delta: target absolute delta for the short put (e.g., 0.30)
  - iv_rank_min: minimum IV rank (0-100) required to open
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from bullbot.data.schemas import Leg, Signal
from bullbot.strategies.base import Strategy, StrategySnapshot
from bullbot import config
from bullbot.strategies.iron_condor import _pick_by_delta
from bullbot.strategies.put_credit_spread import _make_osi


class CashSecuredPut(Strategy):
    CLASS_NAME = "CashSecuredPut"
    CLASS_VERSION = 1

    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Signal | None:
        if any(p for p in open_positions if p):
            return None

        iv_rank_min = float(self.params.get("iv_rank_min", 40))
        if snapshot.iv_rank < iv_rank_min:
            return None

        target_dte = int(self.params.get("dte", 30))
        target_delta = float(self.params.get("target_delta", 0.30))

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
                    side="short",
                    quantity=1,
                    strike=best.strike,
                    expiry=chosen_expiry,
                    kind="P",
                ),
            ],
            max_loss_per_contract=self.max_loss_per_contract(),
            rationale=(
                f"CSP short {best.strike}P {chosen_expiry} "
                f"(delta~{target_delta}, iv_rank={snapshot.iv_rank:.0f})"
            ),
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=int(self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE)),
        )

    def max_loss_per_contract(self) -> float:
        # Conservative placeholder: max loss = strike * 100, approximate 50 * 100
        return 5000.0
