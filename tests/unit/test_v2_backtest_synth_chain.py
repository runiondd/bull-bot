"""Unit tests for bullbot.v2.backtest.synth_chain."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from bullbot.v2.backtest import synth_chain


def _bar(close, high=None, low=None, ts=0):
    return SimpleNamespace(
        ts=ts, open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=1_000_000,
    )


def test_event_day_multiplier_returns_1_for_steady_bars():
    """No qualifying event in the last 5 bars -> multiplier = 1.0."""
    bars = [_bar(close=100.0 + i * 0.01) for i in range(30)]  # tiny drift
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == 1.0


def test_event_day_multiplier_returns_175_on_day_of_event():
    """A 5% spike on the most recent bar -> multiplier = 1.75 (event_age=0)."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    bars[-1] = _bar(close=105.0, high=106.0, low=99.0)  # 5% spike on last bar
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == pytest.approx(1.75, abs=0.01)


def test_event_day_multiplier_decays_linearly_back_to_1():
    """Event was 2 days ago: multiplier = 1.0 + 0.75 × (5 - 2)/5 = 1.45.
    Hold close=105 from bars[-3] onward so the reversion doesn't itself count."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    bars[-3] = _bar(close=105.0, high=106.0, low=99.0)  # spike 2 days ago
    bars[-2] = _bar(close=105.0, high=105.5, low=104.5)
    bars[-1] = _bar(close=105.0, high=105.5, low=104.5)
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == pytest.approx(1.0 + 0.75 * (5 - 2) / 5, abs=0.01)


def test_event_day_multiplier_returns_1_after_5_day_decay():
    """Event was 6 days ago and prices held — no revert event in lookback."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    bars[-6] = _bar(close=105.0, high=106.0, low=99.0)  # 5 days ago (outside lookback)
    # Hold the new level so no revert event lands inside lookback
    for i in range(5, 0, -1):
        bars[-i] = _bar(close=105.0, high=105.5, low=104.5)
    # Re-set bars[-6] (the loop above overwrote it; restore the spike)
    bars[-6] = _bar(close=105.0, high=106.0, low=99.0)
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == 1.0


def test_event_day_multiplier_uses_true_range_rule():
    """Big TR on otherwise-flat close: TR rule fires even when return < 3%."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    # day at idx -1: close back to 100 but high/low blown out
    bars[-1] = _bar(close=100.0, high=110.0, low=90.0)
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == pytest.approx(1.75, abs=0.01)


def test_event_day_multiplier_picks_most_recent_event_when_multiple():
    """Two events in lookback: the more recent one wins (highest multiplier).
    Hold close at each new level so reversions don't create phantom events."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    # Event 4 days ago: jump to 110
    bars[-5] = _bar(close=110.0, high=112.0, low=98.0)
    # Hold 110 until next event
    bars[-4] = _bar(close=110.0, high=110.5, low=109.5)
    bars[-3] = _bar(close=110.0, high=110.5, low=109.5)
    # Event 1 day ago: jump back to ~105 (4.5% drop from 110 -> qualifies as event)
    bars[-2] = _bar(close=105.0, high=106.0, low=99.0)
    # Hold 105
    bars[-1] = _bar(close=105.0, high=105.5, low=104.5)
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    # 1 day ago -> 1.0 + 0.75 * (5-1)/5 = 1.60
    assert mult == pytest.approx(1.0 + 0.75 * 4 / 5, abs=0.01)


def test_event_day_multiplier_returns_1_for_too_few_bars():
    """Need at least ATR_WINDOW + 1 = 15 bars for ATR computation."""
    bars = [_bar(close=100.0) for _ in range(10)]
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == 1.0
