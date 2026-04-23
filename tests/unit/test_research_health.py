"""Unit tests for bullbot.research.health."""
from __future__ import annotations

import sqlite3
import time

import pytest

from bullbot import config
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


# --- check_pf_inf ------------------------------------------------------------


def _make_conn_with_ticker_state() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE ticker_state (
            id INTEGER PRIMARY KEY,
            ticker TEXT UNIQUE,
            phase TEXT,
            iteration_count INTEGER DEFAULT 0,
            plateau_counter INTEGER DEFAULT 0,
            best_strategy_id INTEGER,
            best_pf_is REAL,
            best_pf_oos REAL,
            cumulative_llm_usd REAL DEFAULT 0,
            paper_started_at INTEGER,
            paper_trade_count INTEGER DEFAULT 0,
            live_started_at INTEGER,
            verdict_at INTEGER,
            retired INTEGER DEFAULT 0,
            updated_at INTEGER
        )
    """)
    return c


def test_check_pf_inf_passes_when_all_pf_values_reasonable():
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('SPY', 'paper_trial', 1.8, 10, 0)"
    )
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('QQQ', 'discovering', NULL, NULL, 0)"
    )
    result = H.check_pf_inf(conn)
    assert result.passed is True
    assert result.findings == []


def test_check_pf_inf_flags_infinite_and_absurd_pf_values():
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('AAPL', 'no_edge', ?, 123, 0)", (float("inf"),),
    )
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('TSLA', 'paper_trial', 1e12, 114, 0)"
    )
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('MSFT', 'discovering', 2.5, 99, 0)"
    )
    result = H.check_pf_inf(conn)
    assert result.passed is False
    assert len(result.findings) == 2
    assert any("AAPL" in f and "inf" in f and "123" in f for f in result.findings)
    assert any("TSLA" in f and "114" in f for f in result.findings)
    # MSFT's pf_oos=2.5 is reasonable, should not be flagged
    assert not any("MSFT" in f for f in result.findings)


# --- check_dead_paper_trials -------------------------------------------------


def test_check_dead_paper_trials_passes_when_all_healthy(monkeypatch):
    monkeypatch.setattr(config, "HEALTH_DEAD_PAPER_DAYS", 3)
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    # freshly promoted, not yet past threshold
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, verdict_at, updated_at) "
        "VALUES ('GOOGL', 'paper_trial', NULL, 0, ?, ?)",
        (now - 1 * 86400, now),
    )
    # actively trading
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, updated_at) "
        "VALUES ('SPY', 'paper_trial', ?, 5, ?)",
        (now - 10 * 86400, now),
    )
    result = H.check_dead_paper_trials(conn, now=now)
    assert result.passed is True


def test_check_dead_paper_trials_flags_never_started(monkeypatch):
    monkeypatch.setattr(config, "HEALTH_DEAD_PAPER_DAYS", 3)
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, verdict_at, updated_at) "
        "VALUES ('SATS', 'paper_trial', NULL, 0, ?, ?)",
        (now - 5 * 86400, now),
    )
    result = H.check_dead_paper_trials(conn, now=now)
    assert result.passed is False
    assert len(result.findings) == 1
    assert "SATS" in result.findings[0]
    assert "never fired" in result.findings[0] or "never started" in result.findings[0]


def test_check_dead_paper_trials_flags_zero_trades_after_threshold(monkeypatch):
    monkeypatch.setattr(config, "HEALTH_DEAD_PAPER_DAYS", 3)
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, updated_at) "
        "VALUES ('XLF', 'paper_trial', ?, 0, ?)",
        (now - 5 * 86400, now),
    )
    result = H.check_dead_paper_trials(conn, now=now)
    assert result.passed is False
    assert len(result.findings) == 1
    assert "XLF" in result.findings[0]
    assert "0 live trades" in result.findings[0] or "0 trades" in result.findings[0]
