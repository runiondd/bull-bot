"""Tests for equity snapshot writer."""
from __future__ import annotations

import sqlite3
import time

import pytest

from bullbot.db import migrations
from bullbot.research import equity_snapshot


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrations.apply_schema(c)
    return c


def test_take_snapshot_writes_row(conn):
    """Empty DB: snapshot still writes (with zero pnl)."""
    path = equity_snapshot.take_snapshot(conn, now=1_700_000_000)
    rows = conn.execute("SELECT * FROM equity_snapshots").fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["ts"] == 1_700_000_000 - (1_700_000_000 % 86400)  # midnight UTC
    assert r["total_equity"] == r["income_equity"] + r["growth_equity"]
    assert r["realized_pnl"] == 0
    assert r["unrealized_pnl"] == 0


def test_take_snapshot_idempotent_same_day(conn):
    """Two calls on the same UTC day overwrite, don't error or duplicate."""
    equity_snapshot.take_snapshot(conn, now=1_700_000_000)
    equity_snapshot.take_snapshot(conn, now=1_700_000_000 + 3600)
    rows = conn.execute("SELECT COUNT(*) FROM equity_snapshots").fetchone()
    assert rows[0] == 1  # only one row, second call upserted


def test_take_snapshot_aggregates_paper_positions(conn):
    """Paper realized + unrealized P&L flow into the snapshot."""
    conn.execute(
        "INSERT INTO positions (run_id, ticker, opened_at, open_price, "
        "mark_to_mkt, pnl_realized, unrealized_pnl, closed_at) "
        "VALUES ('paper', 'SPY', 0, -515, 0, 100, 0, 1)"
    )
    conn.execute(
        "INSERT INTO positions (run_id, ticker, opened_at, open_price, "
        "mark_to_mkt, unrealized_pnl) "
        "VALUES ('paper', 'TSLA', 0, 9000, 9000, -250)"
    )
    equity_snapshot.take_snapshot(conn, now=1_700_000_000)
    r = conn.execute("SELECT realized_pnl, unrealized_pnl FROM equity_snapshots").fetchone()
    assert r["realized_pnl"] == 100
    assert r["unrealized_pnl"] == -250


def test_take_snapshot_excludes_backtest_positions(conn):
    """Backtest positions must not poison the live equity curve."""
    conn.execute(
        "INSERT INTO positions (run_id, ticker, opened_at, open_price, "
        "mark_to_mkt, pnl_realized, unrealized_pnl, closed_at) "
        "VALUES ('bt:abc', 'SPY', 0, -100, 0, 999999, 0, 1)"
    )
    equity_snapshot.take_snapshot(conn, now=1_700_000_000)
    r = conn.execute("SELECT realized_pnl FROM equity_snapshots").fetchone()
    assert r["realized_pnl"] == 0
