"""Unit tests for bullbot.v2.vehicle — LLM-picked entry-decision agent."""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.v2 import vehicle, positions
from bullbot.v2.signals import DirectionalSignal


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def test_legspec_round_trip_through_asdict():
    spec = vehicle.LegSpec(
        action="buy", kind="call", strike=100.0, expiry="2026-06-19", qty_ratio=1,
    )
    assert spec.action == "buy"
    assert spec.qty_ratio == 1


def test_vehicle_decision_rejects_unknown_decision_value():
    with pytest.raises(ValueError, match="decision must be one of"):
        vehicle.VehicleDecision(
            decision="maybe", intent="trade", structure="long_call",
            legs=[], exit_plan={}, rationale="",
        )


def test_vehicle_decision_rejects_unknown_intent():
    with pytest.raises(ValueError, match="intent must be one of"):
        vehicle.VehicleDecision(
            decision="open", intent="speculate", structure="long_call",
            legs=[], exit_plan={}, rationale="",
        )


def test_vehicle_decision_rejects_unknown_structure():
    with pytest.raises(ValueError, match="structure must be one of"):
        vehicle.VehicleDecision(
            decision="open", intent="trade", structure="condor_with_diagonal_wings",
            legs=[], exit_plan={}, rationale="",
        )


def test_sanity_result_ok_true_when_no_reason():
    result = vehicle.SanityResult(ok=True, reason=None)
    assert result.ok is True


def test_structure_kinds_excludes_calendars_and_diagonals():
    """Grok review Tier 3 cut: deferred to C.7."""
    assert "calendar" not in vehicle.STRUCTURE_KINDS
    assert "diagonal" not in vehicle.STRUCTURE_KINDS
    assert "long_call" in vehicle.STRUCTURE_KINDS
    assert "bull_call_spread" in vehicle.STRUCTURE_KINDS
    assert "iron_condor" in vehicle.STRUCTURE_KINDS
    assert "covered_call" in vehicle.STRUCTURE_KINDS


def _seed_chain_snapshot(conn, *, ticker, asof_ts, strike, kind, iv, spot=100.0):
    """Insert one row into v2_chain_snapshots for the iv_rank tests."""
    conn.execute(
        "INSERT OR REPLACE INTO v2_chain_snapshots "
        "(ticker, asof_ts, expiry, strike, kind, bid, ask, last, iv, oi, source) "
        "VALUES (?, ?, '2026-06-19', ?, ?, 1.0, 1.2, 1.1, ?, 100, 'yahoo')",
        (ticker, asof_ts, strike, kind, iv),
    )
    conn.commit()


def test_iv_rank_returns_default_when_no_history(conn):
    rank = vehicle._iv_rank(conn, ticker="AAPL", asof_ts=1_700_000_000, spot=100.0)
    assert rank == 0.5


def test_iv_rank_returns_default_when_under_30_days_history(conn):
    asof = 1_700_000_000
    for i in range(10):  # only 10 days
        _seed_chain_snapshot(
            conn, ticker="AAPL", asof_ts=asof - i * 86400,
            strike=100.0, kind="call", iv=0.30,
        )
    rank = vehicle._iv_rank(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    assert rank == 0.5


def test_iv_rank_returns_high_when_current_iv_at_top_of_range(conn):
    asof = 1_700_000_000
    # 30 days of low IV (0.20), today at high IV (0.50)
    for i in range(30):
        _seed_chain_snapshot(
            conn, ticker="AAPL", asof_ts=asof - (30 - i) * 86400,
            strike=100.0, kind="call", iv=0.20,
        )
    _seed_chain_snapshot(
        conn, ticker="AAPL", asof_ts=asof,
        strike=100.0, kind="call", iv=0.50,
    )
    rank = vehicle._iv_rank(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    assert rank > 0.95


def test_iv_rank_returns_low_when_current_iv_at_bottom_of_range(conn):
    asof = 1_700_000_000
    for i in range(30):
        _seed_chain_snapshot(
            conn, ticker="AAPL", asof_ts=asof - (30 - i) * 86400,
            strike=100.0, kind="call", iv=0.50,
        )
    _seed_chain_snapshot(
        conn, ticker="AAPL", asof_ts=asof,
        strike=100.0, kind="call", iv=0.20,
    )
    rank = vehicle._iv_rank(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    assert rank < 0.05


def test_iv_rank_filters_to_near_atm_strikes_only(conn):
    """Strikes far from spot (>5% away) are excluded — they wouldn't reflect
    the at-the-money IV anyway."""
    asof = 1_700_000_000
    for i in range(30):
        ts = asof - (30 - i) * 86400
        # Add far-OTM strike with WILDLY different IV — should be ignored
        _seed_chain_snapshot(
            conn, ticker="AAPL", asof_ts=ts,
            strike=200.0, kind="call", iv=2.0,  # noise
        )
        # ATM strike with reasonable IV
        _seed_chain_snapshot(
            conn, ticker="AAPL", asof_ts=ts,
            strike=100.0, kind="call", iv=0.30,
        )
    _seed_chain_snapshot(
        conn, ticker="AAPL", asof_ts=asof,
        strike=100.0, kind="call", iv=0.30,
    )
    rank = vehicle._iv_rank(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    # If far-OTM strike included, today's 0.30 would look LOW (max 2.0).
    # Filtered correctly, today's IV equals the historical median.
    assert 0.3 < rank < 0.7
