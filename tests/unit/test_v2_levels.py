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
