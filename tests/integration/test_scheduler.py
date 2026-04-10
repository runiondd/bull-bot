"""Scheduler dispatch tests."""
import json
from bullbot import scheduler


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
