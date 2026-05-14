"""
End-to-end integration: fake LLM payload → parser → sweep → leaderboard query.

Exercises the full Engine A→C path for one ticker with synthetic bars and a
1-cell strategy spec. The fake "LLM payload" is just a dict that mimics what
the proposer would emit; we drive ``parse_proposer_response`` directly so no
LLM is in the loop and no ``_call_llm`` monkeypatch is needed.

Walkforward uses the synthetic options chain (``generate_synthetic_chain``)
when no ``option_contracts`` rows exist for the ticker. The chain's strike
step is $10 for spot > $200, so ``width=10`` is required for the long-leg
lookup to find a matching strike — ``width=5`` would yield zero trades on
synthetic data and fail the leaderboard's ``trade_count >= 5`` gate.

The plan's task text (E.3) used ``width=5`` and a non-existent
``tick_one_ticker`` entry point; per the handoff we adapt to call the
pieces directly and bump ``width`` to 10 so the synthetic chain wires up.
"""
from __future__ import annotations

from datetime import datetime, timezone

from bullbot.evolver.proposer import parse_proposer_response
from bullbot.evolver.sweep import sweep
from bullbot.leaderboard.query import top_n


def _seed_bars(conn, ticker: str = "META", n_days: int = 1260) -> None:
    """Deterministic synthetic daily bars starting 2024-01-01.

    Linear uptrend with a small weekly modulation — matches the pattern used by
    ``tests/integration/test_backtest_determinism.py`` and the existing
    walk-forward fixtures. ``n_days=1260`` (~5 years) gives the 24-month
    walkforward window enough room to produce ≥5 OOS trades after the
    leaderboard gate filters incomplete folds.
    """
    base_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    for i in range(n_days):
        ts = base_ts + i * 86400
        price = 500.0 + i * 0.3 + (i % 7) * 0.5
        conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, ?)",
            (ticker, ts, price, price + 2, price - 1, price + 0.5, 1_000_000),
        )


def test_full_pipeline_proposer_to_leaderboard(db_conn):
    """Fake-LLM payload → parsed spec → 1-cell sweep → leaderboard row.

    With 1260 days of synthetic META bars and a 1-cell PutCreditSpread spec,
    walkforward produces ≥5 trades across folds → ``passed_gate=1`` and
    ``trade_count >= 5`` → the row surfaces in the ``leaderboard`` view.
    """
    _seed_bars(db_conn, ticker="META", n_days=1260)

    payload = {
        "class": "PutCreditSpread",
        "rationale": "test",
        "ranges": {
            "short_delta": [0.25],
            "width": [10],
            "dte": [30],
            "iv_rank_min": [20],
            "profit_target_pct": [0.5],
            "stop_loss_mult": [2.0],
        },
        "max_loss_per_trade": 350.0,
    }

    spec = parse_proposer_response(payload)
    n_successes = sweep(
        db_conn,
        ticker="META",
        spec=spec,
        regime_label="up/low/low",
        portfolio_value=265_000,
        run_id="e3-test",
        proposer_model="fake-llm",
    )
    assert n_successes == 1, f"expected 1 successful cell, got {n_successes}"

    # Diagnostic: inspect the proposal row before querying the gated view, so
    # a future failure (gate change, sizing change, etc.) reports concrete
    # numbers rather than just "len(rows) == 0".
    prop = db_conn.execute(
        "SELECT passed_gate, trade_count, score_a, size_units "
        "FROM evolver_proposals WHERE ticker='META'"
    ).fetchone()
    assert prop is not None, "no evolver_proposals row was written"

    rows = top_n(db_conn, ticker="META")
    assert len(rows) >= 1, (
        f"expected ≥1 leaderboard row; got 0. "
        f"evolver_proposals row: passed_gate={prop['passed_gate']}, "
        f"trade_count={prop['trade_count']}, score_a={prop['score_a']}, "
        f"size_units={prop['size_units']}"
    )
    assert rows[0].ticker == "META"
    assert rows[0].class_name == "PutCreditSpread"
