"""Phase 2 A/B integration test — both models land in evolver_proposals."""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

from bullbot import config
from bullbot.db import migrations
from bullbot.evolver import iteration


def _seed_ticker_state(conn: sqlite3.Connection, tickers: list[str]) -> None:
    now = int(time.time())
    for t in tickers:
        conn.execute(
            "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES (?, 'discovering', ?)",
            (t, now),
        )


def test_phase2_tick_writes_both_models_to_evolver_proposals(
    fake_anthropic, monkeypatch, tmp_path
):
    """Run iteration.run() for two tickers that the helper splits across arms;
    confirm both proposer_model values end up in the table."""

    monkeypatch.setattr(config, "PROPOSER_MODEL_AB_ENABLED", True)
    monkeypatch.setattr(config, "PROPOSER_MODEL_A", "claude-opus-4-6")
    monkeypatch.setattr(config, "PROPOSER_MODEL_B", "claude-sonnet-4-6")

    # Force one ticker onto each arm regardless of the underlying hash.
    chosen = {"AAPL": "claude-opus-4-6", "SPY": "claude-sonnet-4-6"}
    monkeypatch.setattr(
        "bullbot.evolver.ab.pick_proposer_model",
        lambda ticker: chosen[ticker],
    )

    class _Snap:
        ticker = "X"
        asof_ts = 0
        spot = 100.0
        bars_1d: list = []
        indicators: dict = {}
        atm_greeks: dict = {}
        iv_rank = 50.0
        regime = "up_low_vix"
        chain: list = []
        market_brief = ""
        ticker_brief = ""
    def _snap_for(conn, ticker, cursor):
        s = _Snap()
        s.ticker = ticker
        return s
    monkeypatch.setattr("bullbot.engine.step._build_snapshot", _snap_for)

    class _Metrics:
        pf_is = 1.1
        pf_oos = 1.0
        sharpe_is = 0.5
        max_dd_pct = 0.15
        trade_count = 6
        regime_breakdown = {"up_low_vix": 1.0}
    monkeypatch.setattr("bullbot.backtest.walkforward.run_walkforward", lambda **k: _Metrics())
    monkeypatch.setattr(iteration, "_get_cursor", lambda conn, ticker: int(time.time()))

    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 21, "short_delta": 0.30, "width": 5,
                   "profit_target_pct": 0.5, "stop_loss_mult": 2.0, "min_dte_close": 7},
        "rationale": "phase2-aapl",
    }))
    fake_anthropic.queue_response(json.dumps({
        "class_name": "IronCondor",
        "params": {"dte": 21, "wing_delta": 0.20, "wing_width": 5, "iv_rank_min": 60,
                   "profit_target_pct": 0.5, "stop_loss_mult": 2.0, "min_dte_close": 7},
        "rationale": "phase2-spy",
    }))

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrations.apply_schema(conn)
    _seed_ticker_state(conn, ["AAPL", "SPY"])

    iteration.run(conn=conn, anthropic_client=fake_anthropic, data_client=None, ticker="AAPL")
    iteration.run(conn=conn, anthropic_client=fake_anthropic, data_client=None, ticker="SPY")

    rows = conn.execute(
        "SELECT ticker, proposer_model FROM evolver_proposals ORDER BY ticker"
    ).fetchall()
    assert len(rows) == 2
    by_ticker = {r["ticker"]: r["proposer_model"] for r in rows}
    assert by_ticker == {
        "AAPL": "claude-opus-4-6",
        "SPY":  "claude-sonnet-4-6",
    }
