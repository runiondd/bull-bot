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


def test_v2_signals_query_joins_open_position_and_pnl(conn):
    """When v2_paper_trades exists, the latest signal rows must carry the
    ticker's open-position direction/shares/entry and lifetime realized PnL."""
    conn.executescript("""
        CREATE TABLE v2_paper_trades (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            shares REAL NOT NULL,
            entry_price REAL NOT NULL,
            entry_ts INTEGER NOT NULL,
            exit_price REAL,
            exit_ts INTEGER,
            pnl_realized REAL,
            exit_reason TEXT,
            signal_id INTEGER,
            created_at INTEGER NOT NULL
        );
    """)
    # AAPL: one closed +$50 trade + one open long 3 shares @ $298.
    conn.execute(
        "INSERT INTO v2_paper_trades (ticker, direction, shares, entry_price, entry_ts, "
        "exit_price, exit_ts, pnl_realized, exit_reason, signal_id, created_at) "
        "VALUES ('AAPL','long',5,100,1,110,2,50.0,'signal_chop',NULL,1)"
    )
    conn.execute(
        "INSERT INTO v2_paper_trades (ticker, direction, shares, entry_price, entry_ts, "
        "exit_price, exit_ts, pnl_realized, exit_reason, signal_id, created_at) "
        "VALUES ('AAPL','long',3,298,3,NULL,NULL,NULL,NULL,NULL,3)"
    )
    rows = queries.v2_signals(conn)
    aapl = next(r for r in rows if r["ticker"] == "AAPL")
    assert aapl["open_direction"] == "long"
    assert aapl["open_shares"] == 3
    assert aapl["open_entry"] == pytest.approx(298.0)
    assert aapl["realized_pnl"] == pytest.approx(50.0)


def test_v2_signals_tab_renders_position_and_pnl(conn):
    """Tab must show position cell + PnL cell when those fields are present."""
    data = {"v2_signals": [
        {
            "ticker": "AAPL", "asof_ts": 1_700_000_000, "direction": "bullish",
            "confidence": 0.65, "horizon_days": 30, "rationale": "x", "rules_version": "v1",
            "open_direction": "long", "open_shares": 3.0, "open_entry": 298.0,
            "current_price": 310.0, "unrealized_pnl": 36.0,
            "realized_pnl": 50.0,
        },
    ]}
    html_out = tabs.v2_signals_tab(data)
    assert "long" in html_out
    assert "298" in html_out
    assert "50" in html_out
    assert "310" in html_out  # current price
    assert "36" in html_out  # unrealized PnL


def test_v2_signals_query_computes_unrealized_pnl(conn):
    """Mark-to-market: queries.v2_signals must compute unrealized_pnl using
    the latest bar close vs entry price for the open position."""
    conn.executescript("""
        CREATE TABLE bars (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            UNIQUE(ticker, timeframe, ts)
        );
        CREATE TABLE v2_paper_trades (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            shares REAL NOT NULL,
            entry_price REAL NOT NULL,
            entry_ts INTEGER NOT NULL,
            exit_price REAL,
            exit_ts INTEGER,
            pnl_realized REAL,
            exit_reason TEXT,
            signal_id INTEGER,
            created_at INTEGER NOT NULL
        );
    """)
    # Open AAPL long 10 shares @ $100. Latest bar close $110. Unrealized = +$100.
    conn.execute("INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
                 "VALUES ('AAPL', '1d', 1, 100, 110, 99, 110, 1)")
    conn.execute("INSERT INTO v2_paper_trades (ticker, direction, shares, entry_price, "
                 "entry_ts, signal_id, created_at) VALUES ('AAPL','long',10,100,1,NULL,1)")
    rows = queries.v2_signals(conn)
    aapl = next(r for r in rows if r["ticker"] == "AAPL")
    assert aapl["current_price"] == pytest.approx(110.0)
    assert aapl["unrealized_pnl"] == pytest.approx(100.0)


