"""v2 daily runner — iterate UNIVERSE and emit one DirectionalSignal per ticker."""
from __future__ import annotations

import logging
import sqlite3
import time
from types import SimpleNamespace

from bullbot import config
from bullbot.v2 import signals, underlying

log = logging.getLogger("bullbot.v2.runner")


def _load_bars(conn: sqlite3.Connection, ticker: str, asof_ts: int, limit: int = 400):
    """Load daily bars for `ticker` with ts <= asof_ts, oldest-first."""
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM bars "
        "WHERE ticker=? AND timeframe='1d' AND ts<=? "
        "ORDER BY ts DESC LIMIT ?",
        (ticker, asof_ts, limit),
    ).fetchall()

    bars = [
        SimpleNamespace(
            ts=r["ts"], open=r["open"], high=r["high"],
            low=r["low"], close=r["close"], volume=r["volume"],
        )
        for r in rows
    ]
    bars.reverse()
    return bars


def run_once(conn: sqlite3.Connection, asof_ts: int | None = None) -> int:
    """Run one v2 daily pass over config.UNIVERSE. Returns the number of signals written."""
    if asof_ts is None:
        asof_ts = int(time.time())
    n = 0
    for ticker in config.UNIVERSE:
        try:
            bars = _load_bars(conn, ticker, asof_ts)
            sig = underlying.classify(ticker=ticker, bars=bars, asof_ts=asof_ts)
            signals.save(conn, sig)
            log.info("v2.runner: %s -> %s conf=%.2f", ticker, sig.direction, sig.confidence)
            n += 1
        except Exception:
            log.exception("v2.runner: %s failed", ticker)
    return n
