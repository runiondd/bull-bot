"""Unit tests for bullbot.research.health."""
from __future__ import annotations

import sqlite3
import time

import pytest

from bullbot.research import health as H


# --- Dataclasses ------------------------------------------------------------

def test_check_result_is_frozen():
    r = H.CheckResult(title="X", passed=True, findings=[])
    with pytest.raises(Exception):  # FrozenInstanceError subclass of AttributeError
        r.title = "Y"


def test_check_result_findings_empty_when_passed():
    # Convention, not a hard constraint, but most call sites assume this.
    r = H.CheckResult(title="X", passed=True, findings=[])
    assert r.passed is True
    assert r.findings == []


def test_health_brief_holds_structured_state():
    brief = H.HealthBrief(
        generated_at=1_700_000_000,
        header={"Universe": "16 tickers"},
        results=[H.CheckResult(title="X", passed=True, findings=[])],
    )
    assert brief.generated_at == 1_700_000_000
    assert brief.header["Universe"] == "16 tickers"
    assert len(brief.results) == 1


# --- _safe_check ------------------------------------------------------------

def test_safe_check_returns_result_from_healthy_fn():
    def clean(conn):
        return H.CheckResult(title="clean", passed=True, findings=[])
    result = H._safe_check(clean, conn=None)
    assert result.title == "clean"
    assert result.passed is True


def test_safe_check_converts_exception_to_findings():
    def boom(conn):
        raise ValueError("explicit failure")
    result = H._safe_check(boom, conn=None)
    assert result.title == "boom"
    assert result.passed is False
    assert any("ValueError" in f and "explicit failure" in f for f in result.findings)
