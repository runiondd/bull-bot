"""Unit tests for bullbot.v2.levels — support/resistance computation."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bullbot.v2 import levels


def _bar(close: float, high: float | None = None, low: float | None = None):
    """Build a SimpleNamespace bar with the duck-typed shape v2 uses."""
    return SimpleNamespace(
        ts=0, open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=1_000_000.0,
    )


def test_level_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be one of"):
        levels.Level(price=100.0, kind="fibonacci_618", strength=0.5)


def test_level_rejects_strength_out_of_range():
    with pytest.raises(ValueError, match="strength must be in"):
        levels.Level(price=100.0, kind="swing_high", strength=1.5)
    with pytest.raises(ValueError, match="strength must be in"):
        levels.Level(price=100.0, kind="swing_high", strength=-0.1)


def test_level_distance_to_returns_absolute_difference():
    lvl = levels.Level(price=105.0, kind="swing_high", strength=0.5)
    assert lvl.distance_to(spot=100.0) == 5.0
    assert lvl.distance_to(spot=110.0) == 5.0


def test_level_distance_pct_to_uses_spot_as_denominator():
    lvl = levels.Level(price=105.0, kind="swing_high", strength=0.5)
    assert lvl.distance_pct_to(spot=100.0) == pytest.approx(0.05)


def test_level_is_above_spot_for_resistance():
    lvl = levels.Level(price=110.0, kind="swing_high", strength=0.5)
    assert lvl.is_above(spot=100.0) is True
    assert lvl.is_above(spot=120.0) is False


def test_find_swing_extrema_detects_simple_peak():
    """A clear peak in the middle with rising-then-falling highs."""
    bars = [
        _bar(close=h, high=h, low=h-0.5)
        for h in [100, 101, 102, 103, 105, 103, 102, 101, 100, 99, 98]
        # idx 0   1   2   3   4*   5   6   7   8   9  10
        # idx 4 is the peak (105 > all neighbors within 3 bars on each side)
    ]
    extrema = levels._find_swing_extrema(bars, n_confirm=3)
    swing_highs = [lvl for lvl in extrema if lvl.kind == "swing_high"]
    assert len(swing_highs) == 1
    assert swing_highs[0].price == 105.0


def test_find_swing_extrema_detects_simple_trough():
    bars = [
        _bar(close=l, high=l+0.5, low=l)
        for l in [100, 99, 98, 97, 95, 97, 98, 99, 100, 101, 102]
        # idx 4 is the trough (95 < all neighbors within 3 bars on each side)
    ]
    extrema = levels._find_swing_extrema(bars, n_confirm=3)
    swing_lows = [lvl for lvl in extrema if lvl.kind == "swing_low"]
    assert len(swing_lows) == 1
    assert swing_lows[0].price == 95.0


def test_find_swing_extrema_skips_unconfirmed_recent_bars():
    """A bar that LOOKS like a high but has fewer than n_confirm bars to its
    right is not yet confirmed and should not be returned."""
    bars = [
        _bar(close=h, high=h, low=h-0.5)
        for h in [100, 101, 102, 103, 105, 103, 102]
        # idx 4 is the highest, but only 2 bars to its right (n_confirm=3) -> unconfirmed
    ]
    extrema = levels._find_swing_extrema(bars, n_confirm=3)
    swing_highs = [lvl for lvl in extrema if lvl.kind == "swing_high"]
    assert swing_highs == []


def test_find_swing_extrema_handles_short_series_gracefully():
    """Fewer than 2*n_confirm + 1 bars -> nothing can be confirmed."""
    bars = [_bar(close=100 + i) for i in range(5)]
    extrema = levels._find_swing_extrema(bars, n_confirm=3)
    assert extrema == []


def test_find_swing_extrema_strength_scales_with_touch_count():
    """A level touched (within 0.5%) by many subsequent bars is stronger."""
    # Peak at 100, then prices return to ~100 many times
    closes = [95, 96, 98, 99, 100, 99, 99.6, 100.0, 99.5, 100.2, 99.8, 100.1, 99.7]
    bars = [_bar(close=c, high=c, low=c-0.2) for c in closes]
    extrema = levels._find_swing_extrema(bars, n_confirm=3)
    swing_highs = [lvl for lvl in extrema if lvl.kind == "swing_high"]
    assert len(swing_highs) >= 1
    # Strength must be > 0 (many touches near 100)
    assert swing_highs[0].strength > 0.2
