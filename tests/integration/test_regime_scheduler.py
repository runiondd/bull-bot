import json
import sqlite3
import time

from bullbot.db import migrations
from tests.conftest import FakeAnthropicClient, FakeUWClient


def _fresh_conn():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)
    return conn


def _seed_bars(conn, ticker, n=252, base_ts=1700000000):
    for i in range(n):
        ts = base_ts + i * 86400
        conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, 100.0, 101.0, 99.0, ?, 1000000)",
            (ticker, ts, 100.0 + i * 0.1),
        )


def test_scheduler_tick_calls_regime_refresh():
    """Scheduler tick should refresh regime briefs before running evolver."""
    conn = _fresh_conn()
    fake_anthropic = FakeAnthropicClient()
    fake_uw = FakeUWClient()

    # Seed bars for regime data tickers + SPY
    for ticker in ["VIX", "SPY", "XLK", "XLF", "XLE", "XLV", "XLI",
                    "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB", "TLT", "HYG"]:
        _seed_bars(conn, ticker)

    # Queue LLM responses: market brief + ticker brief + proposer response
    fake_anthropic.queue_response("Bull regime. Favors PutCreditSpread.")
    fake_anthropic.queue_response("SPY: short puts favorable.")
    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 30, "short_delta": 0.25, "width": 5},
        "rationale": "test",
    }))

    from bullbot import scheduler
    scheduler.tick(conn, fake_anthropic, fake_uw, universe=["SPY"])

    # Verify regime_briefs were created
    rows = conn.execute("SELECT * FROM regime_briefs").fetchall()
    assert len(rows) >= 1
    scopes = {r["scope"] for r in rows}
    assert "market" in scopes


def test_scheduler_tick_skips_regime_on_insufficient_data():
    """If no bars exist for regime tickers, scheduler should still run evolver."""
    conn = _fresh_conn()
    fake_anthropic = FakeAnthropicClient()
    fake_uw = FakeUWClient()

    # Only seed SPY bars (no regime data tickers)
    _seed_bars(conn, "SPY")

    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 30, "short_delta": 0.25, "width": 5},
        "rationale": "test",
    }))

    from bullbot import scheduler
    # Should not crash
    scheduler.tick(conn, fake_anthropic, fake_uw, universe=["SPY"])
