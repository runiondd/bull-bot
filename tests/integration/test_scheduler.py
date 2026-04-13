"""Scheduler dispatch tests."""
import json
import time
from dataclasses import dataclass
from bullbot import scheduler
from bullbot.data.schemas import Signal
from bullbot.engine.step import StepResult


def test_tick_dispatches_discovering_to_evolver(db_conn, fake_anthropic, monkeypatch):
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'discovering', 0)"
    )
    called_with = []
    def fake_evolver_run(conn, anthropic_client, data_client, ticker):
        called_with.append(ticker)
    monkeypatch.setattr("bullbot.evolver.iteration.run", fake_evolver_run)
    scheduler.tick(conn=db_conn, anthropic_client=fake_anthropic, data_client=None, universe=["SPY"])
    assert called_with == ["SPY"]


def test_tick_skips_when_kill_switch_tripped(db_conn, fake_anthropic, monkeypatch):
    db_conn.execute("INSERT INTO kill_state (id, active) VALUES (1, 1)")
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'discovering', 0)"
    )
    called = []
    monkeypatch.setattr("bullbot.evolver.iteration.run", lambda *a, **k: called.append(1))
    scheduler.tick(conn=db_conn, anthropic_client=fake_anthropic, data_client=None, universe=["SPY"])
    assert called == []


def test_tick_isolates_per_ticker_exceptions(db_conn, fake_anthropic, monkeypatch):
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('AAPL', 'discovering', 0)"
    )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'discovering', 0)"
    )
    def flaky_run(conn, anthropic_client, data_client, ticker):
        if ticker == "AAPL":
            raise ValueError("boom")
    monkeypatch.setattr("bullbot.evolver.iteration.run", flaky_run)
    scheduler.tick(db_conn, fake_anthropic, None, ["AAPL", "SPY"])
    rows = db_conn.execute("SELECT ticker FROM iteration_failures").fetchall()
    assert [r["ticker"] for r in rows] == ["AAPL"]


def _setup_paper_trial(db_conn):
    """Insert a paper_trial ticker with a strategy in the DB."""
    db_conn.execute(
        "INSERT INTO strategies (class_name, class_version, params, params_hash, created_at) "
        "VALUES ('PutCreditSpread', 1, ?, 'abc123', 0)",
        (json.dumps({"dte": 30, "short_delta": 0.25, "width": 5,
                      "iv_rank_min": 30, "profit_target_pct": 0.5,
                      "stop_loss_mult": 2.0, "min_dte_close": 7}),),
    )
    strategy_id = db_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_strategy_id, updated_at) "
        "VALUES ('SPY', 'paper_trial', ?, ?)",
        (strategy_id, int(time.time())),
    )
    return strategy_id


def test_paper_trial_dispatches_to_engine_step(db_conn, fake_anthropic, monkeypatch):
    """paper_trial tickers call engine.step with run_id='paper'."""
    _setup_paper_trial(db_conn)
    calls = []

    def fake_step(conn, client, cursor, ticker, strategy, strategy_id, run_id):
        calls.append({"ticker": ticker, "run_id": run_id, "strategy_id": strategy_id})
        return StepResult(signal=None, filled=False)

    monkeypatch.setattr("bullbot.engine.step.step", fake_step)
    scheduler.tick(db_conn, fake_anthropic, None, universe=["SPY"])

    assert len(calls) == 1
    assert calls[0]["ticker"] == "SPY"
    assert calls[0]["run_id"] == "paper"


def test_paper_trial_sets_paper_started_at(db_conn, fake_anthropic, monkeypatch):
    """First paper dispatch sets paper_started_at."""
    _setup_paper_trial(db_conn)
    monkeypatch.setattr(
        "bullbot.engine.step.step",
        lambda *a, **kw: StepResult(signal=None, filled=False),
    )

    row_before = db_conn.execute("SELECT paper_started_at FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert row_before["paper_started_at"] is None

    scheduler.tick(db_conn, fake_anthropic, None, universe=["SPY"])

    row_after = db_conn.execute("SELECT paper_started_at FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert row_after["paper_started_at"] is not None


def test_paper_trial_increments_trade_count_on_open(db_conn, fake_anthropic, monkeypatch):
    """paper_trade_count increments when engine.step fills an open."""
    _setup_paper_trial(db_conn)
    fake_signal = Signal(
        intent="open", legs=[], strategy_class="PutCreditSpread",
        max_loss_per_contract=500.0, rationale="test",
    )

    def fake_step(conn, client, cursor, ticker, strategy, strategy_id, run_id):
        return StepResult(signal=fake_signal, filled=True, position_id=99)

    monkeypatch.setattr("bullbot.engine.step.step", fake_step)
    scheduler.tick(db_conn, fake_anthropic, None, universe=["SPY"])

    row = db_conn.execute("SELECT paper_trade_count FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert row["paper_trade_count"] == 1
