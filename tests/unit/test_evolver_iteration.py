"""Unit tests for bullbot.evolver.iteration — Phase 2 model persistence."""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

from bullbot.db import migrations
from bullbot.evolver import iteration


def test_iteration_persists_proposer_model_on_new_strategy(
    fake_anthropic, monkeypatch, sample_indicators, sample_key_levels
):
    """A successful iteration writes the model used into evolver_proposals.proposer_model."""
    from bullbot import config

    # Pin the A/B helper so AAPL deterministically lands on Sonnet for this test.
    monkeypatch.setattr(
        "bullbot.evolver.ab.pick_proposer_model",
        lambda ticker: "claude-sonnet-4-6",
    )
    # Stub the snapshot builder — bars are out of scope here.
    class _Snap:
        ticker = "AAPL"
        asof_ts = 0
        spot = 195.0
        bars_1d: list = []
        indicators: dict = {}
        atm_greeks: dict = {}
        iv_rank = 50.0
        regime = "up_low_vix"
        chain: list = []
        market_brief = ""
        ticker_brief = ""
    monkeypatch.setattr(
        "bullbot.engine.step._build_snapshot",
        lambda conn, ticker, cursor: _Snap(),
    )
    # Stub walkforward — return deterministic metrics that don't trip the edge gate.
    class _Metrics:
        pf_is = 1.1
        pf_oos = 1.0
        sharpe_is = 0.5
        max_dd_pct = 0.15
        trade_count = 6
        regime_breakdown = {"up_low_vix": 1.0}
    monkeypatch.setattr(
        "bullbot.backtest.walkforward.run_walkforward",
        lambda **kwargs: _Metrics(),
    )
    # Cursor lookup falls through when bars table is empty — we want a fixed value.
    monkeypatch.setattr(iteration, "_get_cursor", lambda conn, ticker: int(time.time()))

    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 21, "short_delta": 0.30, "width": 5,
                   "profit_target_pct": 0.5, "stop_loss_mult": 2.0, "min_dte_close": 7},
        "rationale": "ab-test row",
    }))

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrations.apply_schema(conn)

    iteration.run(conn=conn, anthropic_client=fake_anthropic, data_client=None, ticker="AAPL")

    row = conn.execute(
        "SELECT proposer_model FROM evolver_proposals WHERE ticker='AAPL'"
    ).fetchone()
    assert row is not None
    assert row["proposer_model"] == "claude-sonnet-4-6"


def test_iteration_tags_cost_ledger_with_model(
    fake_anthropic, monkeypatch, sample_indicators, sample_key_levels
):
    """The cost_ledger entry's details JSON should contain the actual model name,
    not the literal string 'proposer'."""
    from bullbot import config

    monkeypatch.setattr(
        "bullbot.evolver.ab.pick_proposer_model",
        lambda ticker: "claude-opus-4-6",
    )
    class _Snap:
        ticker = "SPY"
        asof_ts = 0
        spot = 500.0
        bars_1d: list = []
        indicators: dict = {}
        atm_greeks: dict = {}
        iv_rank = 50.0
        regime = "up_low_vix"
        chain: list = []
        market_brief = ""
        ticker_brief = ""
    monkeypatch.setattr(
        "bullbot.engine.step._build_snapshot",
        lambda conn, ticker, cursor: _Snap(),
    )
    class _Metrics:
        pf_is = 1.1
        pf_oos = 1.0
        sharpe_is = 0.5
        max_dd_pct = 0.15
        trade_count = 6
        regime_breakdown = {"up_low_vix": 1.0}
    monkeypatch.setattr(
        "bullbot.backtest.walkforward.run_walkforward",
        lambda **kwargs: _Metrics(),
    )
    monkeypatch.setattr(iteration, "_get_cursor", lambda conn, ticker: int(time.time()))

    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 21, "short_delta": 0.30, "width": 5,
                   "profit_target_pct": 0.5, "stop_loss_mult": 2.0, "min_dte_close": 7},
        "rationale": "tagged",
    }))

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrations.apply_schema(conn)

    iteration.run(conn=conn, anthropic_client=fake_anthropic, data_client=None, ticker="SPY")

    row = conn.execute(
        "SELECT details FROM cost_ledger WHERE category='llm' AND ticker='SPY'"
    ).fetchone()
    assert row is not None
    payload = json.loads(row["details"])
    assert payload["model"] == "claude-opus-4-6"
