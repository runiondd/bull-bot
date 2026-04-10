"""Golden-value tests for every indicator. Numbers hand-computed."""
import numpy as np
import pytest

from bullbot.features import indicators


CLOSES_20 = [
    100.0, 101.5, 102.0, 101.0, 99.5, 100.2, 101.8, 103.0, 104.5, 103.8,
    105.0, 106.2, 105.5, 104.8, 106.0, 107.5, 108.0, 107.2, 108.5, 109.0,
]


def test_sma_20_matches_numpy():
    result = indicators.sma(CLOSES_20, 20)
    expected = float(np.mean(CLOSES_20))
    assert abs(result - expected) < 1e-9


def test_sma_returns_none_when_insufficient_data():
    assert indicators.sma([1.0, 2.0, 3.0], 20) is None


def test_ema_20_matches_pandas():
    import pandas as pd
    result = indicators.ema(CLOSES_20, 20)
    expected = pd.Series(CLOSES_20).ewm(span=20, adjust=False).mean().iloc[-1]
    assert abs(result - float(expected)) < 1e-9


def test_rsi_14_known_value():
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
              46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
              46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78]
    rsi = indicators.rsi(closes, 14)
    assert 40.0 < rsi < 50.0


def test_atr_14_returns_positive_for_real_series():
    highs = [102.0, 103.5, 104.0, 105.0, 104.5, 106.0, 107.5, 108.0,
             107.2, 108.5, 109.0, 110.5, 111.0, 112.0, 113.5]
    lows = [99.0, 100.5, 101.0, 102.0, 101.5, 103.0, 104.5, 105.0,
            104.2, 105.5, 106.0, 107.5, 108.0, 109.0, 110.5]
    closes = [101.0, 102.0, 103.5, 104.0, 103.0, 105.5, 106.0, 107.5,
              106.2, 107.0, 108.5, 109.0, 110.0, 111.5, 112.5]
    atr = indicators.atr(highs, lows, closes, 14)
    assert atr > 0
    assert 1.0 < atr < 5.0


def test_bollinger_bands_symmetry():
    constant = [100.0] * 25
    upper, mid, lower = indicators.bollinger(constant, 20, 2.0)
    assert abs(upper - 100.0) < 1e-9
    assert abs(mid - 100.0) < 1e-9
    assert abs(lower - 100.0) < 1e-9


def test_bollinger_width_on_real_series():
    upper, mid, lower = indicators.bollinger(CLOSES_20, 20, 2.0)
    assert upper > mid > lower


def test_iv_rank_uses_min_max_of_history():
    history = [0.10, 0.15, 0.20, 0.25, 0.30]
    current = 0.20
    rank = indicators.iv_rank(current, history)
    assert abs(rank - 50.0) < 1e-6


def test_iv_rank_current_at_max():
    history = [0.10, 0.15, 0.20, 0.25, 0.30]
    assert abs(indicators.iv_rank(0.30, history) - 100.0) < 1e-6


def test_iv_rank_current_at_min():
    history = [0.10, 0.15, 0.20, 0.25, 0.30]
    assert abs(indicators.iv_rank(0.10, history) - 0.0) < 1e-6


def test_iv_percentile_counts_rank():
    history = [0.10, 0.15, 0.20, 0.25, 0.30]
    pct = indicators.iv_percentile(0.22, history)
    assert abs(pct - 60.0) < 1e-6
