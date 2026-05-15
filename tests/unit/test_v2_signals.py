"""Unit tests for bullbot.v2.signals."""
from __future__ import annotations

import sqlite3

import pytest

from bullbot.v2 import signals


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE directional_signals (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            asof_ts INTEGER NOT NULL,
            direction TEXT NOT NULL,
            confidence REAL NOT NULL,
            horizon_days INTEGER NOT NULL,
            rationale TEXT,
            rules_version TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE (ticker, asof_ts, rules_version)
        );
    """)
    return c


def test_directional_signal_dataclass_basic():
    s = signals.DirectionalSignal(
        ticker="AAPL",
        asof_ts=1_700_000_000,
        direction="bullish",
        confidence=0.65,
        horizon_days=30,
        rationale="50/200 SMA cross + RSI > 55",
        rules_version="v1",
    )
    assert s.ticker == "AAPL"
    assert s.direction == "bullish"
    assert 0.0 <= s.confidence <= 1.0


def test_save_and_load_roundtrip(conn):
    s = signals.DirectionalSignal(
        ticker="AAPL", asof_ts=1_700_000_000, direction="bullish",
        confidence=0.65, horizon_days=30, rationale="x", rules_version="v1",
    )
    signals.save(conn, s)
    loaded = signals.latest_for(conn, "AAPL", rules_version="v1")
    assert loaded is not None
    assert loaded.ticker == "AAPL"
    assert loaded.direction == "bullish"
    assert loaded.confidence == pytest.approx(0.65)


def test_save_is_idempotent_on_unique_key(conn):
    """Re-saving the same (ticker, asof_ts, rules_version) must not raise — replace."""
    s = signals.DirectionalSignal(
        ticker="AAPL", asof_ts=1_700_000_000, direction="bullish",
        confidence=0.65, horizon_days=30, rationale="x", rules_version="v1",
    )
    signals.save(conn, s)
    s2 = signals.DirectionalSignal(
        ticker="AAPL", asof_ts=1_700_000_000, direction="chop",
        confidence=0.40, horizon_days=14, rationale="y", rules_version="v1",
    )
    signals.save(conn, s2)
    loaded = signals.latest_for(conn, "AAPL", rules_version="v1")
    assert loaded.direction == "chop"


def test_direction_must_be_valid():
    with pytest.raises(ValueError):
        signals.DirectionalSignal(
            ticker="AAPL", asof_ts=0, direction="moonshot",
            confidence=0.5, horizon_days=14, rationale="", rules_version="v1",
        )
