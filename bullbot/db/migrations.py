"""Schema loader."""
from __future__ import annotations
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

def apply_schema(conn: sqlite3.Connection) -> None:
    sql = SCHEMA_PATH.read_text()
    conn.executescript(sql)
    conn.execute("PRAGMA foreign_keys=ON")  # ensure FK enforcement persists
    conn.commit()
