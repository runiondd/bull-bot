"""Position sizer — contract count from equity, risk budget, and capital pool."""
from __future__ import annotations

from bullbot import config

_GROWTH_FRAC = {
    "bull": config.GROWTH_FRAC_BULL,
    "chop": config.GROWTH_FRAC_CHOP,
    "bear": config.GROWTH_FRAC_BEAR,
}


def size_position(
    equity: float,
    max_loss_per_contract: float,
    category: str = "income",
    regime: str = "bull",
) -> int:
    """Return the contract count for this position, or 0 if it can't be sized."""
    if max_loss_per_contract <= 0:
        return 0

    growth_frac = _GROWTH_FRAC.get(regime, config.GROWTH_FRAC_CHOP)
    if category == "growth":
        pool = equity * growth_frac
    else:
        pool = equity * (1.0 - growth_frac)

    risk_budget = config.POSITION_RISK_FRAC * pool
    raw = int(risk_budget // max_loss_per_contract)
    return max(0, min(raw, config.MAX_POSITIONS_PER_TICKER))
