"""Unit tests for bullbot.v2.runner_c."""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.v2 import runner_c


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    # Seed a parent position so FK (position_id → v2_positions.id) is satisfied.
    c.execute(
        "INSERT INTO v2_positions (id, ticker, intent, structure_kind, opened_ts) "
        "VALUES (1, 'SPY', 'trade', 'long_call', 1700000000)"
    )
    c.commit()
    return c


def test_write_position_mtm_inserts_row(conn):
    runner_c._write_position_mtm(
        conn, position_id=1, asof_ts=1_700_000_000,
        mtm_value=1234.56, source="bs",
    )
    row = conn.execute(
        "SELECT position_id, asof_ts, mtm_value, source FROM v2_position_mtm"
    ).fetchone()
    assert row["position_id"] == 1
    assert row["asof_ts"] == 1_700_000_000
    assert row["mtm_value"] == 1234.56
    assert row["source"] == "bs"


def test_write_position_mtm_is_idempotent_on_pk(conn):
    runner_c._write_position_mtm(
        conn, position_id=1, asof_ts=1_700_000_000,
        mtm_value=100.0, source="yahoo",
    )
    runner_c._write_position_mtm(
        conn, position_id=1, asof_ts=1_700_000_000,
        mtm_value=200.0, source="bs",
    )
    rows = conn.execute("SELECT mtm_value, source FROM v2_position_mtm").fetchall()
    assert len(rows) == 1
    assert rows[0]["mtm_value"] == 200.0
    assert rows[0]["source"] == "bs"


from datetime import date
from types import SimpleNamespace


def _seed_bars(conn, ticker, asof_ts, n=60, base_close=100.0):
    for i in range(n):
        ts = asof_ts - (n - 1 - i) * 86400
        c = base_close + (i * 0.01)
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, 1_000_000)",
            (ticker, ts, c, c + 0.3, c - 0.3, c),
        )
    conn.commit()


def _stub_signal_fn(bars, ticker, asof_ts):
    from bullbot.v2.signals import DirectionalSignal
    return DirectionalSignal(
        ticker=ticker, asof_ts=asof_ts, direction="bullish",
        confidence=0.7, horizon_days=30, rationale="stub",
        rules_version="stub",
    )


def _stub_chain_fn(conn, ticker, asof_ts, spot):
    from bullbot.v2.chains import Chain
    return Chain(ticker=ticker, asof_ts=asof_ts, quotes=[])


def test_dispatch_ticker_returns_skipped_when_no_bars(conn, fake_anthropic):
    out = runner_c._dispatch_ticker(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        nav=50_000.0, signal_fn=_stub_signal_fn, chain_fn=_stub_chain_fn,
        llm_client=fake_anthropic,
    )
    assert out == "skipped"


def test_dispatch_ticker_returns_pass_on_llm_pass(conn, fake_anthropic):
    import json
    asof = 1_700_000_000
    _seed_bars(conn, "AAPL", asof, n=60)
    fake_anthropic.queue_response(json.dumps({
        "decision": "pass", "intent": "trade", "structure": "long_call",
        "legs": [], "exit_plan": {}, "rationale": "no edge",
    }))
    out = runner_c._dispatch_ticker(
        conn=conn, ticker="AAPL", asof_ts=asof,
        nav=50_000.0, signal_fn=_stub_signal_fn, chain_fn=_stub_chain_fn,
        llm_client=fake_anthropic,
    )
    assert out == "pass"


def test_run_once_phase_c_skips_when_universe_has_no_bars(conn, fake_anthropic, monkeypatch):
    """No bars for any UNIVERSE ticker → all skipped."""
    monkeypatch.setattr("bullbot.config.UNIVERSE", ["AAPL", "MSFT"])
    counts = runner_c.run_once_phase_c(
        conn=conn, asof_ts=1_700_000_000,
        signal_fn=_stub_signal_fn, chain_fn=_stub_chain_fn,
        llm_client=fake_anthropic,
    )
    assert counts == {"skipped": 2}


def test_run_once_phase_c_counts_actions_per_ticker(conn, fake_anthropic, monkeypatch):
    import json
    asof = 1_700_000_000
    monkeypatch.setattr("bullbot.config.UNIVERSE", ["AAPL"])
    _seed_bars(conn, "AAPL", asof, n=60)
    fake_anthropic.queue_response(json.dumps({
        "decision": "pass", "intent": "trade", "structure": "long_call",
        "legs": [], "exit_plan": {}, "rationale": "no edge",
    }))
    counts = runner_c.run_once_phase_c(
        conn=conn, asof_ts=asof,
        signal_fn=_stub_signal_fn, chain_fn=_stub_chain_fn,
        llm_client=fake_anthropic,
    )
    assert counts == {"pass": 1}


def test_run_once_phase_c_counts_error_when_dispatch_raises(conn, fake_anthropic, monkeypatch):
    """If _dispatch_ticker raises, count as 'error' and continue to next ticker."""
    monkeypatch.setattr("bullbot.config.UNIVERSE", ["AAPL"])
    _seed_bars(conn, "AAPL", 1_700_000_000, n=60)

    def boom_signal_fn(bars, ticker, asof_ts):
        raise RuntimeError("boom")

    counts = runner_c.run_once_phase_c(
        conn=conn, asof_ts=1_700_000_000,
        signal_fn=boom_signal_fn, chain_fn=_stub_chain_fn,
        llm_client=fake_anthropic,
    )
    assert counts == {"error": 1}
