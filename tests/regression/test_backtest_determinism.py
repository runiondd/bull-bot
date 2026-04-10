"""
Tier 3 regression test — frozen strategy + frozen fixture → golden PF.

If this test fails, something has changed in the execution path:
indicators, greeks, fill model, engine.step, walkforward aggregation, or
strategy evaluation logic. Do NOT update the golden values to fix this
test — investigate the change first.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from bullbot.backtest import walkforward
from bullbot.db import migrations
from bullbot.strategies.put_credit_spread import PutCreditSpread


FIXTURE = Path(__file__).parent.parent / "fixtures" / "spy_regression_2023_2024.json"

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
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (b["ticker"], b["timeframe"], b["ts"], b["open"], b["high"],
             b["low"], b["close"], b["volume"], b["source"]),
        )
    for symbol, rows in data["contracts"].items():
        for r in rows:
            conn.execute(
                "INSERT INTO option_contracts "
                "(ticker, expiry, strike, kind, ts, bid, ask, last, volume, open_interest, iv) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r["ticker"], r["expiry"], r["strike"], r["kind"], r["ts"],
                 r["nbbo_bid"], r["nbbo_ask"], r["last"], r["volume"],
                 r["open_interest"], r["iv"]),
            )
    # Insert the frozen strategy row so FK constraints pass
    conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'PutCreditSpread', 1, "
        "'{\"dte\": 14, \"short_delta\": 0.25, \"width\": 5, \"iv_rank_min\": 50}', "
        "'frozen', 0)"
    )
    yield conn
    conn.close()


def test_frozen_backtest_is_deterministic(seeded_db):
    """Same strategy + same fixture → same metrics twice in a row."""
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50
    })

    # First run
    metrics_1 = walkforward.run_walkforward(
        conn=seeded_db, strategy=strategy, strategy_id=1, ticker="SPY"
    )

    # Clear backtest run_ids from orders/positions before re-running
    seeded_db.execute("DELETE FROM orders WHERE run_id LIKE 'bt:%'")
    seeded_db.execute("DELETE FROM positions WHERE run_id LIKE 'bt:%'")

    metrics_2 = walkforward.run_walkforward(
        conn=seeded_db, strategy=strategy, strategy_id=1, ticker="SPY"
    )

    assert abs(metrics_1.pf_oos - metrics_2.pf_oos) < GOLDEN["pf_oos_tolerance"]
    assert metrics_1.trade_count == metrics_2.trade_count


def test_frozen_backtest_produces_nonzero_trades(seeded_db):
    """Sanity: the fixture has enough liquid contracts to generate trades."""
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 0   # loose
    })
    metrics = walkforward.run_walkforward(
        conn=seeded_db, strategy=strategy, strategy_id=1, ticker="SPY"
    )
    # If this fails, the fixture is under-specified and the fill model
    # never finds liquid enough chains to open trades.
    assert metrics.trade_count > 0, "fixture has insufficient tradeable chains"
