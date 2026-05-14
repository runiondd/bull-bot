"""Position sizing under a portfolio-level max-loss-per-trade gate.

Single source of truth used by the sweep engine, dashboard, and (later) live
execution. The gate is symmetric across options and equity strategies: no
single trade's worst-case loss may exceed `max_loss_pct * portfolio_value`
(default 2%).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor


@dataclass(frozen=True)
class SizingResult:
    size_units: int           # contracts (options) or shares (equity)
    worst_case_loss: float    # dollars; size_units * per-unit-loss
    passes_gate: bool         # True iff size_units > 0
    rationale: str            # one-line explanation


def size_strategy(strategy, portfolio_value: float, max_loss_pct: float = 0.02
                  ) -> SizingResult:
    """Return how many contracts/shares of `strategy` can be deployed so the
    worst-case single-trade loss is <= max_loss_pct * portfolio_value.

    Equity strategies size off `stop_loss_pct * spot`. If `stop_loss_pct` is
    None the function assumes a 100%-of-spot worst case, which sizes the
    position so small it's effectively benched.
    """
    budget = portfolio_value * max_loss_pct  # e.g. $5300 on $265k @ 2%

    if getattr(strategy, "is_equity", False):
        spot = strategy.spot
        stop = strategy.stop_loss_pct
        per_unit_loss = spot * (stop if stop is not None else 1.0)
        if per_unit_loss <= 0:
            return SizingResult(0, 0.0, False, "zero spot or invalid stop")
    else:
        per_unit_loss = strategy.max_loss_per_contract
        if per_unit_loss <= 0:
            return SizingResult(0, per_unit_loss, False, "zero max loss per contract")

    units = floor(budget / per_unit_loss)
    if units <= 0:
        return SizingResult(0, per_unit_loss, False, "smallest unit exceeds budget")

    worst = units * per_unit_loss
    return SizingResult(units, worst, True, f"sized for ${budget:.0f} budget")
