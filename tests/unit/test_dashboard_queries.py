"""Tests for bullbot.dashboard.queries — data layer for dashboard generation."""

import json
import sqlite3

import pytest

from bullbot.dashboard import queries


# ---------- fixtures ----------


@pytest.fixture
def _seed_strategy(db_conn):
    """Insert a baseline strategy row and return its id."""
    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at)"
        " VALUES (1, 'BearPutSpread', 1, ?, 'hash1', 1000)",
        [json.dumps({"width": 5, "dte": 45})],
    )
    return 1


# ---------- test_summary_metrics ----------


def test_summary_metrics(db_conn, _seed_strategy):
    # closed paper position with realized pnl
    db_conn.execute(
        "INSERT INTO positions (run_id, ticker, strategy_id, legs, contracts, open_price,"
        " close_price, mark_to_mkt, opened_at, closed_at, pnl_realized)"
        " VALUES ('paper', 'AAPL', 1, '[]', 1, 2.0, 1.5, 0, 1000, 2000, 50.0)",
    )
    # open paper position with mark-to-market
    db_conn.execute(
        "INSERT INTO positions (run_id, ticker, strategy_id, legs, contracts, open_price,"
        " mark_to_mkt, opened_at)"
        " VALUES ('paper', 'TSLA', 1, '[]', 1, 3.0, 25.0, 1100)",
    )
    # backtest position — should be excluded
    db_conn.execute(
        "INSERT INTO positions (run_id, ticker, strategy_id, legs, contracts, open_price,"
        " mark_to_mkt, opened_at, closed_at, pnl_realized)"
        " VALUES ('bt:run1', 'MSFT', 1, '[]', 1, 1.0, 0, 900, 1000, 999.0)",
    )
    # ticker_state with llm costs
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, cumulative_llm_usd, updated_at)"
        " VALUES ('AAPL', 'discovering', 1.25, 1000)",
    )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, cumulative_llm_usd, updated_at)"
        " VALUES ('TSLA', 'paper_trial', 0.75, 1000)",
    )

    result = queries.summary_metrics(db_conn)

    assert result["open_positions"] == 1  # only the open non-backtest one
    assert result["paper_pnl"] == pytest.approx(75.0)  # 50 realized + 25 mark
    assert result["llm_spend"] == pytest.approx(2.0)


# ---------- test_ticker_grid ----------


def test_ticker_grid(db_conn, _seed_strategy):
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, iteration_count, paper_trade_count,"
        " best_strategy_id, updated_at)"
        " VALUES ('AAPL', 'paper_trial', 5, 12, 1, 1000)",
    )

    rows = queries.ticker_grid(db_conn)

    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "AAPL"
    assert r["phase"] == "paper_trial"
    assert r["iteration_count"] == 5
    assert r["paper_trade_count"] == 12
    assert r["strategy"] == "BearPutSpread"


# ---------- test_recent_activity ----------


def test_recent_activity(db_conn, _seed_strategy):
    # evolver proposal
    db_conn.execute(
        "INSERT INTO evolver_proposals (ticker, iteration, strategy_id, rationale,"
        " llm_cost_usd, passed_gate, created_at)"
        " VALUES ('AAPL', 1, 1, 'test rationale', 0.05, 1, 3000)",
    )
    # non-backtest order
    db_conn.execute(
        "INSERT INTO orders (run_id, ticker, strategy_id, intent, status, placed_at)"
        " VALUES ('paper', 'AAPL', 1, 'open', 'filled', 4000)",
    )
    # backtest order — should be excluded
    db_conn.execute(
        "INSERT INTO orders (run_id, ticker, strategy_id, intent, status, placed_at)"
        " VALUES ('bt:run1', 'MSFT', 1, 'open', 'filled', 5000)",
    )
    # ticker promoted to paper
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, updated_at)"
        " VALUES ('TSLA', 'paper_trial', 3500, 3500)",
    )

    events = queries.recent_activity(db_conn, limit=20)

    # should have 3 events (proposal, order, promotion) sorted desc by ts
    assert len(events) == 3
    assert events[0]["ts"] == 4000  # order
    assert events[1]["ts"] == 3500  # promotion
    assert events[2]["ts"] == 3000  # proposal


# ---------- test_evolver_proposals ----------


def test_evolver_proposals(db_conn, _seed_strategy):
    db_conn.execute(
        "INSERT INTO evolver_proposals (ticker, iteration, strategy_id, rationale,"
        " llm_cost_usd, pf_is, pf_oos, sharpe_is, max_dd_pct, trade_count,"
        " passed_gate, created_at)"
        " VALUES ('AAPL', 1, 1, 'good idea', 0.03, 1.5, 1.2, 0.8, 12.0, 30, 1, 2000)",
    )

    rows = queries.evolver_proposals(db_conn)

    assert len(rows) == 1
    r = rows[0]
    assert r["class_name"] == "BearPutSpread"
    assert r["rationale"] == "good idea"
    assert r["params"] == {"width": 5, "dte": 45}
    assert r["pf_is"] == pytest.approx(1.5)


# ---------- test_positions_list ----------


def test_positions_list(db_conn, _seed_strategy):
    legs = json.dumps([{"type": "put", "strike": 100, "side": "long"}])
    exit_rules = json.dumps({"profit_target_pct": 0.5, "max_hold_days": 30})

    # open paper position
    db_conn.execute(
        "INSERT INTO positions (run_id, ticker, strategy_id, legs, contracts, open_price,"
        " mark_to_mkt, exit_rules, opened_at)"
        " VALUES ('paper', 'AAPL', 1, ?, 1, 2.0, 10.0, ?, 1000)",
        [legs, exit_rules],
    )
    # closed backtest position
    db_conn.execute(
        "INSERT INTO positions (run_id, ticker, strategy_id, legs, contracts, open_price,"
        " close_price, mark_to_mkt, exit_rules, opened_at, closed_at, pnl_realized)"
        " VALUES ('bt:abc', 'TSLA', 1, ?, 2, 3.0, 2.0, 0, ?, 500, 600, -100.0)",
        [legs, exit_rules],
    )

    rows = queries.positions_list(db_conn)

    assert len(rows) == 2
    open_pos = [r for r in rows if r["ticker"] == "AAPL"][0]
    bt_pos = [r for r in rows if r["ticker"] == "TSLA"][0]

    assert open_pos["is_open"] is True
    assert open_pos["is_backtest"] is False
    assert open_pos["legs"] == [{"type": "put", "strike": 100, "side": "long"}]
    assert open_pos["exit_rules"]["profit_target_pct"] == 0.5

    assert bt_pos["is_open"] is False
    assert bt_pos["is_backtest"] is True


# ---------- test_orders_list ----------


def test_orders_list(db_conn, _seed_strategy):
    legs = json.dumps([{"type": "call", "strike": 200, "side": "short"}])
    db_conn.execute(
        "INSERT INTO orders (run_id, ticker, strategy_id, intent, legs, status,"
        " commission, pnl_realized, placed_at)"
        " VALUES ('paper', 'AAPL', 1, 'close', ?, 'filled', 1.30, 45.0, 2000)",
        [legs],
    )
    db_conn.execute(
        "INSERT INTO orders (run_id, ticker, strategy_id, intent, legs, status,"
        " commission, pnl_realized, placed_at)"
        " VALUES ('bt:xyz', 'TSLA', 1, 'open', ?, 'filled', 0.65, -10.0, 1500)",
        [legs],
    )

    rows = queries.orders_list(db_conn)

    assert len(rows) == 2
    paper = [r for r in rows if r["ticker"] == "AAPL"][0]
    bt = [r for r in rows if r["ticker"] == "TSLA"][0]

    assert paper["pnl_realized"] == pytest.approx(45.0)
    assert paper["is_backtest"] is False
    assert paper["legs"] == [{"type": "call", "strike": 200, "side": "short"}]

    assert bt["is_backtest"] is True


# ---------- test_cost_breakdown ----------


def test_cost_breakdown(db_conn, _seed_strategy):
    # ticker_state llm costs
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, cumulative_llm_usd, updated_at)"
        " VALUES ('AAPL', 'discovering', 1.50, 1000)",
    )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, cumulative_llm_usd, updated_at)"
        " VALUES ('TSLA', 'paper_trial', 0.80, 1000)",
    )
    # cost_ledger entry
    db_conn.execute(
        "INSERT INTO cost_ledger (ts, category, ticker, amount_usd)"
        " VALUES (1000, 'llm', 'AAPL', 1.50)",
    )
    # paper order with commission
    db_conn.execute(
        "INSERT INTO orders (run_id, ticker, strategy_id, intent, status, commission, placed_at)"
        " VALUES ('paper', 'AAPL', 1, 'open', 'filled', 2.60, 1000)",
    )
    # backtest order with commission
    db_conn.execute(
        "INSERT INTO orders (run_id, ticker, strategy_id, intent, status, commission, placed_at)"
        " VALUES ('bt:run1', 'TSLA', 1, 'open', 'filled', 1.30, 900)",
    )

    result = queries.cost_breakdown(db_conn)

    assert result["llm_per_ticker"]["AAPL"] == pytest.approx(1.50)
    assert result["llm_per_ticker"]["TSLA"] == pytest.approx(0.80)
    assert result["llm_ledger_total"] == pytest.approx(1.50)
    assert result["paper_commissions"] == pytest.approx(2.60)
    assert result["backtest_commissions"] == pytest.approx(1.30)
