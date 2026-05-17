"""Smoke tests that Phase C.0 schema migration creates all five new tables
with the expected columns."""
from __future__ import annotations

import sqlite3

import pytest

from bullbot.db.migrations import apply_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def test_v2_positions_table_exists_with_phase_c0_columns(conn):
    cols = _columns(conn, "v2_positions")
    assert cols == {
        "id", "ticker", "intent", "structure_kind",
        "exit_plan_version", "profit_target_price", "stop_price",
        "time_stop_dte", "assignment_acceptable",
        "nearest_leg_expiry_dte", "exit_plan_extra_json",
        "opened_ts", "closed_ts", "close_reason",
        "linked_position_id", "rationale",
    }


def test_v2_position_legs_table_exists_with_phase_c0_columns(conn):
    cols = _columns(conn, "v2_position_legs")
    assert cols == {
        "id", "position_id", "action", "kind",
        "strike", "expiry", "qty", "entry_price",
        "net_basis", "exit_price",
    }


def test_v2_position_events_table_exists_with_phase_c0_columns(conn):
    cols = _columns(conn, "v2_position_events")
    assert cols == {
        "id", "position_id", "linked_position_id",
        "event_kind", "occurred_ts", "source_leg_id",
        "original_credit_per_contract", "notes",
    }


def test_v2_position_mtm_table_exists(conn):
    cols = _columns(conn, "v2_position_mtm")
    assert cols == {"position_id", "asof_ts", "mtm_value", "source"}


def test_v2_chain_snapshots_table_exists(conn):
    cols = _columns(conn, "v2_chain_snapshots")
    assert cols == {
        "ticker", "asof_ts", "expiry", "strike", "kind",
        "bid", "ask", "last", "iv", "oi", "source",
    }


def test_intent_check_constraint_rejects_unknown_intent(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO v2_positions "
            "(ticker, intent, structure_kind, opened_ts) "
            "VALUES ('AAPL', 'speculate', 'long_call', 1000)"
        )


def test_event_kind_check_constraint_rejects_unknown_kind(conn):
    conn.execute(
        "INSERT INTO v2_positions (id, ticker, intent, structure_kind, opened_ts) "
        "VALUES (1, 'AAPL', 'accumulate', 'csp', 1000)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO v2_position_events "
            "(position_id, event_kind, occurred_ts) "
            "VALUES (1, 'detonated', 1001)"
        )
