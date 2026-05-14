"""Schema loader.

`apply_schema` is idempotent: safe to re-run on an existing DB. It applies
the fresh-DB schema via `CREATE TABLE IF NOT EXISTS` and then a small set
of column-level migrations for columns added after the initial schema.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def apply_schema(conn: sqlite3.Connection) -> None:
    sql = SCHEMA_PATH.read_text()
    conn.executescript(sql)
    _apply_column_migrations(conn)
    conn.execute("PRAGMA foreign_keys=ON")  # ensure FK enforcement persists
    conn.commit()


def _apply_column_migrations(conn: sqlite3.Connection) -> None:
    """Idempotently add columns that were introduced after the initial schema.

    Each block checks whether the column is present via `PRAGMA table_info`
    and only issues the ALTER if missing. Safe to re-run.
    """
    # positions.unrealized_pnl — added 2026-04-23 alongside the daily
    # mark-to-market refresh (see bullbot.engine.exit_manager).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    if "unrealized_pnl" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN unrealized_pnl REAL")

    # evolver_proposals.proposer_model — added 2026-04-27 for the Phase 2
    # Opus-vs-Sonnet A/B harness. NULL on legacy rows.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(evolver_proposals)")}
    if "proposer_model" not in cols:
        conn.execute("ALTER TABLE evolver_proposals ADD COLUMN proposer_model TEXT")

    # evolver_proposals.regime_label — added 2026-05-14 for strategy-search-engine
    # Phase A. Stores the market-regime label (e.g. 'trending', 'range') active
    # during this proposal's evaluation window.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(evolver_proposals)")}
    if "regime_label" not in cols:
        conn.execute("ALTER TABLE evolver_proposals ADD COLUMN regime_label TEXT")

    # evolver_proposals.score_a — added 2026-05-14 for strategy-search-engine
    # Phase A. Composite search score used to rank proposals across the sweep
    # leaderboard (higher is better).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(evolver_proposals)")}
    if "score_a" not in cols:
        conn.execute("ALTER TABLE evolver_proposals ADD COLUMN score_a REAL")

    # evolver_proposals.size_units — added 2026-05-14 for strategy-search-engine
    # Phase A. Position size in contract units at the time of proposal, for
    # normalising returns across different notional sizes.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(evolver_proposals)")}
    if "size_units" not in cols:
        conn.execute("ALTER TABLE evolver_proposals ADD COLUMN size_units INTEGER")

    # evolver_proposals.max_loss_per_trade — added 2026-05-14 for strategy-search-engine
    # Phase A. Maximum single-trade loss (in dollars) observed during backtesting;
    # used as a tail-risk gate in the sweep filter.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(evolver_proposals)")}
    if "max_loss_per_trade" not in cols:
        conn.execute("ALTER TABLE evolver_proposals ADD COLUMN max_loss_per_trade REAL")
