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


# ---------------------------------------------------------------------------
# Task 3 — _large_move_count_90d
# ---------------------------------------------------------------------------
from types import SimpleNamespace


def _bar(close, high=None, low=None):
    return SimpleNamespace(
        ts=0, open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=1_000_000,
    )


def test_large_move_count_zero_for_steady_bars():
    bars = [_bar(close=100.0 + i * 0.01) for i in range(100)]  # tiny drift
    assert vehicle._large_move_count_90d(bars) == 0


def test_large_move_count_detects_large_close_to_close_return():
    bars = [_bar(close=100.0) for _ in range(50)]
    # day 30 spikes 5% — counts; day 31 recovers ~4.76% back — also counts
    bars[30] = _bar(close=105.0, high=105.5, low=99.5)
    n = vehicle._large_move_count_90d(bars)
    assert n == 2


def test_large_move_count_detects_large_true_range():
    """Big intra-day range but close near prior close — captured by TR rule."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(50)]
    # day 30: close still 100 but high/low blown out
    bars[30] = _bar(close=100.0, high=110.0, low=90.0)
    n = vehicle._large_move_count_90d(bars)
    assert n == 1


def test_large_move_count_only_considers_last_90_bars():
    bars = [_bar(close=100.0, high=100.2, low=99.8) for _ in range(120)]
    # spike at idx 5 (outside last 90 = idx 30..119)
    bars[5] = _bar(close=110.0, high=115.0, low=100.0)
    # spike at idx 100 (inside last 90 window); bars 101..119 stay at 110 so
    # the close-to-close recovery doesn't trigger a second large-move count.
    bars[100] = _bar(close=110.0, high=115.0, low=100.0)
    for i in range(101, 120):
        bars[i] = _bar(close=110.0, high=110.2, low=109.8)
    n = vehicle._large_move_count_90d(bars)
    assert n == 1


def test_large_move_count_returns_zero_for_too_few_bars():
    bars = [_bar(close=100.0) for _ in range(5)]
    # 5 bars is below the 14-bar ATR floor; helper returns 0 rather than crashing.
    assert vehicle._large_move_count_90d(bars) == 0


# ---------------------------------------------------------------------------
# Task 4 — _near_atm_liquidity
# ---------------------------------------------------------------------------

def _seed_chain_with_oi(conn, *, ticker, asof_ts, expiry, strike, kind, bid, ask, oi):
    conn.execute(
        "INSERT OR REPLACE INTO v2_chain_snapshots "
        "(ticker, asof_ts, expiry, strike, kind, bid, ask, last, iv, oi, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'yahoo')",
        (ticker, asof_ts, expiry, strike, kind, bid, ask, (bid + ask) / 2, oi),
    )
    conn.commit()


def test_near_atm_liquidity_returns_zeros_when_no_data(conn):
    out = vehicle._near_atm_liquidity(conn, ticker="XYZ", asof_ts=1_700_000_000, spot=100.0)
    assert out["total_oi_within_5pct"] == 0
    assert out["spread_avg_pct"] is None
    assert out["nearest_expiry"] is None


def test_near_atm_liquidity_sums_oi_in_band_only(conn):
    asof = 1_700_000_000
    # In-band strikes (95, 100, 105 with spot=100)
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-06-19",
                        strike=100.0, kind="call", bid=2.0, ask=2.2, oi=1000)
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-06-19",
                        strike=104.0, kind="put", bid=1.5, ask=1.7, oi=500)
    # Out-of-band strike (110, > 5% above spot=100)
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-06-19",
                        strike=110.0, kind="call", bid=0.5, ask=0.7, oi=99999)
    out = vehicle._near_atm_liquidity(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    assert out["total_oi_within_5pct"] == 1500  # 1000 + 500, NOT 99999


def test_near_atm_liquidity_computes_average_bid_ask_spread_pct(conn):
    asof = 1_700_000_000
    # Two in-band strikes: spread ~9.52% and ~4.88%, avg ~7.2%
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-06-19",
                        strike=100.0, kind="call", bid=1.0, ask=1.1, oi=100)
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-06-19",
                        strike=100.0, kind="put", bid=2.0, ask=2.1, oi=100)
    out = vehicle._near_atm_liquidity(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    assert out["spread_avg_pct"] is not None
    assert 0.06 < out["spread_avg_pct"] < 0.08  # average ≈ 7.2%


def test_near_atm_liquidity_returns_nearest_expiry(conn):
    asof = 1_700_000_000
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-07-17",
                        strike=100.0, kind="call", bid=2.0, ask=2.2, oi=100)
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-06-19",
                        strike=100.0, kind="call", bid=2.0, ask=2.2, oi=100)
    out = vehicle._near_atm_liquidity(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    assert out["nearest_expiry"] == "2026-06-19"
