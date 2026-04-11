"""
Unit tests for bullbot/features/regime_agent.py.

TDD: written before the implementation.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from bullbot.db import migrations
from bullbot.features.regime_signals import MarketSignals, TickerSignals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_conn():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)
    return conn


def _sample_market_signals():
    return MarketSignals(
        vix_level=18.0,
        vix_percentile=35.0,
        vix_term_slope=1.02,
        spy_trend="up",
        spy_momentum=3.5,
        breadth_score=72.7,
        sector_momentum={"XLK": 5.2, "XLC": 3.1, "XLF": -1.0},
        risk_appetite="risk_on",
        realized_vs_implied=-4.5,
    )


def _sample_ticker_signals():
    return TickerSignals(
        ticker="AAPL",
        iv_rank=42.0,
        iv_percentile=55.0,
        sector_relative=1.2,
        vol_regime="moderate",
        sector_etf="XLK",
    )


# ---------------------------------------------------------------------------
# Fake Anthropic fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_anthropic():
    from tests.conftest import FakeAnthropicClient
    return FakeAnthropicClient()


# ---------------------------------------------------------------------------
# Test 1: synthesize_market_brief calls the LLM
# ---------------------------------------------------------------------------


def test_synthesize_market_brief_calls_llm(fake_anthropic):
    from bullbot.features import regime_agent
    from bullbot import config

    fake_anthropic.queue_response("Market is risk-on with low VIX. Recommend PutCreditSpread.")
    signals = _sample_market_signals()

    brief, cost = regime_agent.synthesize_market_brief(fake_anthropic, signals)

    assert "risk-on" in brief or len(brief) > 0
    assert len(fake_anthropic.call_log) == 1
    call = fake_anthropic.call_log[0]
    assert call["model"] == config.REGIME_SYNTHESIS_MODEL
    assert isinstance(cost, float)
    assert cost >= 0.0


# ---------------------------------------------------------------------------
# Test 2: synthesize_ticker_brief calls the LLM
# ---------------------------------------------------------------------------


def test_synthesize_ticker_brief_calls_llm(fake_anthropic):
    from bullbot.features import regime_agent
    from bullbot import config

    fake_anthropic.queue_response("AAPL shows moderate IV with positive sector relative strength.")
    signals = _sample_ticker_signals()
    market_brief = "Market is currently risk-on."

    brief, cost = regime_agent.synthesize_ticker_brief(fake_anthropic, signals, market_brief)

    assert len(brief) > 0
    assert len(fake_anthropic.call_log) == 1
    call = fake_anthropic.call_log[0]
    assert call["model"] == config.REGIME_SYNTHESIS_MODEL
    assert isinstance(cost, float)
    assert cost >= 0.0


# ---------------------------------------------------------------------------
# Test 3: refresh_market_brief caches — second call same ts → cache hit
# ---------------------------------------------------------------------------


def test_refresh_market_brief_caches(fake_anthropic):
    from bullbot.features import regime_agent

    conn = _fresh_conn()
    fake_anthropic.queue_response("Bullish regime, low VIX favors premium selling.")
    signals = _sample_market_signals()
    ts = 1_700_000_000

    # First call: LLM should be invoked
    brief1 = regime_agent.refresh_market_brief(conn, fake_anthropic, signals, ts)
    assert len(fake_anthropic.call_log) == 1

    # Second call with same ts: should hit cache, no additional LLM call
    brief2 = regime_agent.refresh_market_brief(conn, fake_anthropic, signals, ts)
    assert len(fake_anthropic.call_log) == 1  # still just 1

    assert brief1 == brief2


# ---------------------------------------------------------------------------
# Test 4: refresh_market_brief stores row in regime_briefs
# ---------------------------------------------------------------------------


def test_refresh_market_brief_stores_in_db(fake_anthropic):
    from bullbot.features import regime_agent

    conn = _fresh_conn()
    fake_anthropic.queue_response("Moderate volatility environment. SPY trend is up.")
    signals = _sample_market_signals()
    ts = 1_700_000_100

    regime_agent.refresh_market_brief(conn, fake_anthropic, signals, ts)

    row = conn.execute(
        "SELECT * FROM regime_briefs WHERE scope='market' AND ts=?", (ts,)
    ).fetchone()

    assert row is not None
    assert row["source"] == "llm"
    # signals_json should be valid JSON matching the dataclass
    stored = json.loads(row["signals_json"])
    assert stored["vix_level"] == 18.0
    assert stored["spy_trend"] == "up"


# ---------------------------------------------------------------------------
# Test 5: fallback on LLM failure — RuntimeError → template + cost=0.0
# ---------------------------------------------------------------------------


class _AlwaysFailClient:
    """Fake Anthropic client that always raises RuntimeError on messages.create."""

    class _Messages:
        def create(self, **kwargs):
            raise RuntimeError("LLM unavailable")

    def __init__(self):
        self.messages = self._Messages()


def test_fallback_on_llm_failure():
    from bullbot.features import regime_agent

    client = _AlwaysFailClient()
    signals = _sample_market_signals()

    brief, cost = regime_agent.synthesize_market_brief(client, signals)

    assert isinstance(brief, str)
    assert len(brief) > 0
    assert cost == 0.0
