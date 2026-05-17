"""Unit tests for bullbot.v2.runner_c."""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.v2 import runner_c


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    # Seed a parent position so FK (position_id → v2_positions.id) is satisfied.
    c.execute(
        "INSERT INTO v2_positions (id, ticker, intent, structure_kind, opened_ts) "
        "VALUES (1, 'SPY', 'trade', 'long_call', 1700000000)"
    )
    c.commit()
    return c


def test_write_position_mtm_inserts_row(conn):
    runner_c._write_position_mtm(
        conn, position_id=1, asof_ts=1_700_000_000,
        mtm_value=1234.56, source="bs",
    )
    row = conn.execute(
        "SELECT position_id, asof_ts, mtm_value, source FROM v2_position_mtm"
    ).fetchone()
    assert row["position_id"] == 1
    assert row["asof_ts"] == 1_700_000_000
    assert row["mtm_value"] == 1234.56
    assert row["source"] == "bs"


def test_write_position_mtm_is_idempotent_on_pk(conn):
    runner_c._write_position_mtm(
        conn, position_id=1, asof_ts=1_700_000_000,
        mtm_value=100.0, source="yahoo",
    )
    runner_c._write_position_mtm(
        conn, position_id=1, asof_ts=1_700_000_000,
        mtm_value=200.0, source="bs",
    )
    rows = conn.execute("SELECT mtm_value, source FROM v2_position_mtm").fetchall()
    assert len(rows) == 1
    assert rows[0]["mtm_value"] == 200.0
    assert rows[0]["source"] == "bs"
