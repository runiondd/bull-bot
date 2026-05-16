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


from types import SimpleNamespace


def _bars(closes: list[float]) -> list[SimpleNamespace]:
    """Build a list of bar-shaped namespaces from a sequence of closes.
    Mirrors the shape that bullbot.v2.runner._load_bars produces."""
    return [
        SimpleNamespace(ts=1_700_000_000 + i * 86400,
                        open=c, high=c, low=c, close=c, volume=1_000_000.0)
        for i, c in enumerate(closes)
    ]


def test_iv_proxy_returns_realized_vol_when_regime_multiplier_is_one():
    """VIX flat at its 60-day median → multiplier = 1.0, IV proxy ≈ realized vol."""
    underlying_bars = _bars([100.0 * (1 + 0.01 * ((-1) ** i)) for i in range(60)])
    vix_bars = _bars([18.0] * 60)
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    # underlying alternates ±1% so realized vol ~ 16% annualized; should land
    # near that, well above the 0.05 floor.
    assert 0.05 < iv < 0.50


def _alternating_bars():
    """60 bars alternating ±1% — produces realized_vol ≈ 0.158 (annualized)."""
    return _bars([100.0 * (1 + 0.01 * ((-1) ** i)) for i in range(60)])


def test_iv_proxy_scales_up_when_vix_above_baseline():
    """Today's VIX = 30, 60-day median VIX = 15 → multiplier = 2.0,
    IV proxy = realized_vol * 2.0 (subject to the [0.05, 3.0] clamp)."""
    underlying_bars = _alternating_bars()  # rv ≈ 0.158
    flat_vix = chains._iv_proxy(underlying_bars=underlying_bars,
                                vix_bars=_bars([15.0] * 60))
    vix_bars = _bars([15.0] * 59 + [30.0])
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    # 2.0× regime multiplier vs the flat-VIX baseline
    assert iv == pytest.approx(2.0 * flat_vix, abs=0.01)


def test_iv_proxy_scales_down_when_vix_below_baseline():
    underlying_bars = _alternating_bars()
    flat_vix = chains._iv_proxy(underlying_bars=underlying_bars,
                                vix_bars=_bars([20.0] * 60))
    vix_bars = _bars([20.0] * 59 + [10.0])
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    # 0.5× regime multiplier vs the flat-VIX baseline
    assert iv == pytest.approx(0.5 * flat_vix, abs=0.01)


def test_iv_proxy_clamps_to_upper_bound_on_pathological_vix_spike():
    underlying_bars = _alternating_bars()
    vix_bars = _bars([10.0] * 59 + [200.0])  # 20× spike (impossible but test the clamp)
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    assert iv == 3.0


def test_iv_proxy_falls_back_to_default_when_underlying_bars_too_few():
    underlying_bars = _bars([100.0] * 5)  # < 31 bars → realized_vol returns its 0.30 default
    vix_bars = _bars([18.0] * 60)
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    assert iv == pytest.approx(0.30, abs=0.01)


def test_iv_proxy_returns_floor_when_underlying_bars_are_flat():
    """Flat closes → realized_vol = 0 → iv = 0 → clamped to IV_PROXY_MIN.
    Documents the actual behavior (not the original test's assumption that
    flat triggers the 0.30 default — that default only fires with too few bars)."""
    underlying_bars = _bars([100.0] * 60)
    vix_bars = _bars([18.0] * 60)
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    assert iv == chains.IV_PROXY_MIN


def test_iv_proxy_falls_back_to_default_when_vix_bars_too_few():
    underlying_bars = _alternating_bars()
    vix_bars = _bars([18.0] * 5)  # < 60 → can't compute regime multiplier
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    # Multiplier defaults to 1.0; result is the realized vol of the alternating pattern.
    assert 0.05 < iv < 1.0
