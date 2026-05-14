"""Unit tests for scripts/grid_baseline.py — Engine C (control group).

We monkey-patch ``sweep`` so these tests never touch walk-forward,
real strategy materialization, or the live DB. The goal is to verify
the script's *orchestration* contract:

  1. For each (class, ticker) pair it calls ``sweep`` exactly once.
  2. Every call is tagged ``proposer_model='grid:baseline'`` and
     ``regime_label='grid:baseline'``.
  3. A failure on one pair does not abort subsequent pairs.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import grid_baseline  # noqa: E402  (path-mangled import)


_MINI_GRID = {
    "PutCreditSpread": {
        "max_loss_per_trade": 350.0,
        "ranges": {
            "short_delta": [0.20, 0.25],
            "width": [10],
            "dte": [30],
            "iv_rank_min": [20],
            "profit_target_pct": [0.50],
            "stop_loss_mult": [2.0],
        },
    },
    "IronCondor": {
        "max_loss_per_trade": 400.0,
        "ranges": {
            "wing_delta": [0.15, 0.20],
            "wing_width": [10],
            "dte": [30],
            "iv_rank_min": [30],
            "profit_target_pct": [0.50],
            "stop_loss_mult": [2.0],
        },
    },
}


def test_grid_baseline_calls_sweep_once_per_class_ticker_pair(monkeypatch):
    """One sweep() call per (class, ticker) pair, tagged grid:baseline."""
    calls: list[dict] = []

    def fake_sweep(conn, **kwargs):
        calls.append(kwargs)
        return 0  # no successful proposals — return value isn't asserted on

    monkeypatch.setattr(grid_baseline, "sweep", fake_sweep)

    universe = ["META", "SPY"]
    written = grid_baseline.run_grid(
        conn=object(),  # fake_sweep ignores conn
        grid=_MINI_GRID,
        universe=universe,
    )

    # 2 classes x 2 tickers = 4 sweep calls
    assert len(calls) == 4
    assert written == 0

    # Every call carries the grid:baseline tags
    for kw in calls:
        assert kw["proposer_model"] == "grid:baseline"
        assert kw["regime_label"] == "grid:baseline"
        assert kw["ticker"] in universe
        assert kw["spec"].class_name in _MINI_GRID
        assert kw["spec"].max_loss_per_trade == _MINI_GRID[kw["spec"].class_name]["max_loss_per_trade"]
        assert kw["run_id"].startswith("grid:baseline:")

    # Every (class, ticker) pair appears exactly once
    seen = {(kw["spec"].class_name, kw["ticker"]) for kw in calls}
    expected = {(cls, t) for cls in _MINI_GRID for t in universe}
    assert seen == expected


def test_grid_baseline_continues_on_per_pair_failure(monkeypatch):
    """If sweep() raises for one pair, later pairs still run."""
    calls: list[dict] = []

    def fake_sweep(conn, **kwargs):
        calls.append(kwargs)
        # Blow up on the first call only
        if len(calls) == 1:
            raise RuntimeError("simulated sweep crash")
        return 1

    monkeypatch.setattr(grid_baseline, "sweep", fake_sweep)

    universe = ["META", "SPY"]
    # Should not raise — the script's per-pair try/except must isolate the failure
    written = grid_baseline.run_grid(
        conn=object(),
        grid=_MINI_GRID,
        universe=universe,
    )

    # All 4 pairs were attempted despite the first one crashing
    assert len(calls) == 4
    # 1 crash, 3 successful sweep returns of 1 each
    assert written == 3
