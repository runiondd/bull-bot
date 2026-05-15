"""Paper-trade entry/exit persistence for v2 share positions.

One row per open-close cycle in `v2_paper_trades`. Open trades have NULL
`exit_*` fields. PnL is computed at close — sign-aware for shorts.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

VALID_DIRECTIONS = ("long", "short")


@dataclass
class Trade:
    ticker: str
    direction: str
    shares: float
    entry_price: float
    entry_ts: int
    signal_id: int | None
    id: int | None = None
    exit_price: float | None = None
    exit_ts: int | None = None
    pnl_realized: float | None = None
    exit_reason: str | None = None

    def __post_init__(self) -> None:
        if self.direction not in VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {VALID_DIRECTIONS}; got {self.direction!r}"
            )


def open_trade(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    direction: str,
    shares: float,
    entry_price: float,
    entry_ts: int,
    signal_id: int | None,
) -> Trade:
    """Open a new paper trade and persist it. Returns the Trade with `id` set."""
    t = Trade(
        ticker=ticker, direction=direction, shares=shares,
        entry_price=entry_price, entry_ts=entry_ts, signal_id=signal_id,
    )
    cur = conn.execute(
        "INSERT INTO v2_paper_trades "
        "(ticker, direction, shares, entry_price, entry_ts, signal_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ticker, direction, shares, entry_price, entry_ts, signal_id, int(time.time())),
    )
    conn.commit()
    t.id = cur.lastrowid
    return t


def open_position_for(conn: sqlite3.Connection, ticker: str) -> Trade | None:
    """Return the currently-open trade for `ticker`, or None if none open."""
    row = conn.execute(
        "SELECT * FROM v2_paper_trades WHERE ticker=? AND exit_ts IS NULL "
        "ORDER BY entry_ts DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_trade(row)


def close_trade(
    conn: sqlite3.Connection,
    *,
    trade_id: int,
    exit_price: float,
    exit_ts: int,
    exit_reason: str,
) -> Trade:
    """Close an open trade by id. Computes PnL (sign-aware for shorts)."""
    row = conn.execute(
        "SELECT * FROM v2_paper_trades WHERE id=?", (trade_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"trade_id {trade_id} not found")
    if row["exit_ts"] is not None:
        raise ValueError(f"trade_id {trade_id} already closed")

    direction = row["direction"]
    shares = float(row["shares"])
    entry_price = float(row["entry_price"])
    if direction == "long":
        pnl = (exit_price - entry_price) * shares
    else:  # short
        pnl = (entry_price - exit_price) * shares

    conn.execute(
        "UPDATE v2_paper_trades SET exit_price=?, exit_ts=?, pnl_realized=?, "
        "exit_reason=? WHERE id=?",
        (exit_price, exit_ts, pnl, exit_reason, trade_id),
    )
    conn.commit()

    updated = conn.execute(
        "SELECT * FROM v2_paper_trades WHERE id=?", (trade_id,),
    ).fetchone()
    return _row_to_trade(updated)


def total_realized_pnl(conn: sqlite3.Connection) -> float:
    """Sum of pnl_realized across all closed trades."""
    row = conn.execute(
        "SELECT COALESCE(SUM(pnl_realized), 0.0) FROM v2_paper_trades "
        "WHERE exit_ts IS NOT NULL"
    ).fetchone()
    return float(row[0])


def _row_to_trade(row: sqlite3.Row) -> Trade:
    return Trade(
        id=int(row["id"]),
        ticker=row["ticker"],
        direction=row["direction"],
        shares=float(row["shares"]),
        entry_price=float(row["entry_price"]),
        entry_ts=int(row["entry_ts"]),
        signal_id=row["signal_id"],
        exit_price=float(row["exit_price"]) if row["exit_price"] is not None else None,
        exit_ts=int(row["exit_ts"]) if row["exit_ts"] is not None else None,
        pnl_realized=float(row["pnl_realized"]) if row["pnl_realized"] is not None else None,
        exit_reason=row["exit_reason"],
    )
