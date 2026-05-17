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
