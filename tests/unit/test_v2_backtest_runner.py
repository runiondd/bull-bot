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
