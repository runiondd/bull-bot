"""Unit tests for bullbot.data.daily_refresh."""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from bullbot.data import daily_refresh
from bullbot.data.schemas import Bar


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE bars (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL,
            UNIQUE(ticker, timeframe, ts)
        );
        """
    )
    return c


def _fake_df(rows: list[tuple[str, float, float, float, float, int]]) -> pd.DataFrame:
    """Build a Yahoo-shaped OHLCV DataFrame from (iso_date, O, H, L, C, V) tuples."""
    data = {
        "Open": [r[1] for r in rows],
        "High": [r[2] for r in rows],
        "Low": [r[3] for r in rows],
        "Close": [r[4] for r in rows],
        "Volume": [r[5] for r in rows],
    }
    idx = pd.DatetimeIndex([pd.Timestamp(r[0], tz="UTC") for r in rows])
    return pd.DataFrame(data, index=idx)


def test_fetch_bars_yahoo_returns_bars():
    df = _fake_df(
        [
            ("2026-04-14", 150.0, 155.0, 149.5, 154.0, 1_000_000),
            ("2026-04-15", 154.5, 158.0, 154.0, 157.5, 1_100_000),
        ]
    )
    fake_yf = lambda symbol, period="1mo": df  # noqa: E731
    bars = daily_refresh.fetch_bars_yahoo("NVDA", fetch_fn=fake_yf)

    assert len(bars) == 2
    assert all(isinstance(b, Bar) for b in bars)
    assert all(b.ticker == "NVDA" for b in bars)
    assert all(b.timeframe == "1d" for b in bars)
    assert all(b.source == "yahoo" for b in bars)
    assert bars[0].close == 154.0
    assert bars[1].close == 157.5
    assert bars[0].volume == 1_000_000


def test_fetch_bars_yahoo_maps_vix_to_caret_symbol():
    seen_symbols: list[str] = []

    def fake_yf(symbol: str, period: str = "1mo") -> pd.DataFrame:
        seen_symbols.append(symbol)
        return _fake_df([("2026-04-15", 15.0, 16.0, 14.5, 15.5, 0)])

    bars = daily_refresh.fetch_bars_yahoo("VIX", fetch_fn=fake_yf)
    assert seen_symbols == ["^VIX"]
    assert bars[0].ticker == "VIX"


def test_fetch_bars_yahoo_empty_raises():
    fake_yf = lambda symbol, period="1mo": pd.DataFrame()  # noqa: E731
    with pytest.raises(daily_refresh.DailyRefreshError):
        daily_refresh.fetch_bars_yahoo("NVDA", fetch_fn=fake_yf)


def test_refresh_all_bars_upserts(conn):
    df_nvda = _fake_df([("2026-04-15", 150.0, 155.0, 149.5, 154.0, 1_000_000)])
    df_tsla = _fake_df([("2026-04-15", 300.0, 310.0, 299.0, 305.0, 2_000_000)])

    def fake_yf(symbol: str, period: str = "1mo") -> pd.DataFrame:
        return {"NVDA": df_nvda, "TSLA": df_tsla}[symbol]

    result = daily_refresh.refresh_all_bars(conn, ["NVDA", "TSLA"], fetch_fn=fake_yf)

    assert result == {"NVDA": 1, "TSLA": 1}
    rows = conn.execute("SELECT ticker, close FROM bars ORDER BY ticker").fetchall()
    assert [(r["ticker"], r["close"]) for r in rows] == [("NVDA", 154.0), ("TSLA", 305.0)]


def test_refresh_all_bars_is_idempotent(conn):
    df = _fake_df([("2026-04-15", 150.0, 155.0, 149.5, 154.0, 1_000_000)])
    fake_yf = lambda symbol, period="1mo": df  # noqa: E731

    daily_refresh.refresh_all_bars(conn, ["NVDA"], fetch_fn=fake_yf)
    daily_refresh.refresh_all_bars(conn, ["NVDA"], fetch_fn=fake_yf)

    n = conn.execute("SELECT COUNT(*) FROM bars WHERE ticker='NVDA'").fetchone()[0]
    assert n == 1


def test_refresh_all_bars_skips_failed_ticker_and_continues(conn):
    df_ok = _fake_df([("2026-04-15", 150.0, 155.0, 149.5, 154.0, 1_000_000)])

    def fake_yf(symbol: str, period: str = "1mo") -> pd.DataFrame:
        if symbol == "BAD":
            raise RuntimeError("simulated fetch failure")
        return df_ok

    result = daily_refresh.refresh_all_bars(conn, ["BAD", "NVDA"], fetch_fn=fake_yf)
    assert result["NVDA"] == 1
    assert result["BAD"] == 0
    rows = conn.execute("SELECT ticker FROM bars").fetchall()
    assert [r["ticker"] for r in rows] == ["NVDA"]


def test_fetch_bars_yahoo_passes_period_to_fetch_fn():
    """Callers should be able to request a longer period (e.g. "5y") for backfill."""
    seen: list[str] = []

    def fake_yf(symbol: str, period: str = "1mo") -> pd.DataFrame:
        seen.append(period)
        return _fake_df([("2026-04-15", 150.0, 155.0, 149.5, 154.0, 1_000_000)])

    daily_refresh.fetch_bars_yahoo("NVDA", period="5y", fetch_fn=fake_yf)
    assert seen == ["5y"]


def test_refresh_all_bars_passes_period(conn):
    seen: list[str] = []

    def fake_yf(symbol: str, period: str = "1mo") -> pd.DataFrame:
        seen.append(period)
        return _fake_df([("2026-04-15", 150.0, 155.0, 149.5, 154.0, 1_000_000)])

    daily_refresh.refresh_all_bars(conn, ["NVDA"], period="5y", fetch_fn=fake_yf)
    assert seen == ["5y"]


def test_discover_tracked_tickers_returns_distinct(conn):
    conn.executemany(
        "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
        "VALUES (?, '1d', ?, 1, 1, 1, 1, 0)",
        [
            ("NVDA", 1),
            ("NVDA", 2),
            ("TSLA", 1),
            ("SPY", 1),
        ],
    )
    tickers = daily_refresh.discover_tracked_tickers(conn)
    assert sorted(tickers) == ["NVDA", "SPY", "TSLA"]
