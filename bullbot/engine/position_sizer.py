"""Position sizer — contract count from equity, risk budget, and capital pool.

Income and growth strategies size against separate accounts:
  - Income: INITIAL_CAPITAL_USD ($50k taxable)
  - Growth: GROWTH_CAPITAL_USD ($215k tax-sheltered)

During backtesting, full account equity is used so the evolver can discover
edge without regime constraints.  For paper/live growth trades, a regime-based
utilization factor scales down exposure in chop/bear markets.
"""
from __future__ import annotations

from bullbot import config

_GROWTH_REGIME_UTIL = {
    "bull": 1.00,
    "chop": 0.50,
    "bear": 0.25,
}


def size_position(
    equity: float,
    max_loss_per_contract: float,
    category: str = "income",
    regime: str = "bull",
    run_id: str = "",
) -> int:
    """Return the contract count for this position, or 0 if it can't be sized."""
    if max_loss_per_contract <= 0:
        return 0

    if run_id.startswith("bt:"):
        pool = equity
    elif category == "growth":
        util = _GROWTH_REGIME_UTIL.get(regime, 0.50)
        pool = equity * util
    else:
        pool = equity

    risk_budget = config.POSITION_RISK_FRAC * pool
    raw = int(risk_budget // max_loss_per_contract)
    if raw == 0 and category == "growth" and max_loss_per_contract <= pool * 0.50:
        raw = 1
    return max(0, min(raw, config.MAX_POSITIONS_PER_TICKER))
