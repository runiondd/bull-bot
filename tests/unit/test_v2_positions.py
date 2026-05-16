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
