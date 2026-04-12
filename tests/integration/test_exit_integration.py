"""Integration test: open a position, advance cursor, verify exit fires and PnL recorded."""
import json
import sqlite3

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.engine import step as engine_step
from bullbot.strategies.put_credit_spread import PutCreditSpread
from bullbot.data.schemas import Bar, OptionContract


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def _insert_bar(conn, ticker, ts, close, *, open_=None, high=None, low=None, volume=1000):
    open_ = open_ or close
    high = high or close
    low = low or close
    conn.execute(
        "INSERT OR REPLACE INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
        "VALUES (?, '1d', ?, ?, ?, ?, ?, ?)",
        (ticker, ts, open_, high, low, close, volume),
    )


def _insert_option(conn, ticker, expiry, strike, kind, ts, bid, ask, iv=0.20):
    db_kind = "call" if kind == "C" else "put"
    conn.execute(
        "INSERT OR REPLACE INTO option_contracts "
        "(ticker, expiry, strike, kind, ts, bid, ask, iv) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ticker, expiry, strike, db_kind, ts, bid, ask, iv),
    )


def _insert_strategy(conn):
    conn.execute(
        "INSERT INTO strategies (class_name, class_version, params, params_hash, created_at) "
        "VALUES ('PutCreditSpread', 1, '{}', 'test', 1000)"
    )
    return 1


def test_position_opens_then_exits_on_profit_target(conn):
    """Full cycle: bar data + options -> open position -> price decays -> exit fires -> PnL recorded."""
    strategy_id = _insert_strategy(conn)
    strategy = PutCreditSpread({
        "dte": 30, "short_delta": 0.25, "width": 5, "iv_rank_min": 0,
        "profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7,
    })

    # Insert 80 bars of SPY history (need 60 minimum for snapshot).
    # base_ts = 2025-05-01 00:00:00 UTC -> bar 79 = 2025-07-19
    base_ts = 1746057600  # 2025-05-01 00:00:00 UTC
    for i in range(80):
        _insert_bar(conn, "SPY", base_ts + i * 86400, 560.0 + i * 0.1)

    # Insert option chain expiring 2025-08-18 (~30 DTE from bar 79 = 2025-07-19).
    # dte=30 -> target_exp = 2025-07-19 + 30d = 2025-08-18 (exact match).
    expiry = "2025-08-18"
    open_ts = base_ts + 79 * 86400  # 2025-07-19

    # Short put at 545 (OTM relative to spot ~567.9), long put at 540
    _insert_option(conn, "SPY", expiry, 545.0, "P", open_ts, 2.15, 2.25, 0.20)
    _insert_option(conn, "SPY", expiry, 540.0, "P", open_ts, 0.95, 1.05, 0.20)
    # Additional strikes so delta selection has candidates
    for strike in [550.0, 555.0, 560.0, 565.0]:
        _insert_option(conn, "SPY", expiry, strike, "P", open_ts, 3.0, 3.20, 0.20)

    # Step 1: should open a position
    result1 = engine_step.step(
        conn=conn, client=None, cursor=open_ts, ticker="SPY",
        strategy=strategy, strategy_id=strategy_id, run_id="test",
    )
    assert result1.filled, "Position should have opened"

    # Verify position exists with exit rules
    pos = conn.execute("SELECT * FROM positions WHERE run_id='test' AND closed_at IS NULL").fetchone()
    assert pos is not None
    assert pos["exit_rules"] is not None

    # Step 2: advance cursor 20 days (DTE = 10 > min_dte_close=7, so only profit target fires).
    next_ts = open_ts + 20 * 86400
    _insert_bar(conn, "SPY", next_ts, 575.0)

    # Read the ACTUAL legs opened so we insert decayed prices for the right strikes.
    legs = json.loads(pos["legs"])

    # Decayed prices chosen so spread passes validation (spread/mid < 0.50) and
    # profit target of 50% is met.
    # Short leg: bid=0.10, ask=0.14 -> mid=0.12, frac=0.04/0.12=0.33 ✓
    # Long leg: bid=0.04, ask=0.06 -> mid=0.05, frac=0.02/0.05=0.40 ✓
    #   (bid=0.03,ask=0.05 would give frac=0.02/0.04=0.5000...1 due to float
    #    precision, which falsely fails the > 0.5 guard)
    # close_cost = (short_close + long_close) * 100
    #   short closes at mid+0.01=0.13 (buy-to-close), long at mid-0.01=0.04
    #   close_cost = (0.13 - 0.04) * 100 = 9
    # open credit ≈ 176; unrealized_pnl = 176 - 9 = 167 >= 0.50 * 176 = 88 ✓
    for leg in legs:
        if leg["side"] == "short":
            _insert_option(conn, "SPY", leg["expiry"], leg["strike"], leg["kind"],
                           next_ts, 0.10, 0.14, 0.15)
        else:
            _insert_option(conn, "SPY", leg["expiry"], leg["strike"], leg["kind"],
                           next_ts, 0.04, 0.06, 0.15)

    result2 = engine_step.step(
        conn=conn, client=None, cursor=next_ts, ticker="SPY",
        strategy=strategy, strategy_id=strategy_id, run_id="test",
    )

    # Position should have been closed by exit manager
    closed_pos = conn.execute("SELECT * FROM positions WHERE run_id='test' AND closed_at IS NOT NULL").fetchone()
    assert closed_pos is not None, "Position should have been closed by exit manager"
    assert closed_pos["pnl_realized"] is not None
    assert closed_pos["pnl_realized"] > 0, "Should be a profitable close"

    # Verify close order exists
    close_order = conn.execute("SELECT * FROM orders WHERE run_id='test' AND intent='close'").fetchone()
    assert close_order is not None
    assert close_order["pnl_realized"] > 0
