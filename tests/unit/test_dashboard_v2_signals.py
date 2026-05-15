"""Unit tests for the V2 Signals dashboard tab + query."""
from __future__ import annotations

import sqlite3
import time

import pytest

from bullbot.dashboard import queries, tabs


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
    now = int(time.time())
    c.execute(
        "INSERT INTO directional_signals "
        "(ticker, asof_ts, direction, confidence, horizon_days, rationale, rules_version, created_at) "
        "VALUES ('AAPL', ?, 'bullish', 0.65, 30, '50/200 cross', 'v1', ?)",
        (now, now),
    )
    c.execute(
        "INSERT INTO directional_signals "
        "(ticker, asof_ts, direction, confidence, horizon_days, rationale, rules_version, created_at) "
        "VALUES ('TSLA', ?, 'bearish', 0.40, 30, '50/200 inverse', 'v1', ?)",
        (now, now),
    )
    return c


def test_v2_signals_query_returns_latest_per_ticker(conn):
    rows = queries.v2_signals(conn)
    assert {r["ticker"] for r in rows} == {"AAPL", "TSLA"}
    aapl = next(r for r in rows if r["ticker"] == "AAPL")
    assert aapl["direction"] == "bullish"
    assert aapl["confidence"] == pytest.approx(0.65)


def test_v2_signals_tab_renders_signals(conn):
    data = {"v2_signals": queries.v2_signals(conn)}
    html = tabs.v2_signals_tab(data)
    assert "AAPL" in html
    assert "bullish" in html
    assert "TSLA" in html
    assert "bearish" in html
    assert "0.65" in html


def test_v2_signals_tab_empty_state():
    html = tabs.v2_signals_tab({"v2_signals": []})
    assert "No v2 signals" in html


def test_v2_signals_query_handles_missing_table():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    assert queries.v2_signals(c) == []
