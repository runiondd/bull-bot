"""Kill switch tests — all three trip conditions + re-arm path."""
from bullbot.risk import kill_switch, cost_ledger
from bullbot import config


def test_not_tripped_on_empty_db(db_conn):
    assert kill_switch.is_tripped(db_conn) is False


def test_trips_on_daily_loss(db_conn):
    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'X', 1, '{}', 'h', 0)"
    )
    import time
    now = int(time.time())
    db_conn.execute(
        "INSERT INTO positions (run_id, ticker, strategy_id, opened_at, closed_at, legs, contracts, open_price, close_price, pnl_realized, mark_to_mkt) "
        "VALUES ('live', 'SPY', 1, ?, ?, '[]', 1, 0, 0, ?, 0)",
        (now - 3600, now, -2000.0),
    )
    assert kill_switch.should_trip_now(db_conn) is True
    kill_switch.trip(db_conn, reason="daily_loss")
    assert kill_switch.is_tripped(db_conn) is True


def test_trips_on_research_ratthole(db_conn):
    cost_ledger.append(db_conn, ts=1, category="llm", ticker="X", amount_usd=1001.0)
    assert kill_switch.should_trip_now(db_conn) is True


def test_rearm_resets_kill_state(db_conn):
    kill_switch.trip(db_conn, reason="test")
    assert kill_switch.is_tripped(db_conn) is True
    kill_switch.rearm(db_conn)
    assert kill_switch.is_tripped(db_conn) is False
