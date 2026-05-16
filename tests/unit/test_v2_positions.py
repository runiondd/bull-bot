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


def _open_simple(conn, ticker="AAPL", intent="trade", structure_kind="long_call"):
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=190.0,
        expiry="2026-06-19", qty=1, entry_price=2.50,
    )
    return positions.open_position(
        conn,
        ticker=ticker, intent=intent, structure_kind=structure_kind,
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=200.0, stop_price=180.0,
        time_stop_dte=21, assignment_acceptable=(intent == "accumulate"),
        nearest_leg_expiry_dte=30, rationale="t",
    )


def test_open_for_ticker_returns_open_position(conn):
    pos = _open_simple(conn, ticker="AAPL")
    found = positions.open_for_ticker(conn, "AAPL")
    assert found is not None
    assert found.id == pos.id


def test_open_for_ticker_returns_none_when_flat(conn):
    assert positions.open_for_ticker(conn, "AAPL") is None


def test_open_for_ticker_ignores_closed_positions(conn):
    pos = _open_simple(conn, ticker="AAPL")
    positions.close_position(
        conn, position_id=pos.id, closed_ts=1_700_001_000,
        close_reason="profit_target",
        leg_exit_prices={pos.legs[0].id: 5.00},
    )
    assert positions.open_for_ticker(conn, "AAPL") is None


def test_open_count_counts_only_open(conn):
    _open_simple(conn, ticker="AAPL")
    _open_simple(conn, ticker="MSFT")
    closed = _open_simple(conn, ticker="GOOG")
    positions.close_position(
        conn, position_id=closed.id, closed_ts=1_700_001_000,
        close_reason="stop", leg_exit_prices={closed.legs[0].id: 0.50},
    )
    assert positions.open_count(conn) == 2


def test_close_position_persists_exit_fields(conn):
    pos = _open_simple(conn, ticker="AAPL")
    positions.close_position(
        conn, position_id=pos.id, closed_ts=1_700_001_000,
        close_reason="profit_target",
        leg_exit_prices={pos.legs[0].id: 5.00},
    )
    reloaded = positions.load_position(conn, pos.id)
    assert reloaded.closed_ts == 1_700_001_000
    assert reloaded.close_reason == "profit_target"
    assert reloaded.legs[0].exit_price == 5.00


def test_close_position_rejects_unknown_close_reason(conn):
    pos = _open_simple(conn, ticker="AAPL")
    with pytest.raises(ValueError, match="close_reason must be one of"):
        positions.close_position(
            conn, position_id=pos.id, closed_ts=1_700_001_000,
            close_reason="for_fun",
            leg_exit_prices={pos.legs[0].id: 5.00},
        )
