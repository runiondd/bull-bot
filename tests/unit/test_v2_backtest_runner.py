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
