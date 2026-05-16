"""Unit tests for bullbot.v2.positions."""
from __future__ import annotations

import sqlite3

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.v2 import positions


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def test_optionleg_rejects_unknown_action():
    with pytest.raises(ValueError, match="action must be one of"):
        positions.OptionLeg(
            action="hold", kind="call", strike=100.0,
            expiry="2026-06-19", qty=1, entry_price=2.50,
        )


def test_optionleg_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be one of"):
        positions.OptionLeg(
            action="buy", kind="future", strike=100.0,
            expiry="2026-06-19", qty=1, entry_price=2.50,
        )


def test_optionleg_share_leg_requires_null_strike_and_expiry():
    with pytest.raises(ValueError, match="share leg must have strike=None and expiry=None"):
        positions.OptionLeg(
            action="buy", kind="share", strike=100.0,
            expiry=None, qty=100, entry_price=100.0,
        )


def test_optionleg_option_leg_requires_strike_and_expiry():
    with pytest.raises(ValueError, match="option leg must have non-None strike and expiry"):
        positions.OptionLeg(
            action="buy", kind="call", strike=None,
            expiry="2026-06-19", qty=1, entry_price=2.50,
        )


def test_optionleg_net_basis_defaults_to_none():
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=100.0,
        expiry="2026-06-19", qty=1, entry_price=2.50,
    )
    assert leg.net_basis is None


def test_optionleg_effective_basis_uses_net_basis_when_set():
    leg = positions.OptionLeg(
        action="buy", kind="share", strike=None, expiry=None,
        qty=100, entry_price=100.0, net_basis=98.0,
    )
    assert leg.effective_basis() == 98.0


def test_optionleg_effective_basis_falls_back_to_entry_price():
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=100.0,
        expiry="2026-06-19", qty=1, entry_price=2.50,
    )
    assert leg.effective_basis() == 2.50


def test_open_position_inserts_position_and_legs(conn):
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=190.0,
        expiry="2026-06-19", qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL",
        intent="trade",
        structure_kind="long_call",
        legs=[leg],
        opened_ts=1_700_000_000,
        profit_target_price=200.0,
        stop_price=180.0,
        time_stop_dte=21,
        assignment_acceptable=False,
        nearest_leg_expiry_dte=30,
        rationale="bullish breakout above 50sma",
    )
    assert pos.id is not None
    assert pos.legs[0].id is not None
    assert pos.legs[0].position_id == pos.id

    row = conn.execute(
        "SELECT * FROM v2_positions WHERE id=?", (pos.id,)
    ).fetchone()
    assert row["ticker"] == "AAPL"
    assert row["intent"] == "trade"
    assert row["structure_kind"] == "long_call"
    assert row["profit_target_price"] == 200.0
    assert row["stop_price"] == 180.0
    assert row["time_stop_dte"] == 21
    assert row["assignment_acceptable"] == 0
    assert row["nearest_leg_expiry_dte"] == 30
    assert row["exit_plan_version"] == 1
    assert row["closed_ts"] is None
    assert row["rationale"] == "bullish breakout above 50sma"


def test_open_position_with_multi_leg_spread(conn):
    legs = [
        positions.OptionLeg(
            action="buy", kind="call", strike=190.0,
            expiry="2026-06-19", qty=1, entry_price=4.00,
        ),
        positions.OptionLeg(
            action="sell", kind="call", strike=200.0,
            expiry="2026-06-19", qty=1, entry_price=1.50,
        ),
    ]
    pos = positions.open_position(
        conn,
        ticker="AAPL",
        intent="trade",
        structure_kind="bull_call_spread",
        legs=legs,
        opened_ts=1_700_000_000,
        profit_target_price=200.0,
        stop_price=185.0,
        time_stop_dte=21,
        assignment_acceptable=False,
        nearest_leg_expiry_dte=30,
        rationale="defined-risk bull",
    )
    rows = conn.execute(
        "SELECT * FROM v2_position_legs WHERE position_id=? ORDER BY id",
        (pos.id,),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["action"] == "buy"
    assert rows[0]["strike"] == 190.0
    assert rows[1]["action"] == "sell"
    assert rows[1]["strike"] == 200.0


def test_load_position_round_trips_all_fields(conn):
    legs = [
        positions.OptionLeg(
            action="sell", kind="put", strike=180.0,
            expiry="2026-06-19", qty=1, entry_price=2.00,
        ),
    ]
    opened = positions.open_position(
        conn,
        ticker="AAPL", intent="accumulate", structure_kind="csp",
        legs=legs, opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=True,
        nearest_leg_expiry_dte=30,
        rationale="basis-lowering CSP",
    )
    loaded = positions.load_position(conn, opened.id)
    assert loaded.ticker == "AAPL"
    assert loaded.intent == "accumulate"
    assert loaded.structure_kind == "csp"
    assert loaded.assignment_acceptable is True
    assert loaded.profit_target_price is None
    assert len(loaded.legs) == 1
    assert loaded.legs[0].action == "sell"
    assert loaded.legs[0].kind == "put"
    assert loaded.legs[0].strike == 180.0
    assert loaded.legs[0].entry_price == 2.00


def test_load_position_returns_none_for_unknown_id(conn):
    assert positions.load_position(conn, 99999) is None


def test_open_position_rejects_empty_legs(conn):
    with pytest.raises(ValueError, match="at least one leg required"):
        positions.open_position(
            conn,
            ticker="AAPL", intent="trade", structure_kind="long_call",
            legs=[], opened_ts=1_700_000_000,
            profit_target_price=200.0, stop_price=180.0,
            time_stop_dte=21, assignment_acceptable=False,
            nearest_leg_expiry_dte=30, rationale="",
        )
