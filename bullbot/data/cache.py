"""
Read-through cache between the fetchers and the rest of the system.
"""

from __future__ import annotations

import re
import sqlite3

from bullbot.data import fetchers
from bullbot.data.schemas import Bar, OptionContract

# DDL for the fetch-log table that tracks what has already been pulled from the API.
# This lives in the same SQLite DB so it is scoped per connection (e.g., per test).
_FETCH_LOG_DDL = """
CREATE TABLE IF NOT EXISTS _bars_fetch_log (
    ticker    TEXT    NOT NULL,
    timeframe TEXT    NOT NULL,
    fetched_limit INTEGER NOT NULL,
    PRIMARY KEY (ticker, timeframe)
)
"""


def _ensure_fetch_log(conn: sqlite3.Connection) -> None:
    conn.execute(_FETCH_LOG_DDL)


def _get_fetched_limit(conn: sqlite3.Connection, ticker: str, timeframe: str) -> int:
    """Return the highest limit previously fetched for this ticker/timeframe, or 0."""
    _ensure_fetch_log(conn)
    row = conn.execute(
        "SELECT fetched_limit FROM _bars_fetch_log WHERE ticker=? AND timeframe=?",
        (ticker, timeframe),
    ).fetchone()
    return row[0] if row else 0


def _record_fetch(conn: sqlite3.Connection, ticker: str, timeframe: str, limit: int) -> None:
    _ensure_fetch_log(conn)
    conn.execute(
        "INSERT OR REPLACE INTO _bars_fetch_log (ticker, timeframe, fetched_limit) VALUES (?, ?, ?)",
        (ticker, timeframe, limit),
    )


def _row_count(conn: sqlite3.Connection, ticker: str, timeframe: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE ticker=? AND timeframe=?",
        (ticker, timeframe),
    ).fetchone()
    return row[0] if row else 0


def _persist_bars(conn: sqlite3.Connection, bars: list[Bar]) -> None:
    for b in bars:
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (b.ticker, b.timeframe, b.ts, b.open, b.high, b.low, b.close, b.volume),
        )


def _load_bars(conn: sqlite3.Connection, ticker: str, timeframe: str, limit: int) -> list[Bar]:
    rows = conn.execute(
        "SELECT ticker, timeframe, ts, open, high, low, close, volume "
        "FROM bars WHERE ticker=? AND timeframe=? ORDER BY ts DESC LIMIT ?",
        (ticker, timeframe, limit),
    ).fetchall()
    return [
        Bar(
            ticker=r["ticker"], timeframe=r["timeframe"], ts=r["ts"],
            open=r["open"], high=r["high"], low=r["low"], close=r["close"],
            volume=int(r["volume"]), source="uw",
        )
        for r in reversed(rows)
    ]


def get_daily_bars(
    conn: sqlite3.Connection,
    client: fetchers._ClientLike,
    ticker: str,
    limit: int = 500,
) -> list[Bar]:
    """Get daily bars, fetching from UW only if cache has fewer than requested.

    After a successful API fetch with a given limit, subsequent calls with the
    same or smaller limit are served entirely from the SQLite cache without
    hitting the network again.
    """
    cached_count = _row_count(conn, ticker, "1d")
    already_fetched_limit = _get_fetched_limit(conn, ticker, "1d")

    # Serve from cache if:
    #   (a) cache has enough rows, OR
    #   (b) we already issued an API fetch with a limit >= current request
    if cached_count >= limit or already_fetched_limit >= limit:
        return _load_bars(conn, ticker, "1d", limit)

    fetch_limit = max(limit, 500)
    fresh = fetchers.fetch_daily_ohlc(client, ticker, limit=fetch_limit)
    _persist_bars(conn, fresh)
    _record_fetch(conn, ticker, "1d", fetch_limit)
    return _load_bars(conn, ticker, "1d", limit)


def _parse_symbol_into_pk(symbol: str) -> tuple[str, str, float, str]:
    m = re.match(
        r"^(?P<t>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<k>[PC])(?P<s>\d{8})$",
        symbol,
    )
    if not m:
        raise ValueError(f"bad symbol {symbol}")
    return m["t"], f"20{m['yy']}-{m['mm']}-{m['dd']}", int(m["s"]) / 1000.0, m["k"]


def _persist_option_contracts(conn: sqlite3.Connection, contracts: list[OptionContract]) -> None:
    for c in contracts:
        # Map schema kind C/P to db kind call/put
        db_kind = "call" if c.kind == "C" else "put"
        conn.execute(
            "INSERT OR REPLACE INTO option_contracts "
            "(ticker, expiry, strike, kind, ts, bid, ask, iv, volume, open_interest) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (c.ticker, c.expiry, c.strike, db_kind, c.ts,
             c.nbbo_bid, c.nbbo_ask, c.iv, c.volume, c.open_interest),
        )


def get_option_contract_history(
    conn: sqlite3.Connection,
    client: fetchers._ClientLike,
    option_symbol: str,
) -> list[OptionContract]:
    """Fetch full historical series for a specific contract and cache it."""
    ticker, expiry, strike, kind = _parse_symbol_into_pk(option_symbol)
    db_kind = "call" if kind == "C" else "put"
    rows = conn.execute(
        "SELECT COUNT(*) FROM option_contracts WHERE "
        "ticker=? AND expiry=? AND strike=? AND kind=?",
        (ticker, expiry, strike, db_kind),
    ).fetchone()
    cached_count = rows[0] if rows else 0
    if cached_count > 0:
        return _load_option_contract(conn, option_symbol)

    fresh = fetchers.fetch_option_historic(client, option_symbol)
    if fresh:
        _persist_option_contracts(conn, fresh)
    return fresh


def _load_option_contract(conn: sqlite3.Connection, option_symbol: str) -> list[OptionContract]:
    ticker, expiry, strike, kind = _parse_symbol_into_pk(option_symbol)
    db_kind = "call" if kind == "C" else "put"
    rows = conn.execute(
        "SELECT * FROM option_contracts WHERE "
        "ticker=? AND expiry=? AND strike=? AND kind=? ORDER BY ts",
        (ticker, expiry, strike, db_kind),
    ).fetchall()
    return [
        OptionContract(
            ticker=r["ticker"], expiry=r["expiry"], strike=r["strike"],
            kind="C" if r["kind"] == "call" else "P",
            ts=r["ts"], nbbo_bid=r["bid"], nbbo_ask=r["ask"],
            last=None,
            volume=r["volume"], open_interest=r["open_interest"], iv=r["iv"],
        )
        for r in rows
    ]
