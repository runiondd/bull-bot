"""Daily equity snapshot for the dashboard equity curve.

Called at the end of scheduler.tick(). Writes one row per UTC day,
upserting on the unique ts constraint so multiple ticks on the same
day don't duplicate.
"""
from __future__ import annotations

import logging
import sqlite3
import time

from bullbot import config

log = logging.getLogger("bullbot.research.equity_snapshot")


def _utc_midnight(ts: int) -> int:
    """Truncate a unix timestamp to midnight UTC of that day."""
    return ts - (ts % 86400)


def take_snapshot(conn: sqlite3.Connection, now: int | None = None) -> int:
    """Compute current equity and write a snapshot row for today (UTC).

    Idempotent within a single UTC day: re-running upserts. Returns the
    snapshot's ts (midnight UTC of the day it was written for).
    """
    now = now if now is not None else int(time.time())
    day_ts = _utc_midnight(now)

    realized = conn.execute(
        "SELECT COALESCE(SUM(pnl_realized), 0) FROM positions "
        "WHERE run_id NOT LIKE 'bt:%' AND pnl_realized IS NOT NULL"
    ).fetchone()[0]
    unrealized = conn.execute(
        "SELECT COALESCE(SUM(unrealized_pnl), 0) FROM positions "
        "WHERE run_id NOT LIKE 'bt:%' AND closed_at IS NULL"
    ).fetchone()[0]

    income_base = config.INITIAL_CAPITAL_USD
    growth_base = config.GROWTH_CAPITAL_USD
    income_equity = income_base + float(realized) + float(unrealized)
    growth_equity = float(growth_base)
    total_equity = income_equity + growth_equity

    conn.execute(
        "INSERT INTO equity_snapshots "
        "(ts, total_equity, income_equity, growth_equity, realized_pnl, unrealized_pnl, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(ts) DO UPDATE SET "
        "total_equity=excluded.total_equity, income_equity=excluded.income_equity, "
        "growth_equity=excluded.growth_equity, realized_pnl=excluded.realized_pnl, "
        "unrealized_pnl=excluded.unrealized_pnl, created_at=excluded.created_at",
        (day_ts, total_equity, income_equity, growth_equity,
         float(realized), float(unrealized), now),
    )
    conn.commit()
    return day_ts
