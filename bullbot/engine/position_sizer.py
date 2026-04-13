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
    run_id: str = "",
) -> int:
    """Return the contract count for this position, or 0 if it can't be sized.

    During backtesting (run_id starts with "bt:"), uses full equity with flat
    risk fraction so the evolver can discover edge without regime-driven
    allocation constraints. Regime-based pool sizing applies to paper/live only.
    """
    if max_loss_per_contract <= 0:
        return 0

    if run_id.startswith("bt:"):
        pool = equity
    else:
        growth_frac = _GROWTH_FRAC.get(regime, config.GROWTH_FRAC_CHOP)
        if category == "growth":
            pool = equity * growth_frac
        else:
            pool = equity * (1.0 - growth_frac)

    risk_budget = config.POSITION_RISK_FRAC * pool
    raw = int(risk_budget // max_loss_per_contract)
    if raw == 0 and category == "growth" and max_loss_per_contract <= pool * 0.50:
        raw = 1
    return max(0, min(raw, config.MAX_POSITIONS_PER_TICKER))
