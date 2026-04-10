"""SQLite connection helper."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from bullbot.db import migrations


@contextmanager
def open_connection(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        migrations.apply_schema(conn)
        yield conn
    finally:
        conn.close()


def open_persistent_connection(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)
    return conn
