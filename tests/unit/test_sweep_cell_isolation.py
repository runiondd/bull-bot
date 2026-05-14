"""test_sweep_cell_isolation.py — verify that a single bad cell does not abort
the entire sweep and that the failure is recorded in sweep_failures."""
import sqlite3
from types import SimpleNamespace

from bullbot.db.migrations import apply_schema
from bullbot.evolver.sweep import StrategySpec, sweep


def test_one_bad_cell_does_not_kill_sweep(monkeypatch, tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)

    call_count = [0]

    def fake_run(*a, **kw):
        call_count[0] += 1
        if call_count[0] == 5:
            raise RuntimeError("simulated walk-forward crash")
        return SimpleNamespace(
            pf_is=1.5, pf_oos=1.3, sharpe_is=1.0, max_dd_pct=0.2,
            trade_count=6, regime_breakdown={},
            realized_pnl=100.0, max_bp_held=500.0, days_held=30.0,
        )

    monkeypatch.setattr(
        "bullbot.evolver.sweep.walkforward.run_walkforward", fake_run
    )

    # 9 cells, each with a distinct short_delta -> 9 distinct strategy_ids
    spec = StrategySpec(
        class_name="PutCreditSpread",
        ranges={
            "short_delta": [0.1, 0.2, 0.3, 0.4, 0.5, 0.15, 0.25, 0.35, 0.45],
        },
        max_loss_per_trade=300.0,
    )
    written = sweep(
        conn, ticker="META", spec=spec, regime_label="up/low/low",
        portfolio_value=265_000, run_id="test",
        proposer_model="claude-sonnet-4-6",
    )

    # 9 cells - 1 failed = 8 successful proposals + 1 sweep_failures row
    assert written == 8
    failures = conn.execute(
        "SELECT COUNT(*) FROM sweep_failures WHERE ticker='META'"
    ).fetchone()[0]
    assert failures == 1
