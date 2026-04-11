"""Unit tests for bullbot.features.regime_signals."""
from __future__ import annotations

import math

import pytest

from bullbot.features.regime_signals import (
    MarketSignals,
    TickerSignals,
    compute_market_signals,
    compute_ticker_signals,
)


def _make_bars_rows(closes, ticker="VIX"):
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            "ticker": ticker, "timeframe": "1d", "ts": 1000 + i * 86400,
            "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1000000,
        })
    return rows


def _make_sector_bars(n_bars=252, rising=True):
    """Build sector_bars dict for all 11 SECTOR_ETFS."""
    from bullbot import config
    sector_bars = {}
    for etf in config.SECTOR_ETFS:
        if rising:
            closes = [100.0 + i * 0.5 for i in range(n_bars)]
        else:
            closes = [100.0] * n_bars
        sector_bars[etf] = _make_bars_rows(closes, ticker=etf)
    return sector_bars


# ---------------------------------------------------------------------------
# MarketSignals tests
# ---------------------------------------------------------------------------

class TestMarketSignalsVixPercentile:
    def test_vix_percentile_in_range(self):
        """VIX linear 10→30 over 252 bars; latest is 30, percentile should be ~100."""
        vix_closes = [10.0 + 20.0 * i / 251 for i in range(252)]
        vix_bars = _make_bars_rows(vix_closes, ticker="VIX")
        spy_closes = [400.0 + i * 0.1 for i in range(252)]
        spy_bars = _make_bars_rows(spy_closes, ticker="SPY")
        hyg_bars = _make_bars_rows([80.0] * 252, ticker="HYG")
        tlt_bars = _make_bars_rows([100.0] * 252, ticker="TLT")
        sector_bars = _make_sector_bars(252)

        result = compute_market_signals(vix_bars, spy_bars, sector_bars, hyg_bars, tlt_bars)
        assert result is not None
        assert isinstance(result, MarketSignals)
        assert 50.0 <= result.vix_percentile <= 100.0


class TestMarketSignalsBreadth:
    def test_all_sectors_above_sma50_breadth_100(self):
        """All sector ETFs monotonically rising → all above 50-SMA → breadth=100."""
        vix_bars = _make_bars_rows([20.0] * 252, ticker="VIX")
        spy_closes = [400.0 + i * 0.5 for i in range(252)]
        spy_bars = _make_bars_rows(spy_closes, ticker="SPY")
        hyg_bars = _make_bars_rows([80.0] * 252, ticker="HYG")
        tlt_bars = _make_bars_rows([100.0] * 252, ticker="TLT")
        sector_bars = _make_sector_bars(252, rising=True)

        result = compute_market_signals(vix_bars, spy_bars, sector_bars, hyg_bars, tlt_bars)
        assert result is not None
        assert result.breadth_score == 100.0


class TestMarketSignalsSpyTrend:
    def test_spy_trend_up(self):
        """SPY monotonically rising over 252 bars → SMA50 > SMA200 → trend='up'."""
        vix_bars = _make_bars_rows([15.0] * 252, ticker="VIX")
        spy_closes = [300.0 + i * 1.0 for i in range(252)]
        spy_bars = _make_bars_rows(spy_closes, ticker="SPY")
        hyg_bars = _make_bars_rows([80.0] * 252, ticker="HYG")
        tlt_bars = _make_bars_rows([100.0] * 252, ticker="TLT")
        sector_bars = _make_sector_bars(252, rising=True)

        result = compute_market_signals(vix_bars, spy_bars, sector_bars, hyg_bars, tlt_bars)
        assert result is not None
        assert result.spy_trend == "up"


class TestMarketSignalsRiskAppetite:
    def test_hyg_rising_tlt_falling_risk_on(self):
        """HYG rising + TLT falling → HYG/TLT ratio rises → risk_on."""
        vix_bars = _make_bars_rows([15.0] * 252, ticker="VIX")
        spy_closes = [400.0 + i * 0.1 for i in range(252)]
        spy_bars = _make_bars_rows(spy_closes, ticker="SPY")
        hyg_closes = [80.0 + i * 0.1 for i in range(252)]   # HYG rising
        tlt_closes = [120.0 - i * 0.1 for i in range(252)]  # TLT falling
        hyg_bars = _make_bars_rows(hyg_closes, ticker="HYG")
        tlt_bars = _make_bars_rows(tlt_closes, ticker="TLT")
        sector_bars = _make_sector_bars(252)

        result = compute_market_signals(vix_bars, spy_bars, sector_bars, hyg_bars, tlt_bars)
        assert result is not None
        assert result.risk_appetite == "risk_on"


class TestMarketSignalsInsufficientData:
    def test_insufficient_data_returns_none(self):
        """Fewer than 60 bars → None."""
        vix_bars = _make_bars_rows([20.0] * 30, ticker="VIX")
        spy_bars = _make_bars_rows([400.0] * 30, ticker="SPY")
        hyg_bars = _make_bars_rows([80.0] * 30, ticker="HYG")
        tlt_bars = _make_bars_rows([100.0] * 30, ticker="TLT")
        sector_bars = _make_sector_bars(30, rising=True)

        result = compute_market_signals(vix_bars, spy_bars, sector_bars, hyg_bars, tlt_bars)
        assert result is None


# ---------------------------------------------------------------------------
# TickerSignals tests
# ---------------------------------------------------------------------------

class TestTickerSignalsBasic:
    def test_iv_rank_above_50_when_current_high(self):
        """Current IV at top of range → iv_rank > 50."""
        ticker_closes = [100.0 + i * 0.1 for i in range(60)]
        ticker_bars = _make_bars_rows(ticker_closes, ticker="AAPL")
        sector_bars = _make_bars_rows([100.0 + i * 0.1 for i in range(60)], ticker="XLK")
        iv_history = [0.20, 0.22, 0.24, 0.26, 0.28, 0.30, 0.32, 0.18, 0.19, 0.21]
        current_iv = 0.35  # above all history

        result = compute_ticker_signals(
            "AAPL", ticker_bars, iv_history, current_iv, sector_bars
        )
        assert result is not None
        assert isinstance(result, TickerSignals)
        assert result.iv_rank > 50.0


class TestTickerSignalsNoIv:
    def test_no_iv_defaults_to_50(self):
        """No IV data (empty history, None current) → iv_rank=50, iv_percentile=50."""
        ticker_closes = [100.0] * 60
        ticker_bars = _make_bars_rows(ticker_closes, ticker="MSFT")
        sector_bars = _make_bars_rows([100.0] * 60, ticker="XLK")

        result = compute_ticker_signals(
            "MSFT", ticker_bars, [], None, sector_bars
        )
        assert result is not None
        assert result.iv_rank == 50.0
        assert result.iv_percentile == 50.0


class TestTickerSignalsInsufficientData:
    def test_insufficient_data_returns_none(self):
        """Fewer than 20 bars → None."""
        ticker_bars = _make_bars_rows([100.0] * 10, ticker="NVDA")
        sector_bars = _make_bars_rows([100.0] * 10, ticker="XLK")

        result = compute_ticker_signals(
            "NVDA", ticker_bars, [], None, sector_bars
        )
        assert result is None


class TestTickerSignalsIndexNoSector:
    def test_spy_has_no_sector_etf(self):
        """SPY → sector_etf=None (from TICKER_SECTOR_MAP), sector_relative=0.0."""
        ticker_closes = [400.0 + i * 0.2 for i in range(60)]
        ticker_bars = _make_bars_rows(ticker_closes, ticker="SPY")
        sector_bars = _make_bars_rows([100.0] * 60, ticker="XLK")

        result = compute_ticker_signals(
            "SPY", ticker_bars, [], None, sector_bars
        )
        assert result is not None
        assert result.sector_etf is None
        assert result.sector_relative == 0.0


# ---------------------------------------------------------------------------
# Additional TickerSignals tests
# ---------------------------------------------------------------------------

def test_ticker_signals_basic():
    """Basic ticker signals with known IV data."""
    ticker_bars = _make_bars_rows([100.0 + i * 0.2 for i in range(252)], "AAPL")
    iv_history = [20.0 + i * (20.0 / 251) for i in range(252)]
    current_iv = 35.0
    sector_bars = _make_bars_rows([150.0 + i * 0.1 for i in range(252)], "XLK")

    signals = compute_ticker_signals(
        ticker="AAPL",
        ticker_bars=ticker_bars,
        iv_history=iv_history,
        current_iv=current_iv,
        sector_etf_bars=sector_bars,
    )
    assert signals is not None
    assert signals.ticker == "AAPL"
    assert signals.iv_rank > 50.0
    assert signals.sector_etf == "XLK"
    assert signals.vol_regime in ("low", "moderate", "high")


def test_ticker_signals_no_iv_defaults_to_50():
    """Missing IV data → iv_rank and iv_percentile default to 50."""
    ticker_bars = _make_bars_rows([100.0] * 60, "AAPL")
    sector_bars = _make_bars_rows([100.0] * 60, "XLK")
    signals = compute_ticker_signals(
        ticker="AAPL",
        ticker_bars=ticker_bars,
        iv_history=[],
        current_iv=None,
        sector_etf_bars=sector_bars,
    )
    assert signals is not None
    assert signals.iv_rank == 50.0
    assert signals.iv_percentile == 50.0


def test_ticker_signals_insufficient_data():
    """Less than 20 bars → returns None."""
    ticker_bars = _make_bars_rows([100.0] * 10, "AAPL")
    result = compute_ticker_signals(
        ticker="AAPL",
        ticker_bars=ticker_bars,
        iv_history=[],
        current_iv=None,
        sector_etf_bars=None,
    )
    assert result is None


def test_ticker_signals_index_has_no_sector():
    """SPY maps to None sector → sector_relative = 0.0."""
    ticker_bars = _make_bars_rows([400.0 + i * 0.3 for i in range(252)], "SPY")
    signals = compute_ticker_signals(
        ticker="SPY",
        ticker_bars=ticker_bars,
        iv_history=[20.0] * 252,
        current_iv=20.0,
        sector_etf_bars=None,
    )
    assert signals is not None
    assert signals.sector_etf is None
    assert signals.sector_relative == 0.0
