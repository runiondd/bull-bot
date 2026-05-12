import math
import pytest
from bullbot.features import indicators


def test_cagr_positive_return():
    curve = [100.0, 200.0]
    result = indicators.cagr(curve, days=365)
    assert abs(result - 1.0) < 0.01

def test_cagr_negative_return():
    curve = [100.0, 50.0]
    result = indicators.cagr(curve, days=365)
    assert abs(result - (-0.50)) < 0.01

def test_cagr_multi_year():
    curve = [100.0, 200.0]
    result = indicators.cagr(curve, days=730)
    assert abs(result - 0.414) < 0.01

def test_cagr_flat():
    curve = [100.0, 100.0]
    result = indicators.cagr(curve, days=365)
    assert result == 0.0

def test_cagr_too_short():
    curve = [100.0]
    result = indicators.cagr(curve, days=365)
    assert result == 0.0


# --- Regression: equity-to-negative produced a complex number that crashed
# plateau.classify on TSLA (iteration_failures.id=18, 2026-04-10). Added 2026-05-12.
def test_cagr_end_zero_returns_finite_real_number():
    curve = [100.0, 0.0]
    result = indicators.cagr(curve, days=365)
    assert isinstance(result, float)
    assert result == -1.0  # -100% — lost everything


def test_cagr_end_negative_returns_finite_real_number():
    """Equity going negative (possible with leveraged short options) must NOT
    produce a complex number — that crashes downstream comparisons in plateau.py.
    """
    curve = [100.0, -50.0]
    result = indicators.cagr(curve, days=365)
    assert isinstance(result, float)
    assert not math.isnan(result)
    assert result == -1.0


def test_cagr_end_negative_fractional_years_no_complex():
    """The original crash path: end<0 with fractional years produced complex."""
    curve = [100.0, -10.0]
    result = indicators.cagr(curve, days=180)  # 180/365 = ~0.49 years
    assert isinstance(result, float)
    assert result == -1.0

def test_sortino_all_positive():
    returns = [0.05, 0.03, 0.04, 0.02, 0.06]
    result = indicators.sortino(returns, risk_free_rate=0.0)
    assert math.isinf(result)

def test_sortino_mixed_returns():
    returns = [0.10, -0.05, 0.08, -0.02, 0.06, -0.01, 0.04]
    result = indicators.sortino(returns, risk_free_rate=0.0)
    assert result > 0

def test_sortino_all_negative():
    returns = [-0.05, -0.03, -0.04]
    result = indicators.sortino(returns, risk_free_rate=0.0)
    assert result < 0

def test_sortino_empty():
    result = indicators.sortino([], risk_free_rate=0.0)
    assert result == 0.0

def test_sortino_with_risk_free():
    returns = [0.10, -0.05, 0.08, -0.02, 0.06]
    r0 = indicators.sortino(returns, risk_free_rate=0.0)
    r1 = indicators.sortino(returns, risk_free_rate=0.04)
    assert r1 < r0
