"""
Market regime classifier.

Pinned algorithm (spec §6.7): rolling 60-day return + rolling 30-day
annualized volatility. Thresholds live in bullbot.config.
"""

from __future__ import annotations

import math

from bullbot import config


def classify(closes_60d: list[float]) -> str:
    """Classify market regime as 'bull' | 'bear' | 'chop'.

    Requires at least 60 closes. Returns 'chop' on insufficient data
    rather than raising — callers should ensure enough history before
    expecting a meaningful verdict.
    """
    if len(closes_60d) < 60:
        return "chop"

    rolling_60d_return = (closes_60d[-1] - closes_60d[-60]) / closes_60d[-60]

    # 30-day annualized volatility of daily returns
    returns = [
        (closes_60d[i] - closes_60d[i - 1]) / closes_60d[i - 1]
        for i in range(-30, 0)
    ]
    if len(returns) < 2:
        return "chop"
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    ann_vol = math.sqrt(var) * math.sqrt(252)

    if rolling_60d_return >= config.REGIME_BULL_RETURN_MIN and ann_vol < config.REGIME_BULL_VOL_MAX:
        return "bull"
    if rolling_60d_return <= config.REGIME_BEAR_RETURN_MAX:
        return "bear"
    return "chop"
