"""Python query layer over the `leaderboard` SQL view.

Wraps the view (created in `bullbot/db/migrations.py`) in a typed Python
interface. The dashboard, daily brief, and (later) live-execution layer
all read through `top_n` rather than embedding SQL.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class LeaderboardEntry:
    proposal_id: int
    ticker: str
    class_name: str
    regime_label: str | None
    score_a: float
    size_units: int
    max_loss_per_trade: float
    trade_count: int
    rank: int


def top_n(
    conn: sqlite3.Connection,
    n: int = 10,
    *,
    regime_label: str | None = None,
    ticker: str | None = None,
    class_name: str | None = None,
) -> list[LeaderboardEntry]:
    """Return the top-N leaderboard entries, optionally filtered.

    Always sorted by `score_a` descending (matches the view's own ORDER BY).
    All filter args are kwargs-only; pass any subset.
    """
    sql = (
        "SELECT proposal_id, ticker, class_name, regime_label, score_a, "
        "size_units, max_loss_per_trade, trade_count, rank FROM leaderboard"
    )
    where: list[str] = []
    args: list[object] = []
    if regime_label is not None:
        where.append("regime_label = ?")
        args.append(regime_label)
    if ticker is not None:
        where.append("ticker = ?")
        args.append(ticker)
    if class_name is not None:
        where.append("class_name = ?")
        args.append(class_name)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY score_a DESC LIMIT ?"
    args.append(n)
    return [LeaderboardEntry(*row) for row in conn.execute(sql, args)]
