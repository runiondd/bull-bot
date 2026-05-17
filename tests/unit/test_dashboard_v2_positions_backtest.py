"""Unit tests for v2 dashboard tabs (positions + backtest)."""
from __future__ import annotations

import csv
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from bullbot.dashboard import queries, tabs
from bullbot.db.migrations import apply_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def _seed_position(conn, *, ticker="AAPL", structure="long_call", intent="trade"):
    opened_ts = int(datetime(2026, 5, 10, 23).timestamp())
    cur = conn.execute(
        "INSERT INTO v2_positions "
        "(ticker, intent, structure_kind, exit_plan_version, "
        "profit_target_price, stop_price, time_stop_dte, "
        "assignment_acceptable, nearest_leg_expiry_dte, exit_plan_extra_json, "
        "opened_ts, linked_position_id, rationale) "
        "VALUES (?, ?, ?, 1, 110.0, 95.0, 21, 0, 35, NULL, ?, NULL, ?)",
        (ticker, intent, structure, opened_ts, "bullish breakout"),
    )
    pid = cur.lastrowid
    conn.execute(
        "INSERT INTO v2_position_legs "
        "(position_id, action, kind, strike, expiry, qty, entry_price) "
        "VALUES (?, 'buy', 'call', 100.0, '2026-06-15', 1, 3.50)",
        (pid,),
    )
    conn.commit()
    return pid


def test_v2_positions_returns_empty_list_when_no_positions(conn):
    assert queries.v2_positions(conn) == []


def test_v2_positions_returns_open_position_with_summary(conn):
    pid = _seed_position(conn, ticker="AAPL")
    rows = queries.v2_positions(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "AAPL"
    assert r["structure_kind"] == "long_call"
    assert r["intent"] == "trade"
    assert r["profit_target_price"] == 110.0
    assert r["stop_price"] == 95.0
    assert r["time_stop_dte"] == 21
    assert r["rationale"] == "bullish breakout"
    assert "buy call 100" in r["legs_summary"].lower() or "long_call" in r["legs_summary"].lower()


def test_v2_positions_excludes_closed_positions(conn):
    pid = _seed_position(conn)
    conn.execute(
        "UPDATE v2_positions SET closed_ts=?, close_reason='profit_target' WHERE id=?",
        (int(datetime(2026, 5, 15, 23).timestamp()), pid),
    )
    conn.commit()
    assert queries.v2_positions(conn) == []


def test_v2_positions_includes_latest_mtm(conn):
    pid = _seed_position(conn)
    conn.execute(
        "INSERT INTO v2_position_mtm (position_id, asof_ts, mtm_value, source) "
        "VALUES (?, ?, 425.50, 'bs')",
        (pid, int(datetime(2026, 5, 14, 23).timestamp())),
    )
    conn.commit()
    rows = queries.v2_positions(conn)
    assert rows[0]["latest_mtm_value"] == 425.50
    assert rows[0]["latest_mtm_source"] == "bs"


def test_v2_positions_handles_missing_mtm_gracefully(conn):
    _seed_position(conn)
    rows = queries.v2_positions(conn)
    assert rows[0]["latest_mtm_value"] is None
    assert rows[0]["latest_mtm_source"] is None
