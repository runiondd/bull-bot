"""Nightly pipeline tests — faithfulness, promotion, kill-switch recompute."""
import time
from bullbot import config, nightly


def test_faithfulness_check_inserts_row(db_conn):
    now = int(time.time())
    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'X', 1, '{}', 'h', 0)"
    )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, paper_trade_count, best_strategy_id, updated_at) "
        "VALUES ('SPY', 'paper_trial', ?, 12, 1, ?)",
        (now - 10 * 86400, now),
    )
    for i, pnl in enumerate([100, -50, 200, -30, 150]):
        db_conn.execute(
            "INSERT INTO positions (run_id, ticker, strategy_id, opened_at, closed_at, "
            "legs, contracts, open_price, close_price, pnl_realized, mark_to_mkt) "
            "VALUES ('paper', 'SPY', 1, ?, ?, '[]', 1, 0, 0, ?, 0)",
            (now - (5 - i) * 86400, now - (5 - i) * 86400 + 3600, pnl),
        )
    nightly.run_all(db_conn)
    checks = db_conn.execute("SELECT * FROM faithfulness_checks").fetchall()
    assert len(checks) >= 1


def test_promotion_to_live_when_all_gates_pass(db_conn):
    now = int(time.time())
    started = now - 22 * 86400
    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'X', 1, '{}', 'h', 0)"
    )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, paper_trade_count, best_strategy_id, updated_at) "
        "VALUES ('SPY', 'paper_trial', ?, 15, 1, ?)",
        (started, now),
    )
    for i in range(5):
        db_conn.execute(
            "INSERT INTO faithfulness_checks (ticker, checked_at, window_days, paper_pf, backtest_pf, delta_pct, passed) "
            "VALUES ('SPY', ?, 5, 1.4, 1.5, -0.067, 1)",
            (now - (5 - i) * 86400,),
        )
    nightly.run_all(db_conn)
    state = db_conn.execute("SELECT phase FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert state["phase"] == "live"


def test_demotion_when_faithfulness_fails(db_conn):
    """Ticker in paper_trial with enough days/trades but recent faithfulness failures → demoted."""
    now = int(time.time())
    started = now - 25 * 86400
    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'X', 1, '{}', 'h', 0)"
    )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, paper_trade_count, best_strategy_id, updated_at) "
        "VALUES ('SPY', 'paper_trial', ?, 15, 1, ?)",
        (started, now),
    )
    # Insert 5 failed faithfulness checks
    for i in range(5):
        db_conn.execute(
            "INSERT INTO faithfulness_checks (ticker, checked_at, window_days, paper_pf, backtest_pf, delta_pct, passed) "
            "VALUES ('SPY', ?, 5, 0.8, 1.5, -0.467, 0)",
            (now - (5 - i) * 86400,),
        )
    nightly.run_all(db_conn)
    state = db_conn.execute("SELECT phase FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert state["phase"] == "discovering"


def test_no_promotion_when_not_enough_days(db_conn):
    """Ticker in paper_trial with < PAPER_TRIAL_DAYS should not be promoted."""
    now = int(time.time())
    started = now - 10 * 86400  # only 10 days, need 21
    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'X', 1, '{}', 'h', 0)"
    )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, paper_trade_count, best_strategy_id, updated_at) "
        "VALUES ('SPY', 'paper_trial', ?, 15, 1, ?)",
        (started, now),
    )
    for i in range(5):
        db_conn.execute(
            "INSERT INTO faithfulness_checks (ticker, checked_at, window_days, paper_pf, backtest_pf, delta_pct, passed) "
            "VALUES ('SPY', ?, 5, 1.4, 1.5, -0.067, 1)",
            (now - (5 - i) * 86400,),
        )
    nightly.run_all(db_conn)
    state = db_conn.execute("SELECT phase FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert state["phase"] == "paper_trial"


def test_no_promotion_when_not_enough_trades(db_conn):
    """Ticker in paper_trial with < PAPER_TRADE_COUNT_MIN should not be promoted."""
    now = int(time.time())
    started = now - 25 * 86400
    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'X', 1, '{}', 'h', 0)"
    )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, paper_trade_count, best_strategy_id, updated_at) "
        "VALUES ('SPY', 'paper_trial', ?, 5, 1, ?)",  # only 5 trades, need 10
        (started, now),
    )
    for i in range(5):
        db_conn.execute(
            "INSERT INTO faithfulness_checks (ticker, checked_at, window_days, paper_pf, backtest_pf, delta_pct, passed) "
            "VALUES ('SPY', ?, 5, 1.4, 1.5, -0.067, 1)",
            (now - (5 - i) * 86400,),
        )
    nightly.run_all(db_conn)
    state = db_conn.execute("SELECT phase FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert state["phase"] == "paper_trial"


def test_run_all_writes_report(db_conn, tmp_path, monkeypatch):
    """run_all should write a markdown report to REPORTS_DIR."""
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    nightly.run_all(db_conn)
    reports = list(tmp_path.glob("nightly_*.md"))
    assert len(reports) >= 1


def test_kill_switch_evaluated_on_run_all(db_conn):
    """run_all trips kill switch when daily loss threshold is exceeded."""
    import time as _time
    now = int(_time.time())
    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'X', 1, '{}', 'h', 0)"
    )
    # Insert a large realized loss for today (live run)
    db_conn.execute(
        "INSERT INTO positions (run_id, ticker, strategy_id, opened_at, closed_at, "
        "legs, contracts, open_price, close_price, pnl_realized, mark_to_mkt) "
        "VALUES ('live', 'SPY', 1, ?, ?, '[]', 1, 0, 0, ?, 0)",
        (now - 3600, now, -2000.0),
    )
    nightly.run_all(db_conn)
    kill_row = db_conn.execute("SELECT active FROM kill_state WHERE id=1").fetchone()
    assert kill_row is not None and kill_row["active"] == 1
