"""Backtest replay runner for v2 Phase C.

Single public entry: backtest(conn, ticker, start, end, starting_nav, llm_client)
-> BacktestResult. Walks one ticker through N historical days, calling the
same Phase C agent + validator + exit-rule pipeline as forward mode but
against chains synthesized from bars via synth_chain.synthesize.

LLM responses are cached on disk (sqlite table backtest_llm_cache) so reruns
of the same backtest cost $0 in Anthropic credits. Cache key is sha256 of
the full LLM prompt.
"""
from __future__ import annotations

import hashlib
import sqlite3


def _cache_key(*, prompt: str) -> str:
    """sha256 hex digest of the full LLM prompt — used as the cache PK."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _cache_get(conn: sqlite3.Connection, *, key: str) -> str | None:
    row = conn.execute(
        "SELECT response_text FROM backtest_llm_cache WHERE prompt_sha=?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return row["response_text"]


def _cache_put(conn: sqlite3.Connection, *, key: str, response: str) -> None:
    """INSERT OR REPLACE so re-running with a new response overwrites
    (typically only useful when developing the prompt template)."""
    conn.execute(
        "INSERT OR REPLACE INTO backtest_llm_cache (prompt_sha, response_text) "
        "VALUES (?, ?)",
        (key, response),
    )
    conn.commit()
