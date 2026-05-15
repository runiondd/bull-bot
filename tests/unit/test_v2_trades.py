"""Unit tests for bullbot.v2.trades."""
from __future__ import annotations

import sqlite3

import pytest

from bullbot.v2 import trades


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
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
    return c


def test_trade_dataclass_validates_direction():
    with pytest.raises(ValueError):
        trades.Trade(
            ticker="AAPL", direction="moonshot", shares=10, entry_price=100,
            entry_ts=1, signal_id=None,
        )


def test_open_trade_writes_row(conn):
    t = trades.open_trade(
        conn, ticker="AAPL", direction="long", shares=3, entry_price=298.0,
        entry_ts=1_700_000_000, signal_id=42,
    )
    assert t.id is not None
    row = conn.execute("SELECT * FROM v2_paper_trades WHERE id=?", (t.id,)).fetchone()
    assert row["ticker"] == "AAPL"
    assert row["direction"] == "long"
    assert row["shares"] == 3
    assert row["exit_price"] is None
    assert row["exit_ts"] is None
    assert row["pnl_realized"] is None


def test_open_position_for_returns_none_when_no_open_trade(conn):
    assert trades.open_position_for(conn, "AAPL") is None


def test_open_position_for_returns_open_trade_only(conn):
    trades.open_trade(
        conn, ticker="AAPL", direction="long", shares=3, entry_price=298.0,
        entry_ts=1_700_000_000, signal_id=None,
    )
    t = trades.open_position_for(conn, "AAPL")
    assert t is not None
    assert t.direction == "long"


def test_close_trade_computes_pnl_long(conn):
    t = trades.open_trade(
        conn, ticker="AAPL", direction="long", shares=10, entry_price=100.0,
        entry_ts=1_700_000_000, signal_id=None,
    )
    closed = trades.close_trade(
        conn, trade_id=t.id, exit_price=110.0, exit_ts=1_700_086_400,
        exit_reason="signal_flip",
    )
    assert closed.pnl_realized == pytest.approx(100.0)  # (110-100) * 10
    assert closed.exit_reason == "signal_flip"


def test_close_trade_computes_pnl_short(conn):
    t = trades.open_trade(
        conn, ticker="AAPL", direction="short", shares=10, entry_price=100.0,
        entry_ts=1_700_000_000, signal_id=None,
    )
    closed = trades.close_trade(
        conn, trade_id=t.id, exit_price=90.0, exit_ts=1_700_086_400,
        exit_reason="signal_flip",
    )
    assert closed.pnl_realized == pytest.approx(100.0)  # short profits when price drops


def test_total_pnl_excludes_open_trades(conn):
    t1 = trades.open_trade(conn, ticker="AAPL", direction="long", shares=10, entry_price=100, entry_ts=1, signal_id=None)
    trades.close_trade(conn, trade_id=t1.id, exit_price=110, exit_ts=2, exit_reason="x")
    trades.open_trade(conn, ticker="TSLA", direction="long", shares=10, entry_price=200, entry_ts=3, signal_id=None)
    # Open TSLA trade should not contribute to total.
    assert trades.total_realized_pnl(conn) == pytest.approx(100.0)
