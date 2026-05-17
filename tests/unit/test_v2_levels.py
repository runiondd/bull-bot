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


def test_sma_levels_emits_one_level_per_window_with_enough_bars():
    bars = [_bar(close=100 + i * 0.1) for i in range(250)]  # 250 bars, all SMAs computable
    sma_lvls = levels._sma_levels(bars)
    kinds = {lvl.kind for lvl in sma_lvls}
    assert kinds == {"sma_20", "sma_50", "sma_200"}


def test_sma_levels_skips_windows_with_insufficient_bars():
    bars = [_bar(close=100.0) for _ in range(30)]  # only 30 bars
    sma_lvls = levels._sma_levels(bars)
    kinds = {lvl.kind for lvl in sma_lvls}
    assert kinds == {"sma_20"}  # 50 and 200 don't have enough bars


def test_sma_levels_computes_arithmetic_mean_of_last_n_closes():
    """100 bars at close=100.0 -> SMA_20 = 100.0, SMA_50 = 100.0."""
    bars = [_bar(close=100.0) for _ in range(100)]
    sma_lvls = levels._sma_levels(bars)
    sma_20 = next(lvl for lvl in sma_lvls if lvl.kind == "sma_20")
    sma_50 = next(lvl for lvl in sma_lvls if lvl.kind == "sma_50")
    assert sma_20.price == pytest.approx(100.0)
    assert sma_50.price == pytest.approx(100.0)


def test_sma_levels_window_200_has_higher_strength_than_window_20():
    bars = [_bar(close=100.0) for _ in range(250)]
    sma_lvls = levels._sma_levels(bars)
    sma_20 = next(lvl for lvl in sma_lvls if lvl.kind == "sma_20")
    sma_200 = next(lvl for lvl in sma_lvls if lvl.kind == "sma_200")
    assert sma_200.strength > sma_20.strength


def test_sma_levels_returns_empty_for_no_bars():
    assert levels._sma_levels([]) == []


def test_round_number_levels_for_spot_under_50_uses_dollar_step():
    """spot=23, 2% band = ±$0.46 -> only $23 is within 2%."""
    rn = levels._round_number_levels(spot=23.0)
    prices = sorted(lvl.price for lvl in rn)
    assert prices == [23.0]


def test_round_number_levels_for_mid_priced_stock_uses_five_dollar_step():
    """spot=103, step=$5, 2% band = ±$2.06 -> only $105 is within 2% (above)
    and $100 is just outside ($3 away, > 2%)."""
    rn = levels._round_number_levels(spot=103.0)
    prices = sorted(lvl.price for lvl in rn)
    assert prices == [105.0]


def test_round_number_levels_for_mid_priced_stock_captures_both_sides_when_close():
    """spot=101.0 -> $100 ($1 away, in)."""
    rn = levels._round_number_levels(spot=101.0)
    prices = sorted(lvl.price for lvl in rn)
    assert prices == [100.0]


def test_round_number_levels_for_expensive_stock_uses_ten_dollar_step():
    """spot=237.50, step=$10, 2% band = ±$4.75 -> $240 is $2.50 away (in),
    $230 is $7.50 away (out)."""
    rn = levels._round_number_levels(spot=237.50)
    prices = sorted(lvl.price for lvl in rn)
    assert prices == [240.0]


def test_round_number_levels_for_high_priced_stock_uses_fifty_dollar_step():
    """spot=1010, step=$50, 2% band = ±$20.20 -> $1000 ($10 away, in),
    $1050 ($40 away, out)."""
    rn = levels._round_number_levels(spot=1010.0)
    prices = sorted(lvl.price for lvl in rn)
    assert prices == [1000.0]


def test_round_number_levels_all_have_kind_round_number_and_fixed_strength():
    rn = levels._round_number_levels(spot=100.5)
    assert all(lvl.kind == "round_number" for lvl in rn)
    assert all(lvl.strength == 0.3 for lvl in rn)


def test_round_number_levels_for_zero_or_negative_spot_returns_empty():
    assert levels._round_number_levels(spot=0.0) == []
    assert levels._round_number_levels(spot=-5.0) == []
