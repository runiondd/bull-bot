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


# --- check_data_shortfalls --------------------------------------------------

from bullbot import config


def _make_conn_with_bars(bars_by_ticker: dict[str, int]) -> sqlite3.Connection:
    """Minimal DB with a bars table populated by per-ticker row count."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE bars (
            ticker TEXT, timeframe TEXT, ts INTEGER,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER
        )
    """)
    for ticker, n in bars_by_ticker.items():
        for i in range(n):
            c.execute(
                "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
                "VALUES (?, '1d', ?, 100, 101, 99, 100, 0)",
                (ticker, i),
            )
    return c


def test_check_data_shortfalls_passes_when_all_tickers_have_enough_bars(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY", "QQQ"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 10)
    conn = _make_conn_with_bars({"SPY": 50, "QQQ": 20})
    result = H.check_data_shortfalls(conn)
    assert result.passed is True
    assert result.findings == []


def test_check_data_shortfalls_flags_under_threshold_tickers(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY", "XLK", "HYG"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 500)
    conn = _make_conn_with_bars({"SPY": 1000, "XLK": 257, "HYG": 257})
    result = H.check_data_shortfalls(conn)
    assert result.passed is False
    assert len(result.findings) == 2
    assert any("XLK" in f and "257" in f and "500" in f for f in result.findings)
    assert any("HYG" in f for f in result.findings)
    # SPY passes, so no finding for it
    assert not any("SPY" in f for f in result.findings)
