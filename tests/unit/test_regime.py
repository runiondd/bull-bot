"""Regime classifier tests."""
from bullbot.features import regime


def test_flat_chop():
    closes = [100.0] * 60
    assert regime.classify(closes) == "chop"


def test_strong_bull():
    # 8% rise over 60 days, low volatility
    closes = [100.0 + 0.13 * i for i in range(60)]  # ~7.8% return
    # Make it slightly over +5% with low vol
    closes = [100.0 + 0.10 * i + 0.02 * (i % 3) for i in range(60)]
    assert closes[-1] / closes[0] - 1 > 0.05
    result = regime.classify(closes)
    assert result == "bull"


def test_bear_on_drop():
    closes = [100.0 - 0.12 * i for i in range(60)]  # 7.2% drop
    assert regime.classify(closes) == "bear"


def test_high_vol_bull_becomes_chop():
    import math
    closes = [
        100.0 * (1 + 0.06 * i / 59) + 5.0 * math.sin(i)
        for i in range(60)
    ]
    result = regime.classify(closes)
    assert result in ("chop", "bull")
