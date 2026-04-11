"""
Options backfill — algorithmically enumerate option symbols for a ticker
across a backfill window and bulk-fetch their histories.

This is the Phase 0b workaround for UW's 7-day chain-discovery gate. We
construct symbols directly using the OSI regex + a hardcoded NYSE weekly/
monthly expiry calendar + a strike grid around the underlying's spot.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, timedelta
from typing import Iterator

import pandas_market_calendars as mcal

from bullbot.data import cache, fetchers

log = logging.getLogger("bullbot.backfill")

_CAL = mcal.get_calendar("NYSE")


def format_osi_symbol(ticker: str, expiry: date, strike: float, kind: str) -> str:
    """Build an OSI option symbol: TICKER + YYMMDD + P/C + strike*1000 (8 digits)."""
    if kind not in ("C", "P"):
        raise ValueError(f"kind must be C or P, got {kind}")
    return f"{ticker}{expiry:%y%m%d}{kind}{int(round(strike * 1000)):08d}"


def enumerate_expiries(start: date, end: date) -> list[date]:
    """All NYSE Fridays between start and end."""
    sched = _CAL.schedule(start_date=start, end_date=end)
    result: list[date] = []
    for idx in sched.index:
        d = idx.date()
        if d.weekday() == 4:  # Friday
            result.append(d)
    return result


def enumerate_strikes_around_spot(
    spot: float, range_fraction: float, step: float
) -> list[float]:
    """Strikes from spot*(1-range) to spot*(1+range), stepped by `step`."""
    lo = spot * (1 - range_fraction)
    hi = spot * (1 + range_fraction)
    lo = (int(lo // step)) * step
    hi_grid = int(hi // step) * step
    # include hi only if it falls exactly on a grid point, otherwise don't overshoot
    hi = hi_grid if hi_grid >= hi - 1e-9 else hi_grid + step
    strikes: list[float] = []
    s = lo
    while s <= hi:
        strikes.append(round(s, 2))
        s += step
    return strikes


def build_candidate_symbols(
    ticker: str,
    spot: float,
    backfill_start: date,
    backfill_end: date,
    strike_range_fraction: float = 0.20,
    strike_step: float = 1.0,
) -> list[str]:
    """Build the full list of candidate option symbols to probe."""
    expiries = enumerate_expiries(backfill_start, backfill_end)
    strikes = enumerate_strikes_around_spot(spot, strike_range_fraction, strike_step)
    out: list[str] = []
    for exp in expiries:
        for k in strikes:
            for kind in ("P", "C"):
                out.append(format_osi_symbol(ticker, exp, k, kind))
    return out


def run(
    conn: sqlite3.Connection,
    client: fetchers._ClientLike,
    ticker: str,
    spot: float,
    start: date,
    end: date,
    rate_limit_sleep: float = 0.1,
    strike_range_fraction: float = 0.20,
    strike_step: float = 1.0,
) -> dict[str, int]:
    """Backfill option history for a ticker across a date window."""
    symbols = build_candidate_symbols(
        ticker=ticker, spot=spot, backfill_start=start, backfill_end=end,
        strike_range_fraction=strike_range_fraction, strike_step=strike_step,
    )
    log.info("backfill %s: %d candidate symbols", ticker, len(symbols))

    tried = 0
    with_data = 0
    rows_written = 0
    for sym in symbols:
        tried += 1
        try:
            contracts = fetchers.fetch_option_historic(client, sym)
        except fetchers.DataFetchError as e:
            log.warning("fetch error on %s: %s", sym, e)
            continue
        if contracts:
            with_data += 1
            for c in contracts:
                db_kind = "call" if c.kind == "C" else "put"
                conn.execute(
                    "INSERT OR REPLACE INTO option_contracts "
                    "(ticker, expiry, strike, kind, ts, bid, ask, iv, volume, open_interest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (c.ticker, c.expiry, c.strike, db_kind, c.ts,
                     c.nbbo_bid, c.nbbo_ask, c.iv, c.volume, c.open_interest),
                )
                rows_written += 1
        time.sleep(rate_limit_sleep)

    log.info("backfill %s done: tried=%d with_data=%d rows_written=%d",
             ticker, tried, with_data, rows_written)
    return {"symbols_tried": tried, "symbols_with_data": with_data, "rows_written": rows_written}
