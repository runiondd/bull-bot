"""
Black-Scholes pricing, analytic greeks, and implied-volatility inversion.

All functions are pure. `compute_greeks` returns a `Greeks` dataclass from
`bullbot.data.schemas`. `implied_volatility` uses scipy's `brentq` to
invert the BS pricing function numerically.
"""

from __future__ import annotations

import math

from scipy.optimize import brentq
from scipy.stats import norm

from bullbot.data.schemas import Greeks


def bs_price(
    spot: float,
    strike: float,
    t_years: float,
    r: float,
    sigma: float,
    is_put: bool,
) -> float:
    """Black-Scholes price (no dividends) for a European call or put."""
    if t_years <= 0 or sigma <= 0:
        if is_put:
            return max(strike - spot, 0.0)
        return max(spot - strike, 0.0)

    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t_years) / (
        sigma * math.sqrt(t_years)
    )
    d2 = d1 - sigma * math.sqrt(t_years)

    if is_put:
        return strike * math.exp(-r * t_years) * norm.cdf(-d2) - spot * norm.cdf(-d1)
    return spot * norm.cdf(d1) - strike * math.exp(-r * t_years) * norm.cdf(d2)


def compute_greeks(
    spot: float,
    strike: float,
    t_years: float,
    r: float,
    sigma: float,
    is_put: bool,
) -> Greeks:
    """Closed-form delta/gamma/theta/vega in Black-Scholes."""
    if t_years <= 0 or sigma <= 0:
        return Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0, iv=sigma)

    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    if is_put:
        delta = norm.cdf(d1) - 1.0
        theta = (
            -spot * norm.pdf(d1) * sigma / (2.0 * sqrt_t)
            + r * strike * math.exp(-r * t_years) * norm.cdf(-d2)
        )
    else:
        delta = norm.cdf(d1)
        theta = (
            -spot * norm.pdf(d1) * sigma / (2.0 * sqrt_t)
            - r * strike * math.exp(-r * t_years) * norm.cdf(d2)
        )

    gamma = norm.pdf(d1) / (spot * sigma * sqrt_t)
    vega = spot * norm.pdf(d1) * sqrt_t / 100.0   # per 1% change in IV

    # Theta returned per calendar day rather than per year
    theta_per_day = theta / 365.0

    return Greeks(delta=delta, gamma=gamma, theta=theta_per_day, vega=vega, iv=sigma)


def implied_volatility(
    mid: float,
    spot: float,
    strike: float,
    t_years: float,
    r: float,
    is_put: bool,
) -> float | None:
    """
    Numerically invert Black-Scholes for implied volatility.

    Returns None if the mid price is outside the arbitrage-free range.
    """
    # Reject zero/negative prices and prices below minimum observable threshold
    # (0.5% of spot) to avoid nonsense IV values on deep OTM options
    if t_years <= 0 or mid <= 0 or mid < 0.005 * spot:
        return None

    if is_put:
        lower_bound = max(strike * math.exp(-r * t_years) - spot, 0.0)
        upper_bound = strike * math.exp(-r * t_years)
    else:
        lower_bound = max(spot - strike * math.exp(-r * t_years), 0.0)
        upper_bound = spot

    if mid < lower_bound - 1e-6 or mid > upper_bound + 1e-6:
        return None

    def objective(sigma: float) -> float:
        return bs_price(spot, strike, t_years, r, sigma, is_put) - mid

    try:
        return brentq(objective, 1e-6, 5.0, maxiter=100, xtol=1e-8)
    except (ValueError, RuntimeError):
        return None
