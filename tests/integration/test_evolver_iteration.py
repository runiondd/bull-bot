"""Full evolver_iteration integration tests."""
import json
from datetime import datetime, timezone

from bullbot.evolver import iteration


def _seed_ticker_state(db_conn, ticker="SPY", phase="discovering"):
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES (?, ?, 0)",
        (ticker, phase),
    )


def _seed_bars(db_conn, ticker="SPY", n_days=500):
    base_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    for i in range(n_days):
        ts = base_ts + i * 86400
        price = 500.0 + i * 0.3
        db_conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, ?)",
            (ticker, ts, price, price + 2, price - 1, price + 1, 1_000_000),
        )


def test_evolver_iteration_inserts_proposal_row(db_conn, fake_anthropic):
    _seed_ticker_state(db_conn)
    _seed_bars(db_conn)
    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50},
        "rationale": "baseline",
    }))
    iteration.run(conn=db_conn, anthropic_client=fake_anthropic, data_client=None, ticker="SPY")
    rows = db_conn.execute("SELECT * FROM evolver_proposals WHERE ticker='SPY'").fetchall()
    assert len(rows) == 1
    assert rows[0]["iteration"] == 1


def test_evolver_iteration_increments_state_counters(db_conn, fake_anthropic):
    _seed_ticker_state(db_conn)
    _seed_bars(db_conn)
    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50},
        "rationale": "baseline",
    }))
    iteration.run(db_conn, fake_anthropic, None, "SPY")
    state = db_conn.execute("SELECT iteration_count FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert state["iteration_count"] == 1


def test_dedup_short_circuit_fires_on_identical_proposal(db_conn, fake_anthropic):
    _seed_ticker_state(db_conn)
    _seed_bars(db_conn)
    for _ in range(2):
        fake_anthropic.queue_response(json.dumps({
            "class_name": "PutCreditSpread",
            "params": {"dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50},
            "rationale": "same",
        }))
    iteration.run(db_conn, fake_anthropic, None, "SPY")
    iteration.run(db_conn, fake_anthropic, None, "SPY")
    n_strategies = db_conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    n_proposals = db_conn.execute("SELECT COUNT(*) FROM evolver_proposals").fetchone()[0]
    assert n_strategies == 1
    assert n_proposals == 2
