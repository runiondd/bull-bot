"""Unit tests for bullbot.v2.chains — Yahoo + BS pricing layer."""
from __future__ import annotations

import sqlite3
from datetime import date

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


def test_chainquote_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be one of"):
        chains.ChainQuote(
            expiry="2026-06-19", strike=100.0, kind="future",
            bid=1.0, ask=1.2, last=1.1, iv=0.30, oi=100, source="yahoo",
        )


def test_chainquote_rejects_unknown_source():
    with pytest.raises(ValueError, match="source must be one of"):
        chains.ChainQuote(
            expiry="2026-06-19", strike=100.0, kind="call",
            bid=1.0, ask=1.2, last=1.1, iv=0.30, oi=100, source="polygon",
        )


def test_chainquote_mid_price_returns_bid_ask_midpoint():
    q = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=1.00, ask=1.20, last=None, iv=0.30, oi=100, source="yahoo",
    )
    assert q.mid_price() == pytest.approx(1.10)


def test_chainquote_mid_price_falls_back_to_last_when_bid_or_ask_missing():
    q = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=None, ask=None, last=1.15, iv=0.30, oi=100, source="yahoo",
    )
    assert q.mid_price() == 1.15


def test_chainquote_mid_price_returns_none_when_no_prices_available():
    q = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=None, ask=None, last=None, iv=0.30, oi=100, source="yahoo",
    )
    assert q.mid_price() is None


def test_chain_empty_quotes_is_valid():
    c = chains.Chain(ticker="AAPL", asof_ts=1_700_000_000, quotes=[])
    assert c.ticker == "AAPL"
    assert c.quotes == []


def test_chain_find_quote_returns_matching_strike_and_kind():
    q1 = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=1.0, ask=1.2, last=1.1, iv=0.30, oi=100, source="yahoo",
    )
    q2 = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="put",
        bid=0.8, ask=1.0, last=0.9, iv=0.32, oi=80, source="yahoo",
    )
    c = chains.Chain(ticker="AAPL", asof_ts=1_700_000_000, quotes=[q1, q2])
    assert c.find_quote(expiry="2026-06-19", strike=100.0, kind="call") is q1
    assert c.find_quote(expiry="2026-06-19", strike=100.0, kind="put") is q2
    assert c.find_quote(expiry="2026-06-19", strike=105.0, kind="call") is None
