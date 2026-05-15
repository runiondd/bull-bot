"""v2 daily runner — iterate UNIVERSE, emit signals, dispatch paper trades."""
from __future__ import annotations

import logging
import sqlite3
import time
from types import SimpleNamespace

from bullbot import config
from bullbot.risk.budget import per_trade_budget_usd
from bullbot.v2 import signals, trader, underlying

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


def _latest_signal_id(conn: sqlite3.Connection, ticker: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM directional_signals WHERE ticker=? "
        "ORDER BY asof_ts DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    return int(row[0]) if row else None


def run_once(conn: sqlite3.Connection, asof_ts: int | None = None) -> int:
    """Run one v2 daily pass over config.UNIVERSE.

    For each ticker: emit a DirectionalSignal, then dispatch a paper-trade
    action (open / hold / flip / close / skip). Returns the number of signals
    written.
    """
    if asof_ts is None:
        asof_ts = int(time.time())
    n = 0
    for ticker in config.UNIVERSE:
        try:
            bars = _load_bars(conn, ticker, asof_ts)
            sig = underlying.classify(ticker=ticker, bars=bars, asof_ts=asof_ts)
            signals.save(conn, sig)
            n += 1

            # Dispatch the paper-trade action for this signal.
            if not bars:
                log.info("v2.runner: %s -> %s conf=%.2f (no bars, skip dispatch)",
                         ticker, sig.direction, sig.confidence)
                continue
            spot = bars[-1].close
            category = config.TICKER_CATEGORY.get(ticker, "income")
            budget = per_trade_budget_usd(category=category)
            signal_id = _latest_signal_id(conn, ticker)
            action = trader.dispatch(
                conn, signal=sig, signal_id=signal_id,
                spot=spot, budget_usd=budget, now_ts=asof_ts,
            )
            log.info(
                "v2.runner: %s -> %s conf=%.2f spot=%.2f budget=$%.0f action=%s",
                ticker, sig.direction, sig.confidence, spot, budget, action.kind,
            )
        except Exception:
            log.exception("v2.runner: %s failed", ticker)
    return n
