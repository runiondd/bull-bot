"""Unit tests for v2 dashboard tabs (positions + backtest)."""
from __future__ import annotations

import csv
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from bullbot.dashboard import queries, tabs
from bullbot.db.migrations import apply_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def _seed_position(conn, *, ticker="AAPL", structure="long_call", intent="trade"):
    opened_ts = int(datetime(2026, 5, 10, 23).timestamp())
    cur = conn.execute(
        "INSERT INTO v2_positions "
        "(ticker, intent, structure_kind, exit_plan_version, "
        "profit_target_price, stop_price, time_stop_dte, "
        "assignment_acceptable, nearest_leg_expiry_dte, exit_plan_extra_json, "
        "opened_ts, linked_position_id, rationale) "
        "VALUES (?, ?, ?, 1, 110.0, 95.0, 21, 0, 35, NULL, ?, NULL, ?)",
        (ticker, intent, structure, opened_ts, "bullish breakout"),
    )
    pid = cur.lastrowid
    conn.execute(
        "INSERT INTO v2_position_legs "
        "(position_id, action, kind, strike, expiry, qty, entry_price) "
        "VALUES (?, 'buy', 'call', 100.0, '2026-06-15', 1, 3.50)",
        (pid,),
    )
    conn.commit()
    return pid


def test_v2_positions_returns_empty_list_when_no_positions(conn):
    assert queries.v2_positions(conn) == []


def test_v2_positions_returns_open_position_with_summary(conn):
    pid = _seed_position(conn, ticker="AAPL")
    rows = queries.v2_positions(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "AAPL"
    assert r["structure_kind"] == "long_call"
    assert r["intent"] == "trade"
    assert r["profit_target_price"] == 110.0
    assert r["stop_price"] == 95.0
    assert r["time_stop_dte"] == 21
    assert r["rationale"] == "bullish breakout"
    assert "buy call 100" in r["legs_summary"].lower() or "long_call" in r["legs_summary"].lower()


def test_v2_positions_excludes_closed_positions(conn):
    pid = _seed_position(conn)
    conn.execute(
        "UPDATE v2_positions SET closed_ts=?, close_reason='profit_target' WHERE id=?",
        (int(datetime(2026, 5, 15, 23).timestamp()), pid),
    )
    conn.commit()
    assert queries.v2_positions(conn) == []


def test_v2_positions_includes_latest_mtm(conn):
    pid = _seed_position(conn)
    conn.execute(
        "INSERT INTO v2_position_mtm (position_id, asof_ts, mtm_value, source) "
        "VALUES (?, ?, 425.50, 'bs')",
        (pid, int(datetime(2026, 5, 14, 23).timestamp())),
    )
    conn.commit()
    rows = queries.v2_positions(conn)
    assert rows[0]["latest_mtm_value"] == 425.50
    assert rows[0]["latest_mtm_source"] == "bs"


def test_v2_positions_handles_missing_mtm_gracefully(conn):
    _seed_position(conn)
    rows = queries.v2_positions(conn)
    assert rows[0]["latest_mtm_value"] is None
    assert rows[0]["latest_mtm_source"] is None


def test_v2_backtest_latest_returns_none_when_no_reports(tmp_path):
    assert queries.v2_backtest_latest(tmp_path) is None


def test_v2_backtest_latest_returns_none_when_only_non_backtest_subdirs(tmp_path):
    (tmp_path / "other_dir").mkdir()
    (tmp_path / "research_health_123").mkdir()
    assert queries.v2_backtest_latest(tmp_path) is None


def test_v2_backtest_latest_returns_most_recent_report(tmp_path):
    older = tmp_path / "backtest_AAPL_2024_old"
    newer = tmp_path / "backtest_AAPL_2024_new"
    older.mkdir()
    newer.mkdir()
    for d in (older, newer):
        (d / "equity_curve.csv").write_text("asof_ts,asof_date,nav\n1700000000,2023-11-14,50000.0\n")
        (d / "vehicle_attribution.csv").write_text(
            "structure_kind,trade_count,wins,losses,win_rate,total_pnl,avg_pnl\n"
            "long_call,3,2,1,0.6667,250.0,83.33\n"
        )
    import os
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_100_000, 1_700_100_000))
    result = queries.v2_backtest_latest(tmp_path)
    assert result is not None
    assert result["dir_name"] == "backtest_AAPL_2024_new"
    assert len(result["equity_curve"]) == 1
    assert result["equity_curve"][0]["nav"] == "50000.0"
    assert len(result["attribution"]) == 1
    assert result["attribution"][0]["structure_kind"] == "long_call"


def test_v2_backtest_latest_handles_missing_csv_files(tmp_path):
    """Subdir exists but is empty / missing CSVs → returns dict with empty lists."""
    d = tmp_path / "backtest_AAPL_2024"
    d.mkdir()
    result = queries.v2_backtest_latest(tmp_path)
    assert result is not None
    assert result["equity_curve"] == []
    assert result["attribution"] == []


def test_v2_positions_tab_renders_empty_state_for_no_positions():
    html = tabs.v2_positions_tab({"v2_positions": []})
    assert "no open positions" in html.lower() or "no v2 positions" in html.lower()


def test_v2_positions_tab_renders_ticker_and_structure():
    data = {"v2_positions": [{
        "ticker": "AAPL", "intent": "trade", "structure_kind": "long_call",
        "opened_date": "2026-05-10", "days_held": 5,
        "legs_summary": "buy call 100 2026-06-15 x1",
        "profit_target_price": 110.0, "stop_price": 95.0, "time_stop_dte": 21,
        "rationale": "bullish",
        "latest_mtm_value": 425.50, "latest_mtm_source": "bs",
        "latest_mtm_asof_date": "2026-05-14",
    }]}
    html = tabs.v2_positions_tab(data)
    assert "AAPL" in html
    assert "long_call" in html
    assert "buy call 100" in html
    assert "425" in html
    assert "bullish" in html


def test_v2_positions_tab_handles_missing_mtm():
    data = {"v2_positions": [{
        "ticker": "MSFT", "intent": "accumulate", "structure_kind": "csp",
        "opened_date": "2026-05-12", "days_held": 3,
        "legs_summary": "sell put 400 2026-06-15 x1",
        "profit_target_price": None, "stop_price": None, "time_stop_dte": None,
        "rationale": "willing to own at 400",
        "latest_mtm_value": None, "latest_mtm_source": None,
        "latest_mtm_asof_date": None,
    }]}
    html = tabs.v2_positions_tab(data)
    assert "MSFT" in html
    assert "csp" in html
    assert "—" in html  # em-dash for missing MtM


def test_v2_backtest_tab_renders_empty_state_when_no_report():
    html = tabs.v2_backtest_tab({"v2_backtest": None})
    assert "no backtest" in html.lower()


def test_v2_backtest_tab_shows_report_dir_name_and_attribution():
    data = {"v2_backtest": {
        "dir_name": "backtest_AAPL_2024_2025",
        "modified_ts": 1_700_000_000,
        "equity_curve": [
            {"asof_ts": "1700000000", "asof_date": "2023-11-14", "nav": "50000.0"},
            {"asof_ts": "1700086400", "asof_date": "2023-11-15", "nav": "50125.5"},
        ],
        "attribution": [
            {"structure_kind": "long_call", "trade_count": "3", "wins": "2",
             "losses": "1", "win_rate": "0.6667", "total_pnl": "250.0",
             "avg_pnl": "83.33"},
        ],
    }}
    html = tabs.v2_backtest_tab(data)
    assert "backtest_AAPL_2024_2025" in html
    assert "long_call" in html
    assert "250" in html
    assert "0.6667" in html or "66.67" in html
    assert "50125" in html


def test_dashboard_generator_includes_new_tabs(conn, tmp_path, monkeypatch):
    """generator source contains v2_positions + v2_backtest wiring."""
    from bullbot.dashboard import generator
    import inspect
    src = inspect.getsource(generator)
    assert "v2_positions" in src
    assert "v2_backtest" in src


def test_dashboard_templates_includes_new_tab_labels():
    from bullbot.dashboard import templates
    import inspect
    src = inspect.getsource(templates)
    assert '"v2_positions"' in src or "'v2_positions'" in src
    assert '"v2_backtest"' in src or "'v2_backtest'" in src
