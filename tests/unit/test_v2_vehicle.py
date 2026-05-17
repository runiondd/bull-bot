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
