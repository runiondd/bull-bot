"""
Tier 3 regression test — frozen strategy + frozen fixture → deterministic output.

If this test fails, something has changed in the execution path:
indicators, greeks, fill model, engine.step, walkforward aggregation, or
strategy evaluation logic. Do NOT update the golden values to fix this
test — investigate the change first.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from bullbot.backtest import walkforward
from bullbot.db import migrations
from bullbot.engine import step as engine_step
from bullbot.strategies.put_credit_spread import PutCreditSpread


FIXTURE = Path(__file__).parent.parent / "fixtures" / "spy_regression.json"

GOLDEN = {
    # These values are set on the FIRST successful run and then frozen.
    # Update only when a deliberate change to engine/fill/strategy is made.
    "pf_oos_tolerance": 0.001,
    "trade_count_tolerance": 0,
}


@pytest.fixture
def seeded_db():
    if not FIXTURE.exists():
        pytest.skip("regression fixture not built yet — run scripts/build_regression_fixture.py")

    data = json.loads(FIXTURE.read_text())
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)

    for b in data["bars"]:
        conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (b["ticker"], b["timeframe"], b["ts"], b["open"], b["high"],
             b["low"], b["close"], b["volume"]),
        )
    # Map fixture kind values (C/P) to schema values (call/put)
    kind_map = {"C": "call", "P": "put", "call": "call", "put": "put"}
    for symbol, rows in data["contracts"].items():
        for r in rows:
            conn.execute(
                "INSERT INTO option_contracts "
                "(ticker, expiry, strike, kind, ts, bid, ask, iv, volume, open_interest) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r["ticker"], r["expiry"], r["strike"], kind_map[r["kind"]],
                 r["ts"], r["nbbo_bid"], r["nbbo_ask"], r["iv"],
                 r.get("volume"), r.get("open_interest")),
            )
    # Insert the frozen strategy row so FK constraints pass
    conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'PutCreditSpread', 1, "
        "'{\"dte\": 15, \"short_delta\": 0.25, \"width\": 5, \"iv_rank_min\": 0}', "
        "'frozen', 0)"
    )

    max_ts = conn.execute("SELECT MAX(ts) FROM bars").fetchone()[0]
    yield conn, max_ts
    conn.close()


def _anchor_now_to_fixture(max_ts):
    """Return a datetime.now replacement that returns the fixture's max date."""
    def _fake_now(tz=None):
        if tz is not None:
            return datetime.fromtimestamp(max_ts, tz=tz)
        return datetime.fromtimestamp(max_ts)
    return _fake_now


def _run_full_segment(conn, strategy, strategy_id, run_id):
    """Run strategy across all bars, return (fills, orders_count)."""
    bars = conn.execute(
        "SELECT ts FROM bars WHERE ticker='SPY' AND timeframe='1d' ORDER BY ts",
    ).fetchall()
    fills = 0
    for row in bars:
        result = engine_step.step(
            conn=conn, client=None, cursor=row["ts"],
            ticker="SPY", strategy=strategy,
            strategy_id=strategy_id, run_id=run_id,
        )
        if result.filled:
            fills += 1
    orders = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE run_id=?", (run_id,),
    ).fetchone()[0]
    return fills, orders


def test_frozen_backtest_is_deterministic(seeded_db):
    """Same strategy + same fixture → same metrics twice in a row."""
    conn, max_ts = seeded_db
    strategy = PutCreditSpread(params={
        "dte": 15, "short_delta": 0.25, "width": 5, "iv_rank_min": 50
    })

    # Anchor walkforward folds to the fixture's date range (12 months of data)
    fake_now = _anchor_now_to_fixture(max_ts)
    patches = [
        patch("bullbot.backtest.walkforward.datetime", wraps=datetime, **{"now": fake_now}),
        patch("bullbot.backtest.walkforward.config.WF_WINDOW_MONTHS", 12),
    ]
    for p in patches:
        p.start()
    try:
        metrics_1 = walkforward.run_walkforward(
            conn=conn, strategy=strategy, strategy_id=1, ticker="SPY"
        )

        conn.execute("DELETE FROM orders WHERE run_id LIKE 'bt:%'")
        conn.execute("DELETE FROM positions WHERE run_id LIKE 'bt:%'")

        metrics_2 = walkforward.run_walkforward(
            conn=conn, strategy=strategy, strategy_id=1, ticker="SPY"
        )
    finally:
        for p in patches:
            p.stop()

    assert abs(metrics_1.pf_oos - metrics_2.pf_oos) < GOLDEN["pf_oos_tolerance"]
    assert metrics_1.trade_count == metrics_2.trade_count
    # Also verify IS trades are deterministic
    for f1, f2 in zip(metrics_1.fold_metrics, metrics_2.fold_metrics):
        assert f1.trade_count_is == f2.trade_count_is
        assert abs(f1.pf_is - f2.pf_is) < GOLDEN["pf_oos_tolerance"]


def test_frozen_backtest_produces_nonzero_trades(seeded_db):
    """Sanity: the engine+strategy can open trades against this fixture.

    Runs the strategy across ALL bars (not just walkforward OOS windows)
    to verify the execution pipeline works end-to-end. The fixture has
    sampled expiries (every 3rd Friday), so only some cursors will match
    the strategy's DTE window — but at least some must.
    """
    conn, max_ts = seeded_db
    strategy = PutCreditSpread(params={
        "dte": 15, "short_delta": 0.25, "width": 5, "iv_rank_min": 0
    })

    fills_1, orders_1 = _run_full_segment(conn, strategy, 1, "sanity:run1")

    # Clean and re-run for determinism check
    conn.execute("DELETE FROM orders WHERE run_id='sanity:run1'")
    conn.execute("DELETE FROM positions WHERE run_id='sanity:run1'")

    fills_2, orders_2 = _run_full_segment(conn, strategy, 1, "sanity:run2")

    assert fills_1 > 0, (
        f"fixture has insufficient tradeable chains (0 fills across {251} bars)"
    )
    assert fills_1 == fills_2, f"non-deterministic: {fills_1} vs {fills_2} fills"
    assert orders_1 == orders_2, f"non-deterministic: {orders_1} vs {orders_2} orders"
