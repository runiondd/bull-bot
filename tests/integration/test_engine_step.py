"""Unified engine.step integration test — backtest cursor + paper cursor."""
from datetime import datetime, timezone

from bullbot.data.schemas import Bar, OptionContract
from bullbot.engine import step
from bullbot.strategies.put_credit_spread import PutCreditSpread


def _seed_bars(db_conn, ticker="SPY"):
    """Insert 60 daily bars into the bars table."""
    base_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    for i in range(60):
        ts = base_ts + i * 86400
        price = 500.0 + i * 0.5
        db_conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, ?)",
            (ticker, ts, price, price + 2, price - 1, price + 1, 1_000_000.0),
        )


def _seed_chain(db_conn, ticker="SPY", spot=530.0, asof_ts=None):
    """Insert a synthetic option chain into option_contracts."""
    if asof_ts is None:
        asof_ts = int(datetime(2024, 2, 29, tzinfo=timezone.utc).timestamp())
    expiry = "2024-03-15"
    for strike in [515, 520, 525, 530, 535, 540, 545]:
        for kind in ("put", "call"):
            db_conn.execute(
                "INSERT INTO option_contracts "
                "(ticker, expiry, strike, kind, ts, bid, ask, iv, volume, open_interest) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, expiry, float(strike), kind, asof_ts,
                 1.20, 1.30, 0.18, 1000, 5000),
            )


def test_step_backtest_mode_no_signal_returns_none(db_conn):
    """With iv_rank_min=99, snapshot.iv_rank=50 means no signal fires."""
    _seed_bars(db_conn)
    _seed_chain(db_conn)

    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 99,
    })
    result = step.step(
        conn=db_conn,
        client=None,
        cursor=int(datetime(2024, 2, 29, tzinfo=timezone.utc).timestamp()),
        ticker="SPY",
        strategy=strategy,
        strategy_id=1,
        run_id="bt:test",
    )
    assert result.signal is None
    assert result.filled is False


def test_step_inserts_strategy_row_if_needed(db_conn):
    """Step produces a result even when strategy row already exists in DB."""
    _seed_bars(db_conn)
    _seed_chain(db_conn)

    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'PutCreditSpread', 1, '{}', 'h1', 0)"
    )
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50,
    })
    result = step.step(
        conn=db_conn,
        client=None,
        cursor=int(datetime(2024, 2, 29, tzinfo=timezone.utc).timestamp()),
        ticker="SPY",
        strategy=strategy,
        strategy_id=1,
        run_id="bt:test",
    )
    assert result is not None


def test_step_insufficient_bars_returns_none(db_conn):
    """With fewer than 60 bars, _build_snapshot returns None."""
    # Insert only 10 bars
    base_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    for i in range(10):
        ts = base_ts + i * 86400
        price = 500.0 + i * 0.5
        db_conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, ?)",
            ("SPY", ts, price, price + 2, price - 1, price + 1, 1_000_000.0),
        )
    _seed_chain(db_conn)

    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50,
    })
    result = step.step(
        conn=db_conn,
        client=None,
        cursor=int(datetime(2024, 2, 29, tzinfo=timezone.utc).timestamp()),
        ticker="SPY",
        strategy=strategy,
        strategy_id=1,
        run_id="bt:test",
    )
    assert result.signal is None
    assert result.filled is False
