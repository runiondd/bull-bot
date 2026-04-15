"""Daily bar refresh via Yahoo Finance.

Generalized version of `fetch_vix_bars_yahoo` that works for any ticker, plus
an idempotent upsert helper used by the daily scheduler job and the
`scripts/update_bars.py` CLI.

The fetch function takes an injected `fetch_fn` so tests can provide a fake
yfinance-shaped DataFrame without hitting the network. Production callers
omit it and get the real yfinance backend.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable

import pandas as pd

from bullbot.data.schemas import Bar

log = logging.getLogger("bullbot.daily_refresh")


class DailyRefreshError(Exception):
    """Raised when a Yahoo fetch returns no usable data."""


# Yahoo uses `^` prefix for indices; our DB stores the plain symbol.
_YAHOO_SYMBOL_MAP: dict[str, str] = {
    "VIX": "^VIX",
}


def _default_fetch(symbol: str) -> pd.DataFrame:
    """Real Yahoo Finance fetch — imported lazily so tests don't need yfinance."""
    import yfinance as yf

    return yf.Ticker(symbol).history(period="1mo", interval="1d")


FetchFn = Callable[[str], pd.DataFrame]


def fetch_bars_yahoo(ticker: str, fetch_fn: FetchFn | None = None) -> list[Bar]:
    """Fetch recent daily bars for `ticker` from Yahoo Finance.

    Returns a list of validated `Bar` objects tagged `source="yahoo"`. Raises
    `DailyRefreshError` if Yahoo returns an empty frame.
    """
    fetch_fn = fetch_fn or _default_fetch
    symbol = _YAHOO_SYMBOL_MAP.get(ticker.upper(), ticker.upper())
    df = fetch_fn(symbol)
    if df is None or df.empty:
        raise DailyRefreshError(f"empty Yahoo response for {ticker} (symbol={symbol})")

    bars: list[Bar] = []
    for idx, row in df.iterrows():
        ts = int(idx.timestamp())
        bars.append(
            Bar(
                ticker=ticker,
                timeframe="1d",
                ts=ts,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=max(int(row.get("Volume", 0) or 0), 0),
                source="yahoo",
            )
        )
    return bars


def refresh_all_bars(
    conn: sqlite3.Connection,
    tickers: list[str],
    fetch_fn: FetchFn | None = None,
) -> dict[str, int]:
    """Refresh daily bars for each ticker in `tickers`, upserting into `bars`.

    Failures for an individual ticker are logged and recorded as 0 in the
    result — the refresh continues with the remaining tickers. Returns a
    `{ticker: bars_written}` map.
    """
    result: dict[str, int] = {}
    for ticker in tickers:
        try:
            bars = fetch_bars_yahoo(ticker, fetch_fn=fetch_fn)
        except Exception as exc:
            log.warning("daily_refresh: %s failed: %s", ticker, exc)
            result[ticker] = 0
            continue
        for b in bars:
            conn.execute(
                "INSERT OR REPLACE INTO bars "
                "(ticker, timeframe, ts, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (b.ticker, b.timeframe, b.ts, b.open, b.high, b.low, b.close, b.volume),
            )
        result[ticker] = len(bars)
        log.info("daily_refresh: %s -> %d bars", ticker, len(bars))
    conn.commit()
    return result


def discover_tracked_tickers(conn: sqlite3.Connection) -> list[str]:
    """Return all distinct tickers that already have at least one row in `bars`."""
    rows = conn.execute("SELECT DISTINCT ticker FROM bars").fetchall()
    return [r[0] for r in rows]
