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


_DTE_TARGETS = [30, 60, 90, 180, 270, 365]


def _strike_step(spot: float) -> float:
    if spot < 50:
        return 2.50
    if spot <= 200:
        return 5.0
    return 10.0


def generate_synthetic_chain(
    ticker: str,
    spot: float,
    cursor: int,
    bars: list[Bar],
    risk_free_rate: float = config.RISK_FREE_RATE,
) -> list[OptionContract]:
    """Generate a synthetic option chain using Black-Scholes and realized vol."""
    vol = realized_vol(bars)
    step = _strike_step(spot)

    low_strike = math.floor(spot * 0.60 / step) * step
    high_strike = math.ceil(spot * 1.40 / step) * step
    strikes = []
    s = low_strike
    while s <= high_strike:
        strikes.append(round(s, 2))
        s += step

    expiries: list[tuple[str, float]] = []
    for dte in _DTE_TARGETS:
        exp_ts = cursor + dte * 86400
        exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
        expiry_str = exp_dt.strftime("%Y-%m-%d")
        t_years = dte / 365.0
        expiries.append((expiry_str, t_years))

    contracts: list[OptionContract] = []
    for expiry_str, t_years in expiries:
        for strike in strikes:
            for kind in ("C", "P"):
                price = bs_price(spot, strike, t_years, vol, risk_free_rate, kind)
                bid = max(0.01, round(price * 0.95, 2))
                ask = round(max(price * 1.05, bid + 0.01), 2)
                contracts.append(OptionContract(
                    ticker=ticker,
                    expiry=expiry_str,
                    strike=strike,
                    kind=kind,
                    ts=cursor,
                    nbbo_bid=bid,
                    nbbo_ask=ask,
                    volume=100,
                    open_interest=1000,
                    iv=round(vol, 4),
                ))
    return contracts
