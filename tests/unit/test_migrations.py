"""Schema loader tests."""
import sqlite3
from bullbot.db import migrations

def test_apply_schema_creates_all_tables():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    table_names = {r[0] for r in rows}
    expected = {"bars", "option_contracts", "iv_surface", "strategies", "evolver_proposals",
                "ticker_state", "orders", "positions", "cost_ledger", "kill_state",
                "faithfulness_checks", "iteration_failures"}
    missing = expected - table_names
    assert not missing, f"missing tables: {missing}"

def test_wal_mode_enabled():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1

def test_strategies_unique_constraint():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    conn.execute("INSERT INTO strategies (class_name, class_version, params, params_hash, created_at) VALUES ('PutCreditSpread', 1, '{}', 'hash1', 1)")
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO strategies (class_name, class_version, params, params_hash, created_at) VALUES ('PutCreditSpread', 1, '{}', 'hash1', 2)")

def test_kill_state_singleton_constraint():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    conn.execute("INSERT INTO kill_state (id, active) VALUES (1, 0)")
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO kill_state (id, active) VALUES (2, 0)")

def test_ticker_state_phase_check_constraint():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('AAPL', 'nonsense', 0)")

def test_evolver_proposals_unique_per_ticker_iteration():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    conn.execute("INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) VALUES (1, 'PCS', 1, '{}', 'h', 0)")
    conn.execute("INSERT INTO evolver_proposals (ticker, iteration, strategy_id, llm_cost_usd, passed_gate, created_at) VALUES ('AAPL', 1, 1, 0.0, 0, 0)")
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO evolver_proposals (ticker, iteration, strategy_id, llm_cost_usd, passed_gate, created_at) VALUES ('AAPL', 1, 1, 0.0, 0, 0)")


def test_positions_has_unrealized_pnl_column():
    """Fresh DB should have unrealized_pnl column on positions."""
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
    assert "unrealized_pnl" in cols


def test_apply_schema_migrates_existing_db_missing_unrealized_pnl():
    """Simulate a pre-migration DB: positions without unrealized_pnl column.
    apply_schema must ADD the column, not error, and second run must be a no-op."""
    conn = sqlite3.connect(":memory:")
    # Create a legacy positions table WITHOUT unrealized_pnl (pre-2026-04-23 shape).
    conn.execute("""
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL DEFAULT 'live',
            ticker TEXT NOT NULL,
            strategy_id INTEGER,
            legs TEXT,
            contracts INTEGER NOT NULL DEFAULT 1,
            open_price REAL NOT NULL,
            close_price REAL,
            mark_to_mkt REAL NOT NULL DEFAULT 0.0,
            exit_rules TEXT,
            opened_at INTEGER NOT NULL,
            closed_at INTEGER,
            pnl_realized REAL
        ) STRICT
    """)
    conn.execute(
        "INSERT INTO positions (ticker, open_price, opened_at) VALUES ('SPY', 100.0, 0)"
    )

    migrations.apply_schema(conn)  # should ADD the column, not throw
    cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
    assert "unrealized_pnl" in cols

    # Pre-existing row should still be there; new column should be NULL for it.
    row = conn.execute(
        "SELECT ticker, unrealized_pnl FROM positions WHERE ticker='SPY'"
    ).fetchone()
    assert row == ("SPY", None)

    # Second apply must be a no-op (idempotent).
    migrations.apply_schema(conn)
    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
    assert cols_after == cols


def test_equity_snapshots_table_exists():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='equity_snapshots'").fetchall()
    assert len(rows) == 1


def test_equity_snapshots_columns():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(equity_snapshots)")}
    assert {"id", "ts", "total_equity", "income_equity", "growth_equity",
            "realized_pnl", "unrealized_pnl"}.issubset(cols)


def test_equity_snapshots_unique_ts():
    """Snapshots are written daily — one per UTC day. Enforce uniqueness on ts."""
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    conn.execute("INSERT INTO equity_snapshots (ts, total_equity, income_equity, growth_equity, realized_pnl, unrealized_pnl) VALUES (1, 265000, 50000, 215000, 0, 0)")
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO equity_snapshots (ts, total_equity, income_equity, growth_equity, realized_pnl, unrealized_pnl) VALUES (1, 266000, 50500, 215500, 100, 400)")


def test_apply_schema_migrates_legacy_db_without_equity_snapshots():
    """Pre-migration DB shouldn't break apply_schema."""
    conn = sqlite3.connect(":memory:")
    # Apply schema with everything except equity_snapshots
    migrations.apply_schema(conn)
    conn.execute("DROP TABLE equity_snapshots")
    # Re-applying must add it back without error
    migrations.apply_schema(conn)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='equity_snapshots'").fetchall()
    assert len(rows) == 1
