"""DirectionalSignal — the output of the v2 underlying agent.

One row per (ticker, asof_ts, rules_version) in `directional_signals`.
Schema-validated at construction so persisted rows are always meaningful.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

VALID_DIRECTIONS = ("bullish", "bearish", "chop", "no_edge")


@dataclass(frozen=True)
class DirectionalSignal:
    ticker: str
    asof_ts: int
    direction: str
    confidence: float
    horizon_days: int
    rationale: str
    rules_version: str

    def __post_init__(self) -> None:
        if self.direction not in VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {VALID_DIRECTIONS}; got {self.direction!r}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1]; got {self.confidence}")


def save(conn: sqlite3.Connection, signal: DirectionalSignal) -> None:
    """Upsert a signal (replaces on (ticker, asof_ts, rules_version) collision)."""
    conn.execute(
        "INSERT OR REPLACE INTO directional_signals "
        "(ticker, asof_ts, direction, confidence, horizon_days, rationale, "
        " rules_version, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            signal.ticker, signal.asof_ts, signal.direction, signal.confidence,
            signal.horizon_days, signal.rationale, signal.rules_version,
            int(time.time()),
        ),
    )
    conn.commit()


def latest_for(
    conn: sqlite3.Connection, ticker: str, rules_version: str | None = None,
) -> DirectionalSignal | None:
    """Return the most recent signal for `ticker`. If rules_version is set, scope to it."""
    if rules_version is None:
        row = conn.execute(
            "SELECT * FROM directional_signals WHERE ticker=? "
            "ORDER BY asof_ts DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM directional_signals WHERE ticker=? AND rules_version=? "
            "ORDER BY asof_ts DESC LIMIT 1",
            (ticker, rules_version),
        ).fetchone()
    if row is None:
        return None
    return DirectionalSignal(
        ticker=row["ticker"],
        asof_ts=int(row["asof_ts"]),
        direction=row["direction"],
        confidence=float(row["confidence"]),
        horizon_days=int(row["horizon_days"]),
        rationale=row["rationale"] or "",
        rules_version=row["rules_version"],
    )
