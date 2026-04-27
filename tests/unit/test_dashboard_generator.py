# tests/unit/test_dashboard_generator.py
import sqlite3
import json
from pathlib import Path

import pytest

from bullbot.dashboard import generator


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE ticker_state (
            id INTEGER PRIMARY KEY, ticker TEXT, phase TEXT,
            iteration_count INTEGER, plateau_counter INTEGER,
            best_strategy_id INTEGER, best_pf_is REAL, best_pf_oos REAL,
            cumulative_llm_usd REAL, paper_started_at INTEGER,
            paper_trade_count INTEGER, live_started_at INTEGER,
            verdict_at INTEGER, retired INTEGER, updated_at INTEGER
        );
        CREATE TABLE strategies (
            id INTEGER PRIMARY KEY, class_name TEXT, class_version INTEGER,
            params TEXT, params_hash TEXT, parent_id INTEGER, created_at INTEGER
        );
        CREATE TABLE evolver_proposals (
            id INTEGER PRIMARY KEY, ticker TEXT, iteration INTEGER,
            strategy_id INTEGER, rationale TEXT, llm_cost_usd REAL,
            pf_is REAL, pf_oos REAL, sharpe_is REAL, max_dd_pct REAL,
            trade_count INTEGER, regime_breakdown TEXT, passed_gate INTEGER,
            created_at INTEGER
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY, run_id TEXT, ticker TEXT, strategy_id INTEGER,
            legs TEXT, contracts INTEGER, open_price REAL, close_price REAL,
            mark_to_mkt REAL, opened_at INTEGER, closed_at INTEGER,
            pnl_realized REAL, exit_rules TEXT, unrealized_pnl REAL
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY, run_id TEXT, ticker TEXT, strategy_id INTEGER,
            intent TEXT, legs TEXT, status TEXT, commission REAL,
            pnl_realized REAL, placed_at INTEGER
        );
        CREATE TABLE cost_ledger (
            id INTEGER PRIMARY KEY, ts INTEGER, category TEXT, ticker TEXT,
            amount_usd REAL, details TEXT
        );
        CREATE TABLE equity_snapshots (
            id INTEGER PRIMARY KEY, ts INTEGER,
            total_equity REAL, income_equity REAL, growth_equity REAL,
            realized_pnl REAL, unrealized_pnl REAL
        );
        CREATE TABLE long_inventory (
            id INTEGER PRIMARY KEY, account TEXT, ticker TEXT, kind TEXT,
            strike REAL, expiry TEXT, qty REAL, cost_basis REAL,
            added_at INTEGER, removed_at INTEGER
        );
        CREATE TABLE bars (
            id INTEGER PRIMARY KEY, ticker TEXT, timeframe TEXT,
            ts INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER
        );
        CREATE TABLE iteration_failures (
            id INTEGER PRIMARY KEY, ticker TEXT, strategy_id INTEGER,
            iteration INTEGER, reason TEXT, created_at INTEGER
        );
    """)
    c.execute("INSERT INTO ticker_state (ticker,phase,iteration_count,paper_trade_count,cumulative_llm_usd,best_strategy_id) VALUES ('SPY','paper_trial',3,1,2.50,1)")
    c.execute("INSERT INTO strategies (id,class_name,class_version,params,params_hash) VALUES (1,'PutCreditSpread',1,'{}','abc')")
    return c


def test_generate_writes_html_file(conn, tmp_path):
    out = tmp_path / "dashboard.html"
    generator.generate(conn, output_path=out)
    assert out.exists()
    html = out.read_text()
    assert "Bull-Bot Dashboard" in html
    assert "SPY" in html
    assert "PutCreditSpread" in html


def test_generate_uses_default_path(conn, monkeypatch, tmp_path):
    import bullbot.config as cfg
    monkeypatch.setattr(cfg, "REPORTS_DIR", tmp_path)
    generator.generate(conn)
    assert (tmp_path / "dashboard.html").exists()


def test_generate_includes_health_tab(conn, tmp_path):
    # Updated for new generator: tab ids are lowercase; health-grid replaces research-health class.
    out = tmp_path / "dashboard.html"
    generator.generate(conn, output_path=out)
    html = out.read_text()
    assert "tab-health" in html          # new generator uses lowercase tab ids
    assert "health-grid" in html         # health_tab() renders class="health-grid"
    assert ">Health<" in html            # sidebar still labels it "Health"


def test_generate_uses_new_shell_and_tabs(conn, tmp_path):
    """Smoke test: generator produces HTML with new design tokens."""
    out = tmp_path / "dashboard.html"
    generator.generate(conn, output_path=out)
    text = out.read_text()
    # New shell markers
    assert "data-theme" in text
    assert "data-accent" in text
    assert "IBM+Plex+Sans" in text
    # 8 tabs present
    for tab in ("overview", "positions", "evolver", "universe",
                "transactions", "health", "costs", "inventory"):
        assert f"tab-{tab}" in text
    # Sidebar groups
    assert ">Operations<" in text
    assert ">Diagnostics<" in text


def test_generate_empty_db_renders(tmp_path, monkeypatch):
    """Fresh DB with only schema applied — page must render."""
    import sqlite3
    from bullbot.db import migrations
    from bullbot import config
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrations.apply_schema(conn)
    generator.generate(conn)
    out = tmp_path / "dashboard.html"
    assert out.exists()
    text = out.read_text()
    # Empty-state assertions
    assert len(text) > 5000  # not blank
    assert "data-theme" in text
