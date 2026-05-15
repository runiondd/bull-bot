"""Unit tests for bullbot.v2.underlying."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from bullbot.v2 import underlying


@dataclass
class _FakeBar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def _trending_up_bars(n: int = 250, start: float = 100.0) -> list[_FakeBar]:
    bars = []
    for i in range(n):
        c = start + i * 0.5
        bars.append(_FakeBar(ts=1_700_000_000 + i * 86400, open=c - 0.1, high=c + 0.5, low=c - 0.5, close=c, volume=1_000_000))
    return bars


def _trending_down_bars(n: int = 250, start: float = 200.0) -> list[_FakeBar]:
    bars = []
    for i in range(n):
        c = start - i * 0.5
        bars.append(_FakeBar(ts=1_700_000_000 + i * 86400, open=c + 0.1, high=c + 0.5, low=c - 0.5, close=c, volume=1_000_000))
    return bars


def _flat_bars(n: int = 250, price: float = 100.0) -> list[_FakeBar]:
    return [_FakeBar(ts=1_700_000_000 + i * 86400, open=price, high=price + 0.5, low=price - 0.5, close=price, volume=1_000_000) for i in range(n)]


def test_classify_returns_bullish_on_uptrend():
    sig = underlying.classify(ticker="AAPL", bars=_trending_up_bars(), asof_ts=1_700_000_000 + 250 * 86400)
    assert sig.direction == "bullish"
    assert sig.confidence > 0.0
    assert sig.rules_version


def test_classify_returns_bearish_on_downtrend():
    sig = underlying.classify(ticker="AAPL", bars=_trending_down_bars(), asof_ts=1_700_000_000 + 250 * 86400)
    assert sig.direction == "bearish"


def test_classify_returns_chop_on_flat():
    sig = underlying.classify(ticker="AAPL", bars=_flat_bars(), asof_ts=1_700_000_000 + 250 * 86400)
    assert sig.direction == "chop"


def test_classify_returns_no_edge_with_too_few_bars():
    short = _trending_up_bars(n=50)
    sig = underlying.classify(ticker="AAPL", bars=short, asof_ts=1_700_000_000 + 50 * 86400)
    assert sig.direction == "no_edge"
    assert sig.confidence == 0.0


def test_confidence_is_clamped_to_unit_interval():
    bars = _flat_bars(n=240)
    bars.append(_FakeBar(ts=bars[-1].ts + 86400, open=100, high=10000, low=100, close=10000, volume=1_000_000))
    sig = underlying.classify(ticker="AAPL", bars=bars, asof_ts=bars[-1].ts)
    assert 0.0 <= sig.confidence <= 1.0
