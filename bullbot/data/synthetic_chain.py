"""Synthetic option chain generator using Black-Scholes and realized volatility."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from bullbot import config
from bullbot.data.schemas import Bar, OptionContract


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def realized_vol(bars: list[Bar], window: int = 30) -> float:
    """Annualized realized volatility from daily log returns.

    Returns 0.30 as a default if fewer than window+1 bars are available.
    """
    if len(bars) < window + 1:
        return 0.30
    closes = [b.close for b in bars[-(window + 1):]]
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(log_returns) < 2:
        return 0.30
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def bs_price(
    spot: float, strike: float, t_years: float, vol: float, r: float, kind: str,
) -> float:
    """Black-Scholes European option price.

    kind: "C" for call, "P" for put.
    Returns intrinsic value when t_years <= 0.
    """
    if t_years <= 0:
        if kind == "C":
            return max(0.0, spot - strike)
        return max(0.0, strike - spot)
    if vol <= 0:
        df = math.exp(-r * t_years)
        if kind == "C":
            return max(0.0, spot - strike * df)
        return max(0.0, strike * df - spot)

    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * t_years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t

    if kind == "C":
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    return strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
