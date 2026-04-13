"""Synthetic option chain generator using Black-Scholes and realized volatility."""
from __future__ import annotations

import calendar
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


def _third_friday(year: int, month: int) -> datetime:
    """Return the 3rd Friday of the given year/month as a UTC datetime."""
    # First day of the month
    first_day_weekday = calendar.weekday(year, month, 1)  # Mon=0 .. Sun=6
    # Friday is weekday 4
    first_friday = 1 + (4 - first_day_weekday) % 7
    third_friday_day = first_friday + 14
    return datetime(year, month, third_friday_day, tzinfo=timezone.utc)


def _monthly_expiries(cursor: int, max_dte: int = 400) -> list[tuple[str, float, int]]:
    """Return standard monthly option expiry dates from cursor up to max_dte days out.

    Each tuple is (expiry_str "YYYY-MM-DD", t_years, dte_days).
    """
    cursor_dt = datetime.fromtimestamp(cursor, tz=timezone.utc)
    result: list[tuple[str, float, int]] = []

    year = cursor_dt.year
    month = cursor_dt.month

    for _ in range(18):  # up to 18 months out covers 400+ DTE
        tf = _third_friday(year, month)
        dte_days = (tf - cursor_dt).days
        if dte_days > 0:  # only future expiries
            if dte_days > max_dte:
                break
            t_years = dte_days / 365.0
            expiry_str = tf.strftime("%Y-%m-%d")
            result.append((expiry_str, t_years, dte_days))
        # advance month
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    return result


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

    # Use standard monthly expiries (3rd Friday) instead of cursor-relative dates
    all_monthly = _monthly_expiries(cursor, max_dte=400)
    seen: set[str] = set()
    expiries: list[tuple[str, float]] = []
    for target_dte in _DTE_TARGETS:
        best = min(all_monthly, key=lambda m: abs(m[2] - target_dte))
        if best[0] not in seen:
            seen.add(best[0])
            expiries.append((best[0], best[1]))

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
