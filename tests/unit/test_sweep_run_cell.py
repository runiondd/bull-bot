import json
import sqlite3
from types import SimpleNamespace

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.evolver.sweep import Cell, StrategySpec, run_cell


def test_run_cell_writes_one_proposal_row(monkeypatch, tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)

    fake_metrics = SimpleNamespace(
        pf_is=1.6,
        pf_oos=1.4,
        sharpe_is=1.1,
        max_dd_pct=0.15,
        trade_count=8,
        regime_breakdown={},
        realized_pnl=400.0,
        max_bp_held=2000.0,
        days_held=30.0,
    )
    monkeypatch.setattr(
        "bullbot.evolver.sweep.walkforward.run_walkforward",
        lambda *a, **kw: fake_metrics,
    )

    cell = Cell(
        class_name="PutCreditSpread",
        params={"short_delta": 0.25, "width": 5, "dte": 30,
                "iv_rank_min": 20, "profit_target_pct": 0.5,
                "stop_loss_mult": 2.0},
    )
    spec = StrategySpec(
        class_name="PutCreditSpread", ranges={},
        max_loss_per_trade=350.0,
    )

    proposal_id = run_cell(
        conn,
        ticker="META",
        cell=cell,
        spec=spec,
        regime_label="up/low/low",
        portfolio_value=265_000,
        run_id="test-run",
        proposer_model="claude-sonnet-4-6",
        iteration=1,
    )

    assert proposal_id is not None

    row = conn.execute(
        "SELECT ticker, regime_label, score_a, size_units, max_loss_per_trade, passed_gate "
        "FROM evolver_proposals WHERE id=?",
        (proposal_id,),
    ).fetchone()
    assert row[0] == "META"
    assert row[1] == "up/low/low"
    assert row[2] > 0          # score_a annualized
    assert row[3] > 0          # sized
    assert row[4] == 350.0
    assert row[5] == 1         # passed_gate


def test_run_cell_handles_same_strategy_across_iterations(monkeypatch, tmp_path):
    """Two run_cell calls with the same cell but different iterations
    produce one strategies row and two evolver_proposals rows."""
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)

    fake_metrics = SimpleNamespace(
        pf_is=1.6, pf_oos=1.4, sharpe_is=1.1, max_dd_pct=0.15,
        trade_count=8, regime_breakdown={},
        realized_pnl=400.0, max_bp_held=2000.0, days_held=30.0,
    )
    monkeypatch.setattr(
        "bullbot.evolver.sweep.walkforward.run_walkforward",
        lambda *a, **kw: fake_metrics,
    )

    cell = Cell(
        class_name="PutCreditSpread",
        params={"short_delta": 0.25, "width": 5, "dte": 30,
                "iv_rank_min": 20, "profit_target_pct": 0.5,
                "stop_loss_mult": 2.0},
    )
    spec = StrategySpec(class_name="PutCreditSpread", ranges={}, max_loss_per_trade=350.0)

    p1 = run_cell(conn, ticker="META", cell=cell, spec=spec,
                  regime_label="up/low/low", portfolio_value=265_000,
                  run_id="run-1", proposer_model="claude-sonnet-4-6", iteration=1)
    p2 = run_cell(conn, ticker="META", cell=cell, spec=spec,
                  regime_label="up/low/low", portfolio_value=265_000,
                  run_id="run-2", proposer_model="claude-sonnet-4-6", iteration=2)

    assert p1 != p2
    strategy_count = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    assert strategy_count == 1  # find-or-insert is idempotent on strategies

    proposal_count = conn.execute("SELECT COUNT(*) FROM evolver_proposals").fetchone()[0]
    assert proposal_count == 2


def test_run_cell_passed_gate_zero_when_strategy_oversized(monkeypatch, tmp_path):
    """A strategy with max-loss exceeding the portfolio cap fails the gate."""
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)

    fake_metrics = SimpleNamespace(
        pf_is=1.6, pf_oos=1.4, sharpe_is=1.1, max_dd_pct=0.15,
        trade_count=8, regime_breakdown={},
        realized_pnl=400.0, max_bp_held=2000.0, days_held=30.0,
    )
    monkeypatch.setattr(
        "bullbot.evolver.sweep.walkforward.run_walkforward",
        lambda *a, **kw: fake_metrics,
    )

    cell = Cell(class_name="PutCreditSpread", params={"short_delta": 0.25})
    # 1 contract loses $10,000 — way more than 2% of $1,000 portfolio ($20 budget)
    spec = StrategySpec(class_name="PutCreditSpread", ranges={}, max_loss_per_trade=10_000.0)

    proposal_id = run_cell(conn, ticker="META", cell=cell, spec=spec,
                           regime_label="up/low/low", portfolio_value=1_000,
                           run_id="test-run", proposer_model="claude-sonnet-4-6", iteration=1)

    row = conn.execute(
        "SELECT passed_gate, size_units FROM evolver_proposals WHERE id=?",
        (proposal_id,),
    ).fetchone()
    assert row[0] == 0  # passed_gate = 0
    assert row[1] == 0  # size_units = 0
