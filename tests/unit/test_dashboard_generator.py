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
    out = tmp_path / "dashboard.html"
    generator.generate(conn, output_path=out)
    html = out.read_text()
    assert "tab-Health" in html
    assert 'class="research-health"' in html
    assert ">Health<" in html  # tab button text
