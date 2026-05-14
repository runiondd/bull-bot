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
        " close_price, mark_to_mkt, opened_at, closed_at, pnl_realized, unrealized_pnl)"
        " VALUES ('paper', 'AAPL', 1, '[]', 1, 2.0, 1.5, 0, 1000, 2000, 50.0, 0.0)",
    )
    # open paper position with current unrealized pnl
    db_conn.execute(
        "INSERT INTO positions (run_id, ticker, strategy_id, legs, contracts, open_price,"
        " mark_to_mkt, opened_at, unrealized_pnl)"
        " VALUES ('paper', 'TSLA', 1, '[]', 1, 3.0, 3.0, 1100, 25.0)",
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
    assert result["realized_pnl"] == pytest.approx(50.0)
    assert result["unrealized_pnl"] == pytest.approx(25.0)
    assert result["paper_pnl"] == pytest.approx(75.0)  # backward-compat sum
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


# ---------- test_equity_curve ----------


def test_equity_curve_returns_recent_snapshots(db_conn, _seed_strategy):
    """30 days of snapshots, most recent last."""
    for i in range(30):
        db_conn.execute(
            "INSERT INTO equity_snapshots (ts, total_equity, income_equity, "
            "growth_equity, realized_pnl, unrealized_pnl) VALUES (?, ?, ?, ?, ?, ?)",
            (i * 86400, 265000 + i * 100, 50000 + i * 50, 215000 + i * 50, i * 50, i * 50),
        )
    result = queries.equity_curve(db_conn, days=30)
    assert len(result) == 30
    assert result[0]["total_equity"] == 265000  # oldest
    assert result[-1]["total_equity"] == 265000 + 29 * 100  # newest


def test_equity_curve_returns_empty_when_no_snapshots(db_conn):
    """Empty DB: empty list, no crash."""
    result = queries.equity_curve(db_conn, days=30)
    assert result == []


def test_equity_curve_respects_days_parameter(db_conn):
    for i in range(50):
        db_conn.execute(
            "INSERT INTO equity_snapshots (ts, total_equity, income_equity, "
            "growth_equity, realized_pnl, unrealized_pnl) VALUES (?, ?, ?, ?, ?, ?)",
            (i * 86400, 265000, 50000, 215000, 0, 0),
        )
    result = queries.equity_curve(db_conn, days=10)
    assert len(result) == 10


# ---------- test_account_summary ----------


def test_account_summary_returns_required_fields(db_conn, _seed_strategy):
    # Seed a snapshot so total_equity/income/growth are populated
    db_conn.execute(
        "INSERT INTO equity_snapshots (ts, total_equity, income_equity, "
        "growth_equity, realized_pnl, unrealized_pnl) VALUES (?, ?, ?, ?, ?, ?)",
        (1_700_000_000, 268_412.18, 51_204.42, 217_207.76, 3_104.55, 1_708.00),
    )
    result = queries.account_summary(db_conn, now=1_700_000_000)
    assert result["total_equity"] == pytest.approx(268_412.18)
    assert result["income_account"] == pytest.approx(51_204.42)
    assert result["growth_account"] == pytest.approx(217_207.76)
    assert result["target_monthly"] == 10_000  # from config
    assert "month_to_date" in result
    assert "days_to_target" in result


def test_account_summary_empty_db_returns_baseline(db_conn):
    """No snapshots: fall back to config baseline so the page still renders."""
    result = queries.account_summary(db_conn, now=1_700_000_000)
    assert result["total_equity"] == 50_000 + 215_000  # INITIAL + GROWTH
    assert result["income_account"] == 50_000
    assert result["growth_account"] == 215_000
    assert result["month_to_date"] == 0


# ---------- test_extended_metrics ----------


def test_extended_metrics_returns_required_keys(db_conn, _seed_strategy):
    # 3 wins, 2 losses
    for pnl in (100, 200, 50, -80, -120):
        db_conn.execute(
            "INSERT INTO positions (run_id, ticker, opened_at, open_price, "
            "mark_to_mkt, pnl_realized, closed_at) VALUES "
            "('paper', 'SPY', 0, 1, 0, ?, 1)", (pnl,),
        )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_trade_count, updated_at) "
        "VALUES ('SPY', 'paper_trial', 5, 0)"
    )
    result = queries.extended_metrics(db_conn)
    expected_keys = {"sharpe_30d", "win_rate", "avg_win", "avg_loss",
                     "profit_factor", "paper_trade_count", "backtest_count",
                     "llm_spend_7d"}
    assert expected_keys.issubset(set(result.keys()))
    assert result["win_rate"] == pytest.approx(0.6)  # 3/5
    assert result["avg_win"] == pytest.approx(116.667, abs=0.01)
    assert result["avg_loss"] == pytest.approx(-100.0)
    assert result["profit_factor"] == pytest.approx(350 / 200)
    assert result["paper_trade_count"] == 5


def test_extended_metrics_empty_db(db_conn):
    """Empty DB: zeros across the board, no division-by-zero."""
    result = queries.extended_metrics(db_conn)
    assert result["win_rate"] == 0
    assert result["avg_win"] == 0
    assert result["avg_loss"] == 0
    assert result["profit_factor"] == 0
    assert result["paper_trade_count"] == 0


# ---------- test_universe_with_edge ----------


def test_universe_with_edge_joins_state_and_strategy(db_conn, _seed_strategy):
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, iteration_count, paper_trade_count, "
        "best_strategy_id, best_pf_is, best_pf_oos, updated_at) "
        "VALUES ('SPY', 'paper_trial', 5, 2, 1, 1.78, 1.42, 0)"
    )
    result = queries.universe_with_edge(db_conn)
    assert len(result) == 1
    r = result[0]
    assert r["ticker"] == "SPY"
    assert r["phase"] == "paper_trial"
    assert r["category"] == "income"  # SPY is income per config.TICKER_CATEGORY
    assert r["strategy"] == "BearPutSpread"
    assert r["edge"]["pf_oos"] == pytest.approx(1.42)
    assert r["edge"]["pf_is"] == pytest.approx(1.78)


def test_universe_with_edge_handles_null_strategy(db_conn):
    """no_edge tickers have no best_strategy_id — must not crash."""
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, iteration_count, paper_trade_count, "
        "updated_at) VALUES ('XLE', 'no_edge', 22, 0, 0)"
    )
    result = queries.universe_with_edge(db_conn)
    assert len(result) == 1
    assert result[0]["strategy"] is None
    assert result[0]["edge"]["pf_oos"] == 0.0  # NULL → 0


def test_universe_with_edge_empty_db(db_conn):
    assert queries.universe_with_edge(db_conn) == []


# ---------- test_leaderboard_entries ----------


def _seed_gated_proposal(
    db_conn,
    *,
    proposal_id: int,
    ticker: str,
    strategy_id: int,
    score_a: float,
    trade_count: int = 10,
    size_units: int = 1,
    max_loss_per_trade: float = 100.0,
    regime_label: str | None = "trending",
    created_at: int = 1000,
) -> None:
    """Insert an evolver_proposals row that satisfies the leaderboard view
    gates: passed_gate=1, trade_count >= 5, score_a IS NOT NULL.
    """
    db_conn.execute(
        "INSERT INTO evolver_proposals (id, ticker, iteration, strategy_id, rationale,"
        " llm_cost_usd, passed_gate, trade_count, score_a, size_units,"
        " max_loss_per_trade, regime_label, created_at)"
        " VALUES (?, ?, 1, ?, 'r', 0.01, 1, ?, ?, ?, ?, ?, ?)",
        (proposal_id, ticker, strategy_id, trade_count, score_a, size_units,
         max_loss_per_trade, regime_label, created_at),
    )


def test_leaderboard_entries_returns_sorted_by_score_a(db_conn, _seed_strategy):
    """Three gated proposals must come back sorted by score_a descending."""
    _seed_gated_proposal(db_conn, proposal_id=10, ticker="SPY",
                         strategy_id=1, score_a=1.40)
    _seed_gated_proposal(db_conn, proposal_id=11, ticker="QQQ",
                         strategy_id=1, score_a=2.10)
    _seed_gated_proposal(db_conn, proposal_id=12, ticker="AAPL",
                         strategy_id=1, score_a=0.85)

    result = queries.leaderboard_entries(db_conn, n=10)

    assert isinstance(result, list)
    assert all(isinstance(r, dict) for r in result)
    assert len(result) == 3
    scores = [r["score_a"] for r in result]
    assert scores == sorted(scores, reverse=True)
    # First entry is the highest score
    assert result[0]["ticker"] == "QQQ"
    assert result[0]["score_a"] == pytest.approx(2.10)
    # Each dict carries the rendering columns the tab needs
    expected_keys = {"proposal_id", "ticker", "class_name", "regime_label",
                     "score_a", "size_units", "max_loss_per_trade",
                     "trade_count", "rank"}
    assert expected_keys.issubset(set(result[0].keys()))


def test_leaderboard_entries_respects_n_limit(db_conn, _seed_strategy):
    for i in range(5):
        _seed_gated_proposal(db_conn, proposal_id=20 + i,
                             ticker=f"T{i}", strategy_id=1,
                             score_a=1.0 + i * 0.1)
    result = queries.leaderboard_entries(db_conn, n=3)
    assert len(result) == 3


def test_leaderboard_entries_empty_when_no_gated_proposals(db_conn, _seed_strategy):
    """Proposal that fails the gate (passed_gate=0) is excluded."""
    db_conn.execute(
        "INSERT INTO evolver_proposals (ticker, iteration, strategy_id, rationale,"
        " llm_cost_usd, passed_gate, trade_count, score_a, created_at)"
        " VALUES ('SPY', 1, 1, 'r', 0.01, 0, 20, 1.5, 1000)",
    )
    assert queries.leaderboard_entries(db_conn, n=10) == []
