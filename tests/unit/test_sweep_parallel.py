"""test_sweep_parallel.py — tests for sweep.sweep (sequential for now; filename
kept for forward-compat with the future parallel implementation)."""
import sqlite3
from types import SimpleNamespace

from bullbot.db.migrations import apply_schema
from bullbot.evolver.sweep import StrategySpec, sweep


def test_sweep_writes_n_rows(monkeypatch, tmp_path):
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

    spec = StrategySpec(
        class_name="PutCreditSpread",
        ranges={
            "short_delta": [0.2, 0.25, 0.3, 0.35],
            "width": [5, 10],
            "dte": [21, 30, 45],
        },
        max_loss_per_trade=350.0,
    )
    written = sweep(
        conn, ticker="META", spec=spec, regime_label="up/low/low",
        portfolio_value=265_000, run_id="test-run",
        proposer_model="claude-sonnet-4-6", n_cells_max=200, n_jobs=2,
    )
    assert written == 24

    n_rows = conn.execute(
        "SELECT COUNT(*) FROM evolver_proposals "
        "WHERE ticker='META' AND regime_label='up/low/low'"
    ).fetchone()[0]
    assert n_rows == 24
