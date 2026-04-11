import sqlite3
import pytest
from bullbot.db import migrations


def _fresh_conn():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)
    return conn


def test_regime_briefs_table_exists():
    conn = _fresh_conn()
    conn.execute("SELECT * FROM regime_briefs LIMIT 0")


def test_regime_briefs_insert_and_unique():
    conn = _fresh_conn()
    conn.execute(
        "INSERT INTO regime_briefs (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("market", 1744243200, '{"vix": 15}', "Low vol regime.", "claude-sonnet-4-6", 0.003, "llm", 1744243200),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO regime_briefs (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("market", 1744243200, '{"vix": 16}', "Different.", "claude-sonnet-4-6", 0.003, "llm", 1744243200),
        )


def test_regime_briefs_different_scope_same_ts():
    conn = _fresh_conn()
    conn.execute(
        "INSERT INTO regime_briefs (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("market", 1744243200, '{}', "Market brief.", "claude-sonnet-4-6", 0.003, "llm", 1744243200),
    )
    conn.execute(
        "INSERT INTO regime_briefs (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("AAPL", 1744243200, '{}', "AAPL brief.", "claude-sonnet-4-6", 0.003, "llm", 1744243200),
    )
    rows = conn.execute("SELECT COUNT(*) FROM regime_briefs").fetchone()[0]
    assert rows == 2
