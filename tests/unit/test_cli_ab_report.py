"""Unit tests for `bullbot ab-report` CLI command."""
from __future__ import annotations

import argparse
import sqlite3
import time

import pytest

from bullbot.cli import cmd_ab_report
from bullbot.db import migrations


def _seed_proposals(conn: sqlite3.Connection) -> None:
    """Insert one strategy and 6 proposals across two models for the test."""
    conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'PCS', 1, '{}', 'h', 0)"
    )
    rows = [
        # (ticker, iteration, model, passed_gate, pf_oos, llm_cost_usd)
        ("AAPL", 1, "claude-opus-4-6",   1, 1.40, 0.05),
        ("AAPL", 2, "claude-opus-4-6",   1, 1.55, 0.05),
        ("AAPL", 3, "claude-opus-4-6",   0, 0.95, 0.05),
        ("SPY",  1, "claude-sonnet-4-6", 1, 1.32, 0.012),
        ("SPY",  2, "claude-sonnet-4-6", 0, 1.10, 0.012),
        ("SPY",  3, "claude-sonnet-4-6", 0, 0.85, 0.012),
    ]
    now = int(time.time())
    for ticker, it, model, passed, pf, cost in rows:
        conn.execute(
            "INSERT INTO evolver_proposals "
            "(ticker, iteration, strategy_id, llm_cost_usd, pf_oos, passed_gate, created_at, proposer_model) "
            "VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
            (ticker, it, cost, pf, passed, now, model),
        )
    conn.commit()


def test_ab_report_prints_per_model_stats(monkeypatch, capsys, tmp_path):
    """Output contains both models, with pass-rate, avg pf_oos, and total cost columns."""
    from bullbot import config
    db_path = tmp_path / "bull.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    migrations.apply_schema(conn)
    _seed_proposals(conn)
    conn.close()

    monkeypatch.setattr(config, "DB_PATH", db_path)

    args = argparse.Namespace(days=30)
    rc = cmd_ab_report(args)
    assert rc == 0

    out = capsys.readouterr().out
    assert "claude-opus-4-6" in out
    assert "claude-sonnet-4-6" in out
    # Opus pass rate: 2/3 ≈ 66.7%; Sonnet: 1/3 ≈ 33.3%.
    assert "66.7" in out or "0.667" in out
    assert "33.3" in out or "0.333" in out


def test_ab_report_handles_no_data(monkeypatch, capsys, tmp_path):
    """Empty evolver_proposals → friendly message, no crash."""
    from bullbot import config
    db_path = tmp_path / "bull.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    migrations.apply_schema(conn)
    conn.close()

    monkeypatch.setattr(config, "DB_PATH", db_path)

    args = argparse.Namespace(days=30)
    rc = cmd_ab_report(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "no proposer_model data" in out.lower() or "no proposals" in out.lower()
