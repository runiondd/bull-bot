"""End-to-end integration test for v2 chains module.

Wires together fetch_chain → v2_chain_snapshots → price_leg to confirm the
full forward-mode flow works as a unit. Uses mocked yfinance (no network).
"""
from __future__ import annotations

import sqlite3
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from bullbot.db.migrations import apply_schema
from bullbot.v2 import chains
from bullbot.v2.positions import OptionLeg


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def _seed_bars(conn, ticker, ts, close):
    conn.execute(
        "INSERT OR REPLACE INTO bars "
        "(ticker, timeframe, ts, open, high, low, close, volume) "
        "VALUES (?, '1d', ?, ?, ?, ?, ?, 1000000)",
        (ticker, ts, close, close, close, close),
    )


def test_full_flow_fetch_then_price_cache_hit_and_cache_miss(conn):
    """1. Seed 60 bars for AAPL and VIX so the IV proxy has data.
    2. fetch_chain pulls a stubbed Yahoo chain at strikes 95/100/105.
    3. price_leg on the cached 100-call returns yahoo mid.
    4. price_leg on a 200-call (not in the chain) falls back to BS."""

    asof = 1_700_000_000
    for i in range(60):
        _seed_bars(conn, "AAPL", asof - (60 - i) * 86400, 100.0)
        _seed_bars(conn, "VIX", asof - (60 - i) * 86400, 18.0)
    conn.commit()

    calls = pd.DataFrame([
        {"strike": 95.0, "bid": 6.10, "ask": 6.30, "lastPrice": 6.20,
         "impliedVolatility": 0.32, "openInterest": 420},
        {"strike": 100.0, "bid": 3.20, "ask": 3.40, "lastPrice": 3.30,
         "impliedVolatility": 0.30, "openInterest": 1850},
        {"strike": 105.0, "bid": 1.40, "ask": 1.55, "lastPrice": 1.47,
         "impliedVolatility": 0.29, "openInterest": 730},
    ])
    puts = pd.DataFrame([])

    class FakeTicker:
        options = ["2026-06-19"]
        def option_chain(self, expiry):
            return SimpleNamespace(calls=calls, puts=puts)

    result = chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=asof,
        client=lambda symbol: FakeTicker(),
    )
    assert result is not None
    assert len(result.quotes) == 3

    # 1. Cache hit at 100 call.
    leg_cached = OptionLeg(
        action="buy", kind="call", strike=100.0,
        expiry="2026-06-19", qty=1, entry_price=0.0,
    )
    price_cached, source_cached = chains.price_leg(
        conn=conn, ticker="AAPL", leg=leg_cached, spot=100.0,
        today=date(2026, 5, 17), asof_ts=asof,
    )
    assert source_cached == "yahoo"
    assert price_cached == pytest.approx(3.30)

    # 2. Cache miss at 200 call — BS fallback.
    leg_uncached = OptionLeg(
        action="buy", kind="call", strike=200.0,
        expiry="2026-06-19", qty=1, entry_price=0.0,
    )
    price_uncached, source_uncached = chains.price_leg(
        conn=conn, ticker="AAPL", leg=leg_uncached, spot=100.0,
        today=date(2026, 5, 17), asof_ts=asof,
    )
    assert source_uncached == "bs"
    # 200 strike on a 100-spot, ~1mo DTE, low vol → deep OTM, near-zero price
    assert price_uncached < 0.10
