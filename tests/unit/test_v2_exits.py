"""Unit tests for bullbot.v2.exits — deterministic exit-rule evaluator."""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.v2 import exits, positions
from bullbot.v2.signals import DirectionalSignal


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def test_exitaction_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be one of"):
        exits.ExitAction(kind="explode", reason="boom")


def test_exitaction_defaults_reason_to_empty_string():
    action = exits.ExitAction(kind="hold")
    assert action.reason == ""
    assert action.linked_position_id is None


def test_exitaction_carries_linked_position_id_for_assignment():
    action = exits.ExitAction(
        kind="assigned_to_shares", reason="CSP ITM at expiry",
        linked_position_id=42,
    )
    assert action.linked_position_id == 42


def test_action_kinds_constant_includes_all_trade_and_accumulate_outcomes():
    expected = {
        "hold",
        "closed_profit_target", "closed_stop", "closed_signal_flip",
        "closed_time_stop", "closed_credit_profit_take", "closed_safety_stop",
        "assigned_to_shares", "called_away", "exercised_to_shares",
        "expired_worthless",
    }
    assert set(exits.ACTION_KINDS) == expected


def _share_position(conn, qty=100, entry_price=100.0, net_basis=None,
                    intent="trade", structure_kind="long_shares",
                    profit_target_price=None, stop_price=None,
                    time_stop_dte=None, nearest_leg_expiry_dte=None,
                    rationale="", ticker="AAPL"):
    leg = positions.OptionLeg(
        action="buy", kind="share", strike=None, expiry=None,
        qty=qty, entry_price=entry_price, net_basis=net_basis,
    )
    return positions.open_position(
        conn,
        ticker=ticker, intent=intent, structure_kind=structure_kind,
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=profit_target_price, stop_price=stop_price,
        time_stop_dte=time_stop_dte,
        assignment_acceptable=(intent == "accumulate"),
        nearest_leg_expiry_dte=nearest_leg_expiry_dte,
        rationale=rationale,
    )


def test_position_pnl_pct_uses_entry_price_when_net_basis_is_none(conn):
    pos = _share_position(conn, qty=100, entry_price=100.0, net_basis=None)
    pct = exits._position_pnl_pct(position=pos, spot=95.0)
    assert pct == pytest.approx(-0.05)


def test_position_pnl_pct_uses_net_basis_when_set(conn):
    """Grok Tier 1 Finding 1: assigned shares carry net_basis (lower than
    strike). P&L must compute against net_basis, not entry_price."""
    pos = _share_position(conn, qty=100, entry_price=100.0, net_basis=98.0)
    pct = exits._position_pnl_pct(position=pos, spot=92.0)
    assert pct == pytest.approx((92.0 - 98.0) / 98.0)


def test_position_pnl_pct_for_short_shares_inverts_sign(conn):
    leg = positions.OptionLeg(
        action="sell", kind="share", strike=None, expiry=None,
        qty=100, entry_price=100.0,
    )
    short_pos = positions.open_position(
        conn,
        ticker="MSFT", intent="trade", structure_kind="short_shares",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=False,
        nearest_leg_expiry_dte=None, rationale="",
    )
    pct = exits._position_pnl_pct(position=short_pos, spot=110.0)
    assert pct == pytest.approx(-0.10)


def test_position_pnl_pct_returns_zero_for_non_share_position(conn):
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=100.0, expiry="2026-06-19",
        qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="long_call",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=False,
        nearest_leg_expiry_dte=30, rationale="",
    )
    pct = exits._position_pnl_pct(position=pos, spot=95.0)
    assert pct == 0.0
