"""
Greeks and IV inversion tests.

Black-Scholes closed form + Brent's-method inverter. Golden values from
standard references (e.g., Hull's textbook table 15.2).
"""
import math

import pytest

from bullbot.features import greeks


def test_bs_call_atm_short_dated():
    # S=100, K=100, r=5%, T=0.25 (3 months), sigma=20%
    # Hull reference value: ~4.615
    price = greeks.bs_price(
        spot=100.0, strike=100.0, t_years=0.25, r=0.05, sigma=0.20, is_put=False
    )
    assert 4.5 < price < 4.7


def test_bs_put_itm():
    # S=95, K=100, r=5%, T=0.25, sigma=30%
    price = greeks.bs_price(
        spot=95.0, strike=100.0, t_years=0.25, r=0.05, sigma=0.30, is_put=True
    )
    assert 6.0 < price < 8.0


def test_bs_atm_delta_is_around_half():
    g = greeks.compute_greeks(
        spot=100.0, strike=100.0, t_years=0.25, r=0.05, sigma=0.20, is_put=False
    )
    assert 0.55 < g.delta < 0.65  # ATM-ish call delta > 0.5 due to r > 0


def test_bs_put_delta_is_negative():
    g = greeks.compute_greeks(
        spot=100.0, strike=100.0, t_years=0.25, r=0.05, sigma=0.20, is_put=True
    )
    assert -0.5 < g.delta < 0.0


def test_bs_theta_is_negative_for_long_options():
    g = greeks.compute_greeks(
        spot=100.0, strike=100.0, t_years=0.25, r=0.05, sigma=0.20, is_put=False
    )
    assert g.theta < 0


def test_implied_vol_roundtrip_call():
    sigma_true = 0.25
    price = greeks.bs_price(
        spot=100.0, strike=105.0, t_years=0.5, r=0.03, sigma=sigma_true, is_put=False
    )
    sigma_recovered = greeks.implied_volatility(
        mid=price, spot=100.0, strike=105.0, t_years=0.5, r=0.03, is_put=False
    )
    assert abs(sigma_recovered - sigma_true) < 1e-4


def test_implied_vol_roundtrip_put():
    sigma_true = 0.18
    price = greeks.bs_price(
        spot=100.0, strike=95.0, t_years=0.25, r=0.04, sigma=sigma_true, is_put=True
    )
    sigma_recovered = greeks.implied_volatility(
        mid=price, spot=100.0, strike=95.0, t_years=0.25, r=0.04, is_put=True
    )
    assert abs(sigma_recovered - sigma_true) < 1e-4


def test_implied_vol_returns_none_on_nonsense_price():
    assert (
        greeks.implied_volatility(
            mid=0.01, spot=100.0, strike=150.0, t_years=0.25, r=0.03, is_put=False
        )
        is None
    )
