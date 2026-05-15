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


def test_run_daily_refreshes_bars_then_ticks(db_conn, monkeypatch):
    """`run-daily` must refresh bars for tracked tickers, then call scheduler.tick once.

    `discover_tracked_tickers` now returns the union of bars ∪ config.UNIVERSE ∪
    non-retired ticker_state, so a brand-new UNIVERSE ticker gets bootstrapped on
    daily refresh instead of being silently skipped. SPY and TSLA must still be
    in the refresh list, and tick must follow.
    """
    db_conn.execute(
        "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
        "VALUES ('SPY', '1d', 1, 1, 1, 1, 1, 0)"
    )
    db_conn.execute(
        "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
        "VALUES ('TSLA', '1d', 1, 1, 1, 1, 1, 0)"
    )

    calls: list[tuple[str, tuple]] = []

    def fake_refresh(conn, tickers, fetch_fn=None):
        calls.append(("refresh", tuple(sorted(tickers))))
        return {t: 1 for t in tickers}

    def fake_tick(conn, anthropic_client, data_client, universe=None):
        calls.append(("tick", ()))

    monkeypatch.setattr("bullbot.cli._open_db", lambda: db_conn)
    monkeypatch.setattr("bullbot.cli._build_anthropic_client", lambda: object())
    monkeypatch.setattr("bullbot.cli._build_uw_client", lambda: object())
    monkeypatch.setattr("bullbot.data.daily_refresh.refresh_all_bars", fake_refresh)
    monkeypatch.setattr("bullbot.scheduler.tick", fake_tick)

    rc = cli.main(["run-daily"])
    assert rc == 0
    assert len(calls) == 2
    assert calls[0][0] == "refresh"
    refreshed = set(calls[0][1])
    assert {"SPY", "TSLA"}.issubset(refreshed)
    assert calls[1] == ("tick", ())


def test_run_v2_daily_calls_runner(db_conn, monkeypatch):
    """`run-v2-daily` must invoke v2.runner.run_once and return its count."""
    calls: list[int] = []

    def fake_run_once(conn, asof_ts=None):
        calls.append(1)
        return 7

    monkeypatch.setattr("bullbot.cli._open_db", lambda: db_conn)
    monkeypatch.setattr("bullbot.v2.runner.run_once", fake_run_once)

    rc = cli.main(["run-v2-daily"])
    assert rc == 0
    assert calls == [1]
