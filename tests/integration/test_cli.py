"""CLI smoke tests — each subcommand runs without raising."""
import pytest
from bullbot import cli


def test_status_command(db_conn, capsys, monkeypatch):
    monkeypatch.setattr("bullbot.cli._open_db", lambda: db_conn)
    cli.main(["status"])
    captured = capsys.readouterr()
    assert "bullbot" in captured.out.lower() or "no tickers" in captured.out.lower()


def test_add_ticker_inserts_state_row(db_conn, monkeypatch):
    monkeypatch.setattr("bullbot.cli._open_db", lambda: db_conn)
    cli.main(["add-ticker", "SPY"])
    row = db_conn.execute("SELECT phase FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert row is not None
    assert row["phase"] == "discovering"


def test_retire_ticker_sets_flag(db_conn, monkeypatch):
    monkeypatch.setattr("bullbot.cli._open_db", lambda: db_conn)
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('AAPL', 'live', 0)"
    )
    cli.main(["retire-ticker", "AAPL"])
    row = db_conn.execute("SELECT retired FROM ticker_state WHERE ticker='AAPL'").fetchone()
    assert row["retired"] == 1


def test_rearm_requires_ticker_and_flag(db_conn, monkeypatch):
    monkeypatch.setattr("bullbot.cli._open_db", lambda: db_conn)
    db_conn.execute("INSERT INTO kill_state (id, active) VALUES (1, 1)")
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'killed', 0)"
    )
    cli.main(["rearm", "--ticker", "SPY", "--acknowledge-risk"])
    row = db_conn.execute("SELECT active FROM kill_state WHERE id=1").fetchone()
    assert row["active"] == 0
    row2 = db_conn.execute("SELECT phase FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert row2["phase"] == "paper_trial"
