"""Smoke test for `bullbot.cli run-v2-daily` — ensures both runner.run_once
(Phase A) and runner_c.run_once_phase_c (Phase C) are invoked."""
from __future__ import annotations

import pytest


def test_cmd_run_v2_daily_invokes_both_runners(monkeypatch):
    from bullbot import cli

    a_called = {"n": 0}
    c_called = {"n": 0}

    def fake_a_run_once(conn):
        a_called["n"] += 1
        return 5

    def fake_c_run_once_phase_c(*, conn, asof_ts, **kwargs):
        c_called["n"] += 1
        return {"pass": 3, "opened": 1}

    monkeypatch.setattr("bullbot.v2.runner.run_once", fake_a_run_once)
    monkeypatch.setattr("bullbot.v2.runner_c.run_once_phase_c", fake_c_run_once_phase_c)

    import sqlite3
    from bullbot.db.migrations import apply_schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_schema(conn)
    monkeypatch.setattr(cli, "_open_db", lambda: conn)

    rc = cli.cmd_run_v2_daily(args=None)
    assert rc == 0
    assert a_called["n"] == 1
    assert c_called["n"] == 1


def test_cmd_run_v2_daily_returns_zero_even_when_phase_c_raises(monkeypatch):
    from bullbot import cli

    def fake_a_run_once(conn):
        return 5

    def fake_c_boom(*, conn, asof_ts, **kwargs):
        raise RuntimeError("anthropic 500")

    monkeypatch.setattr("bullbot.v2.runner.run_once", fake_a_run_once)
    monkeypatch.setattr("bullbot.v2.runner_c.run_once_phase_c", fake_c_boom)

    import sqlite3
    from bullbot.db.migrations import apply_schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_schema(conn)
    monkeypatch.setattr(cli, "_open_db", lambda: conn)

    rc = cli.cmd_run_v2_daily(args=None)
    assert rc == 0
