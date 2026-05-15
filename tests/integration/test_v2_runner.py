"""Integration tests for bullbot.v2.runner."""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def conn(monkeypatch):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
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
    for i in range(250):
        c.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES ('AAPL', '1d', ?, ?, ?, ?, ?, ?)",
            (1_700_000_000 + i * 86400, 100 + i * 0.5, 100.5 + i * 0.5, 99.5 + i * 0.5, 100 + i * 0.5, 1_000_000),
        )
    for i in range(250):
        c.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES ('TSLA', '1d', ?, ?, ?, ?, ?, ?)",
            (1_700_000_000 + i * 86400, 200 - i * 0.5, 200.5 - i * 0.5, 199.5 - i * 0.5, 200 - i * 0.5, 1_000_000),
        )
    monkeypatch.setattr("bullbot.config.UNIVERSE", ["AAPL", "TSLA"])
    return c


def test_run_once_writes_signals_for_universe(conn):
    from bullbot.v2 import runner

    n = runner.run_once(conn, asof_ts=1_700_000_000 + 250 * 86400)
    assert n == 2
    rows = conn.execute("SELECT ticker, direction FROM directional_signals ORDER BY ticker").fetchall()
    assert [(r["ticker"], r["direction"]) for r in rows] == [
        ("AAPL", "bullish"), ("TSLA", "bearish"),
    ]


def test_run_once_is_idempotent(conn):
    from bullbot.v2 import runner
    runner.run_once(conn, asof_ts=1_700_000_000 + 250 * 86400)
    runner.run_once(conn, asof_ts=1_700_000_000 + 250 * 86400)
    n = conn.execute("SELECT COUNT(*) FROM directional_signals").fetchone()[0]
    assert n == 2


def test_run_once_also_dispatches_paper_trades(conn):
    """The runner should both write signals AND open paper trades."""
    # Add the trades table to the fixture
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

    from bullbot.v2 import runner
    runner.run_once(conn, asof_ts=1_700_000_000 + 250 * 86400)

    # AAPL bullish high-conf → long position opened.
    aapl = conn.execute("SELECT direction, shares FROM v2_paper_trades WHERE ticker='AAPL'").fetchone()
    assert aapl is not None
    assert aapl["direction"] == "long"
    assert aapl["shares"] >= 1

    # TSLA bearish high-conf → short position opened.
    tsla = conn.execute("SELECT direction FROM v2_paper_trades WHERE ticker='TSLA'").fetchone()
    assert tsla is not None
    assert tsla["direction"] == "short"
