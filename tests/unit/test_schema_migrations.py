"""Schema migration tests for strategy-search-engine Phase A columns + tables."""
import sqlite3
from bullbot.db.migrations import apply_schema


def test_evolver_proposals_has_new_columns(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    apply_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(evolver_proposals)")}
    assert {"regime_label", "score_a", "size_units", "max_loss_per_trade"} <= cols


def test_sweep_failures_table_exists(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    apply_schema(conn)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "sweep_failures" in tables
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sweep_failures)")}
    assert {"id", "ts", "ticker", "class_name", "cell_params_json",
            "exc_type", "exc_message", "traceback"} <= cols
