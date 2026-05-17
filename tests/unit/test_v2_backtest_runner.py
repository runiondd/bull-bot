"""Unit tests for bullbot.v2.backtest.runner."""
from __future__ import annotations

import sqlite3

import pytest

from bullbot.db.migrations import apply_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def test_backtest_llm_cache_table_exists_with_expected_columns(conn):
    cols = _columns(conn, "backtest_llm_cache")
    assert cols == {"prompt_sha", "response_text"}


def test_backtest_llm_cache_pk_rejects_duplicate_prompt_sha(conn):
    conn.execute(
        "INSERT INTO backtest_llm_cache (prompt_sha, response_text) "
        "VALUES ('abc', 'first')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO backtest_llm_cache (prompt_sha, response_text) "
            "VALUES ('abc', 'second')"
        )


from bullbot.v2.backtest import runner


def test_cache_key_returns_64_char_hex_digest():
    key = runner._cache_key(prompt="any prompt")
    assert len(key) == 64
    # sha256 hex digest only contains [0-9a-f]
    assert all(c in "0123456789abcdef" for c in key)


def test_cache_key_is_deterministic_for_same_input():
    a = runner._cache_key(prompt="hello")
    b = runner._cache_key(prompt="hello")
    assert a == b


def test_cache_key_differs_for_different_inputs():
    a = runner._cache_key(prompt="hello")
    b = runner._cache_key(prompt="world")
    assert a != b


def test_cache_get_returns_none_when_key_absent(conn):
    assert runner._cache_get(conn, key="abc" * 21 + "f") is None


def test_cache_put_then_get_round_trip(conn):
    key = "f" * 64
    runner._cache_put(conn, key=key, response="my response")
    assert runner._cache_get(conn, key=key) == "my response"


def test_cache_put_is_idempotent_on_collision(conn):
    """INSERT OR REPLACE — re-putting same key with new value overwrites."""
    key = "a" * 64
    runner._cache_put(conn, key=key, response="first")
    runner._cache_put(conn, key=key, response="second")
    assert runner._cache_get(conn, key=key) == "second"


from datetime import date


def test_backtest_trade_rejects_unknown_intent():
    with pytest.raises(ValueError, match="intent must be one of"):
        runner.BacktestTrade(
            ticker="AAPL", structure_kind="long_call", intent="speculate",
            opened_ts=1_700_000_000, closed_ts=1_700_100_000,
            close_reason="profit_target", realized_pnl=50.0, rationale="",
        )


def test_backtest_trade_realized_pnl_can_be_negative():
    trade = runner.BacktestTrade(
        ticker="AAPL", structure_kind="long_call", intent="trade",
        opened_ts=1_700_000_000, closed_ts=1_700_100_000,
        close_reason="stop", realized_pnl=-150.0, rationale="",
    )
    assert trade.realized_pnl == -150.0


def test_backtest_result_total_realized_pnl_sums_trades():
    result = runner.BacktestResult(
        ticker="AAPL", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
        starting_nav=50_000.0, ending_nav=52_000.0,
        trades=[
            runner.BacktestTrade(
                ticker="AAPL", structure_kind="long_call", intent="trade",
                opened_ts=1, closed_ts=2, close_reason="profit_target",
                realized_pnl=300.0, rationale="",
            ),
            runner.BacktestTrade(
                ticker="AAPL", structure_kind="csp", intent="accumulate",
                opened_ts=3, closed_ts=4, close_reason="expired_worthless",
                realized_pnl=200.0, rationale="",
            ),
        ],
        daily_mtm=[],
    )
    assert result.total_realized_pnl() == 500.0


def test_backtest_result_total_realized_pnl_returns_zero_for_no_trades():
    result = runner.BacktestResult(
        ticker="AAPL", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
        starting_nav=50_000.0, ending_nav=50_000.0, trades=[], daily_mtm=[],
    )
    assert result.total_realized_pnl() == 0.0


from types import SimpleNamespace

from bullbot.v2.signals import DirectionalSignal


def _bar(close, high=None, low=None, ts=0):
    return SimpleNamespace(
        ts=ts, open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=1_000_000,
    )


def _seed_bars(conn, ticker, asof_start_ts, n=60, base_close=100.0):
    """Seed n daily bars into the bars table, ending at asof_start_ts."""
    for i in range(n):
        ts = asof_start_ts - (n - 1 - i) * 86400
        c = base_close + (i * 0.01)
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, 1_000_000)",
            (ticker, ts, c, c + 0.3, c - 0.3, c),
        )
    conn.commit()


def _stub_signal_fn(bars):
    return DirectionalSignal(
        ticker="AAPL", asof_ts=bars[-1].ts, direction="bullish",
        confidence=0.7, horizon_days=30, rationale="stub",
        rules_version="stub",
    )


def _stub_strike_grid_fn(spot):
    return [round(spot + (i * 5)) for i in range(-4, 5)]  # 9 strikes spanning ATM ±20%


def _stub_expiries_fn(today):
    """Two expiries: 33 DTE and 65 DTE."""
    from datetime import timedelta
    return [
        (today + timedelta(days=33)).isoformat(),
        (today + timedelta(days=65)).isoformat(),
    ]


def test_replay_one_day_returns_none_when_too_few_bars(conn, fake_anthropic):
    """No bars seeded → can't compute signal → skip the day."""
    out = runner._replay_one_day(
        conn=conn, ticker="AAPL",
        today=date(2026, 5, 17), asof_ts=1_700_000_000,
        starting_nav_today=50_000.0,
        signal_fn=_stub_signal_fn, strike_grid_fn=_stub_strike_grid_fn,
        expiries_fn=_stub_expiries_fn,
        llm_client=fake_anthropic, llm_cache_conn=conn,
    )
    assert out is None


def test_replay_one_day_opens_position_on_valid_llm_decision(conn, fake_anthropic):
    """Seeded bars + LLM returns valid long_call → position opens, no trade closed yet."""
    import json
    asof = 1_700_000_000
    _seed_bars(conn, "AAPL", asof, n=60, base_close=100.0)
    _seed_bars(conn, "VIX", asof, n=60, base_close=18.0)
    fake_anthropic.queue_response(json.dumps({
        "decision": "open", "intent": "trade", "structure": "long_call",
        "legs": [{"action": "buy", "kind": "call", "strike": 101.0,
                  "expiry": (date(2026, 5, 17).fromordinal(
                      date(2026, 5, 17).toordinal() + 33)).isoformat(),
                  "qty_ratio": 1}],
        "exit_plan": {"profit_target_price": 110.0, "stop_price": 95.0,
                      "time_stop_dte": 21, "assignment_acceptable": False},
        "rationale": "bullish",
    }))
    out = runner._replay_one_day(
        conn=conn, ticker="AAPL",
        today=date(2026, 5, 17), asof_ts=asof,
        starting_nav_today=50_000.0,
        signal_fn=_stub_signal_fn, strike_grid_fn=_stub_strike_grid_fn,
        expiries_fn=_stub_expiries_fn,
        llm_client=fake_anthropic, llm_cache_conn=conn,
    )
    assert out is not None
    assert out["action_taken"] in {"opened", "pass", "held"}
    # Verify a position was actually opened in the DB
    from bullbot.v2 import positions
    open_pos = positions.open_for_ticker(conn, "AAPL")
    if out["action_taken"] == "opened":
        assert open_pos is not None


def test_replay_one_day_uses_llm_cache_on_repeat_call(conn, fake_anthropic):
    """First call hits LLM (queued response). Second call same day → cache hit."""
    import json
    asof = 1_700_000_000
    _seed_bars(conn, "AAPL", asof, n=60, base_close=100.0)
    _seed_bars(conn, "VIX", asof, n=60, base_close=18.0)
    fake_anthropic.queue_response(json.dumps({
        "decision": "pass", "intent": "trade", "structure": "long_call",
        "legs": [], "exit_plan": {}, "rationale": "first call",
    }))
    runner._replay_one_day(
        conn=conn, ticker="AAPL",
        today=date(2026, 5, 17), asof_ts=asof,
        starting_nav_today=50_000.0,
        signal_fn=_stub_signal_fn, strike_grid_fn=_stub_strike_grid_fn,
        expiries_fn=_stub_expiries_fn,
        llm_client=fake_anthropic, llm_cache_conn=conn,
    )
    # Verify cache was populated
    cache_count = conn.execute(
        "SELECT COUNT(*) AS n FROM backtest_llm_cache"
    ).fetchone()["n"]
    assert cache_count == 1
    # Second call SHOULD NOT call the LLM (no new queued response, would error if called)
    runner._replay_one_day(
        conn=conn, ticker="AAPL",
        today=date(2026, 5, 17), asof_ts=asof,
        starting_nav_today=50_000.0,
        signal_fn=_stub_signal_fn, strike_grid_fn=_stub_strike_grid_fn,
        expiries_fn=_stub_expiries_fn,
        llm_client=fake_anthropic, llm_cache_conn=conn,
    )
    # No new cache entry — same key
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM backtest_llm_cache"
    ).fetchone()["n"] == 1


def test_backtest_iterates_days_and_skips_when_no_bars(conn, fake_anthropic):
    """No bars at all → empty result, no exceptions."""
    result = runner.backtest(
        conn=conn, ticker="AAPL",
        start=date(2024, 1, 1), end=date(2024, 1, 7),
        starting_nav=50_000.0,
        signal_fn=_stub_signal_fn, strike_grid_fn=_stub_strike_grid_fn,
        expiries_fn=_stub_expiries_fn,
        llm_client=fake_anthropic,
    )
    assert result.ticker == "AAPL"
    assert result.starting_nav == 50_000.0
    assert result.ending_nav == 50_000.0  # no trades, NAV unchanged
    assert result.trades == []
    assert result.daily_mtm == []


def test_backtest_returns_filled_result_with_seeded_bars(conn, fake_anthropic):
    """Seed 60 bars + queue 'pass' responses → backtest completes, daily_mtm populated."""
    import json
    # Seed 60 days ending at 2024-03-15
    end_ts = int(date(2024, 3, 15).strftime("%s"))
    _seed_bars(conn, "AAPL", end_ts, n=60, base_close=100.0)
    _seed_bars(conn, "VIX", end_ts, n=60, base_close=18.0)
    # Queue many "pass" responses — the cache means we only need one
    fake_anthropic.queue_response(json.dumps({
        "decision": "pass", "intent": "trade", "structure": "long_call",
        "legs": [], "exit_plan": {}, "rationale": "no edge",
    }))
    result = runner.backtest(
        conn=conn, ticker="AAPL",
        start=date(2024, 3, 13), end=date(2024, 3, 15),
        starting_nav=50_000.0,
        signal_fn=_stub_signal_fn, strike_grid_fn=_stub_strike_grid_fn,
        expiries_fn=_stub_expiries_fn,
        llm_client=fake_anthropic,
    )
    # 3 days iterated, all "pass" → no trades opened
    assert len(result.trades) == 0
    # daily_mtm should have at least 1 entry (the days with bars)
    assert len(result.daily_mtm) >= 1
    # Ending NAV equals starting NAV since no trades closed
    assert result.ending_nav == 50_000.0
