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

from bullbot import config
from bullbot.data.schemas import Bar

log = logging.getLogger("bullbot.daily_refresh")


class DailyRefreshError(Exception):
    """Raised when a Yahoo fetch returns no usable data."""


# Yahoo uses `^` prefix for indices; our DB stores the plain symbol.
_YAHOO_SYMBOL_MAP: dict[str, str] = {
    "VIX": "^VIX",
}


def _default_fetch(symbol: str, period: str = "1mo") -> pd.DataFrame:
    """Real Yahoo Finance fetch — imported lazily so tests don't need yfinance.

    auto_adjust=False returns raw closes (not dividend-adjusted), matching the
    existing convention used by SPY / sector / UW-sourced bars in the DB.
    Mixing adjusted and raw closes causes spurious ~0.85% mismatches on
    dividend-paying tickers.

    actions=False suppresses standalone dividend/split metadata rows, which
    yfinance otherwise emits as all-NaN OHLC rows on the ex-date and trips
    Bar's gt=0 validators (observed on SPY ex-div 2026-03-20).
    """
    import yfinance as yf

    return yf.Ticker(symbol).history(
        period=period, interval="1d", auto_adjust=False, actions=False
    )


FetchFn = Callable[..., pd.DataFrame]


def fetch_bars_yahoo(
    ticker: str,
    period: str = "1mo",
    fetch_fn: FetchFn | None = None,
) -> list[Bar]:
    """Fetch recent daily bars for `ticker` from Yahoo Finance.

    `period` is a yfinance period string (e.g. "1mo", "3mo", "1y", "5y",
    "max"). Defaults to "1mo" for daily refresh jobs; use "5y" for
    historical backfills.

    Returns a list of validated `Bar` objects tagged `source="yahoo"`. Raises
    `DailyRefreshError` if Yahoo returns an empty frame.
    """
    fetch_fn = fetch_fn or _default_fetch
    symbol = _YAHOO_SYMBOL_MAP.get(ticker.upper(), ticker.upper())
    df = fetch_fn(symbol, period=period)
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
    period: str = "1mo",
    bootstrap_period: str = "5y",
    fetch_fn: FetchFn | None = None,
    commit: bool = True,
) -> dict[str, int]:
    """Refresh daily bars for each ticker in `tickers`, upserting into `bars`.

    `period` is the window for tickers that already have bars (default "1mo"
    for daily incremental refresh). `bootstrap_period` is used instead for
    tickers with zero bars in the DB, so that newly-added tickers pick up
    enough history (default "5y") to clear the evolver's 60-bar snapshot
    threshold on first run.

    `commit=True` (the default) calls `conn.commit()` after all upserts
    succeed, matching the daily-job use case where this function owns the
    transaction. Pass `commit=False` when calling from inside a larger
    transaction that the caller manages (e.g. data migrations).

    Failures for an individual ticker are logged and recorded as 0 in the
    result — the refresh continues with the remaining tickers. Returns a
    `{ticker: bars_written}` map.
    """
    result: dict[str, int] = {}
    for ticker in tickers:
        has_existing = conn.execute(
            "SELECT 1 FROM bars WHERE ticker=? LIMIT 1", (ticker,)
        ).fetchone() is not None
        effective_period = period if has_existing else bootstrap_period
        try:
            bars = fetch_bars_yahoo(ticker, period=effective_period, fetch_fn=fetch_fn)
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
        log.info(
            "daily_refresh: %s -> %d bars (period=%s)",
            ticker,
            len(bars),
            effective_period,
        )
    if commit:
        conn.commit()
    return result


def discover_tracked_tickers(conn: sqlite3.Connection) -> list[str]:
    """Return every ticker that should be refreshed daily.

    Union of three sources:
      - tickers that already have rows in `bars`
      - tickers in `config.UNIVERSE` (newly added but not yet bootstrapped)
      - non-retired tickers in `ticker_state` (auto-inserted by the scheduler)

    Without this union, a ticker added to UNIVERSE never got an initial
    backfill — the evolver would spin on it forever with "Not enough bar data".
    """
    have_bars = {
        r[0] for r in conn.execute("SELECT DISTINCT ticker FROM bars").fetchall()
    }
    in_universe = set(config.UNIVERSE)
    try:
        state_rows = conn.execute(
            "SELECT ticker FROM ticker_state WHERE retired=0"
        ).fetchall()
        in_state = {r[0] for r in state_rows}
    except sqlite3.OperationalError:
        in_state = set()
    return sorted(have_bars | in_universe | in_state)
