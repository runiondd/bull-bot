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
