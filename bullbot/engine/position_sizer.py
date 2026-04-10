"""
Position sizer — fixed fraction of current equity at risk per position.

Spec §6.3: max_contracts = floor( (POSITION_RISK_FRAC × equity) /
max_loss_per_contract ), capped at MAX_POSITIONS_PER_TICKER.
"""

from __future__ import annotations

from bullbot import config


def size_position(equity: float, max_loss_per_contract: float) -> int:
    """Return the contract count for this position, or 0 if it can't be sized."""
    if max_loss_per_contract <= 0:
        return 0
    risk_budget = config.POSITION_RISK_FRAC * equity
    raw = int(risk_budget // max_loss_per_contract)
    return max(0, min(raw, config.MAX_POSITIONS_PER_TICKER))
