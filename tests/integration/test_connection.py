"""Connection helper tests."""
import sqlite3
from bullbot.db import connection


def test_open_connection_has_wal_journal_mode(tmp_path):
    db = tmp_path / "test.db"
    with connection.open_connection(db) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


def test_open_connection_has_foreign_keys_on(tmp_path):
    db = tmp_path / "test.db"
    with connection.open_connection(db) as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


def test_open_connection_creates_schema(tmp_path):
    db = tmp_path / "test.db"
    with connection.open_connection(db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bars'"
        ).fetchall()
        assert rows


def test_row_factory_returns_dict_like(tmp_path):
    db = tmp_path / "test.db"
    with connection.open_connection(db) as conn:
        row = conn.execute("SELECT 1 AS one, 2 AS two").fetchone()
        assert row["one"] == 1
        assert row["two"] == 2


def test_conftest_fixture_provides_in_memory_db(db_conn):
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    assert len(rows) >= 12
