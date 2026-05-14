"""
Regression: run_walkforward is deterministic across repeated calls.

Same DB state + same strategy + same ticker → byte-identical BacktestMetrics.
This guards the parallel sweep design (B.3) where two workers executing the
same cell must produce the same result without coordination.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from bullbot.backtest.walkforward import run_walkforward
from bullbot.strategies.registry import materialize, params_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLASS_NAME = "PutCreditSpread"
_PARAMS = {"dte": 45, "short_delta": 0.25, "width": 5, "iv_rank_min": 20}
_TICKER = "SPY"


def _seed_bars(conn, ticker: str = _TICKER, n_days: int = 500) -> None:
    """Deterministic synthetic bar sequence — same as test_walkforward.py."""
    base_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    for i in range(n_days):
        ts = base_ts + i * 86400
        price = 500.0 + i * 0.3 + (i % 7) * 0.5
        conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, ?)",
            (ticker, ts, price, price + 2, price - 1, price + 0.5, 1_000_000),
        )


def _insert_strategy(conn) -> int:
    """Insert a PutCreditSpread row and return its integer id."""
    phash = params_hash(_PARAMS)
    conn.execute(
        "INSERT INTO strategies (class_name, class_version, params, params_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            _CLASS_NAME,
            1,  # CLASS_VERSION
            json.dumps(_PARAMS),
            phash,
            int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()),
        ),
    )
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_run_walkforward_is_deterministic(db_conn):
    """run_walkforward produces byte-identical BacktestMetrics on repeated calls.

    Each invocation writes orders tagged with a fresh uuid run_id per fold,
    so the two runs do not interfere.  If this test fails it means genuine
    non-determinism was introduced — report BLOCKED, do not add seed hacks.
    """
    _seed_bars(db_conn)
    strategy = materialize(_CLASS_NAME, _PARAMS)
    strategy_id = _insert_strategy(db_conn)

    m1 = run_walkforward(db_conn, strategy, strategy_id, ticker=_TICKER)
    # The second call writes to a different set of run_id-tagged order rows —
    # no teardown needed between calls.
    m2 = run_walkforward(db_conn, strategy, strategy_id, ticker=_TICKER)

    assert m1.pf_is == m2.pf_is, f"pf_is differs: {m1.pf_is} vs {m2.pf_is}"
    assert m1.pf_oos == m2.pf_oos, f"pf_oos differs: {m1.pf_oos} vs {m2.pf_oos}"
    assert m1.sharpe_is == m2.sharpe_is, f"sharpe_is differs: {m1.sharpe_is} vs {m2.sharpe_is}"
    assert m1.max_dd_pct == m2.max_dd_pct, f"max_dd_pct differs: {m1.max_dd_pct} vs {m2.max_dd_pct}"
    assert m1.trade_count == m2.trade_count, f"trade_count differs: {m1.trade_count} vs {m2.trade_count}"
    assert m1.regime_breakdown == m2.regime_breakdown, (
        f"regime_breakdown differs: {m1.regime_breakdown} vs {m2.regime_breakdown}"
    )
    assert m1.cagr_oos == m2.cagr_oos, f"cagr_oos differs: {m1.cagr_oos} vs {m2.cagr_oos}"
    assert m1.sortino_oos == m2.sortino_oos, (
        f"sortino_oos differs: {m1.sortino_oos} vs {m2.sortino_oos}"
    )
    assert m1.realized_pnl == m2.realized_pnl, (
        f"realized_pnl differs: {m1.realized_pnl} vs {m2.realized_pnl}"
    )
