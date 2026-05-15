"""Per-trade dollar budget derived from account size and risk tolerance.

Centralizes the "how much can I lose on one trade" calculation so the
proposer, sweep, and sizing paths all read the same number, and so the
bot scales naturally as Dan raises capital or risk tolerance.
"""
from __future__ import annotations

from bullbot import config


def per_trade_budget_usd(category: str, max_loss_pct: float = 0.02) -> float:
    """Return the dollar ceiling for a single trade's worst-case loss.

    `category` selects the account: "growth" uses GROWTH_CAPITAL_USD,
    anything else (including unknown) uses INITIAL_CAPITAL_USD.
    `max_loss_pct` is the portfolio fraction allowed per trade (default 2%).
    """
    if category == "growth":
        portfolio = config.GROWTH_CAPITAL_USD
    else:
        portfolio = config.INITIAL_CAPITAL_USD
    return float(portfolio) * float(max_loss_pct)
