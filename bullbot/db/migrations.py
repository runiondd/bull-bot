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
