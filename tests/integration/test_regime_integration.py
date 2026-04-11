"""
End-to-end test: regime refresh populates briefs, cache dedup works, cost tracked.
"""

import json
import sqlite3

import pytest

from bullbot.db import migrations
from bullbot.features import regime_agent, regime_signals
from bullbot import config
from tests.conftest import FakeAnthropicClient


def _fresh_conn():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)
    return conn


def _seed_bars(conn, ticker, n=252, base_ts=1700000000, start_price=100.0):
    for i in range(n):
        ts = base_ts + i * 86400
        p = start_price + i * 0.1
        conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, 1000000)",
            (ticker, ts, p, p + 1, p - 1, p),
        )


def test_full_regime_refresh_and_cache_dedup():
    """Regime refresh produces briefs; second call reuses cache."""
    conn = _fresh_conn()
    client = FakeAnthropicClient()
    ts = 1700000000 - (1700000000 % 86400)

    # Seed all regime data tickers
    for ticker in config.REGIME_DATA_TICKERS:
        _seed_bars(conn, ticker)
    _seed_bars(conn, "SPY", start_price=400.0)

    # Market signals
    vix_bars = [dict(r) for r in reversed(conn.execute(
        "SELECT * FROM bars WHERE ticker='VIX' ORDER BY ts DESC LIMIT 252"
    ).fetchall())]
    spy_bars = [dict(r) for r in reversed(conn.execute(
        "SELECT * FROM bars WHERE ticker='SPY' ORDER BY ts DESC LIMIT 252"
    ).fetchall())]
    sector_bars = {}
    for etf in config.SECTOR_ETFS:
        rows = conn.execute(
            "SELECT * FROM bars WHERE ticker=? ORDER BY ts DESC LIMIT 252", (etf,)
        ).fetchall()
        sector_bars[etf] = [dict(r) for r in reversed(rows)]
    hyg_bars = [dict(r) for r in reversed(conn.execute(
        "SELECT * FROM bars WHERE ticker='HYG' ORDER BY ts DESC LIMIT 252"
    ).fetchall())]
    tlt_bars = [dict(r) for r in reversed(conn.execute(
        "SELECT * FROM bars WHERE ticker='TLT' ORDER BY ts DESC LIMIT 252"
    ).fetchall())]

    signals = regime_signals.compute_market_signals(
        vix_bars=vix_bars, spy_bars=spy_bars, sector_bars=sector_bars,
        hyg_bars=hyg_bars, tlt_bars=tlt_bars,
    )
    assert signals is not None

    # First refresh → LLM call
    client.queue_response("Bull regime. PutCreditSpread favored.")
    brief1 = regime_agent.refresh_market_brief(conn, client, signals, ts)
    assert brief1 == "Bull regime. PutCreditSpread favored."
    assert len(client.call_log) == 1

    # Second refresh → cache hit
    brief2 = regime_agent.refresh_market_brief(conn, client, signals, ts)
    assert brief2 == brief1
    assert len(client.call_log) == 1  # No new LLM call

    # Verify DB row
    row = conn.execute("SELECT * FROM regime_briefs WHERE scope='market'").fetchone()
    assert row is not None
    assert row["source"] == "llm"
    assert json.loads(row["signals_json"])["vix_level"] == signals.vix_level


def test_regime_cost_tracked_in_cost_ledger():
    """Regime agent LLM calls should be logged in cost_ledger."""
    conn = _fresh_conn()
    client = FakeAnthropicClient()
    ts = 1700000000

    signals = regime_signals.MarketSignals(
        vix_level=20.0, vix_percentile=50.0, vix_term_slope=1.0,
        spy_trend="flat", spy_momentum=0.0, breadth_score=50.0,
        sector_momentum={}, risk_appetite="neutral", realized_vs_implied=0.0,
    )

    client.queue_response("Neutral regime.")
    regime_agent.refresh_market_brief(conn, client, signals, ts)

    rows = conn.execute(
        "SELECT * FROM cost_ledger WHERE category='llm'"
    ).fetchall()
    assert len(rows) >= 1
    details = json.loads(rows[-1]["details"])
    assert details["source"] == "regime_agent"
