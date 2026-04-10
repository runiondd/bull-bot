"""Append-only billing log."""
from __future__ import annotations
import json
import sqlite3
from typing import Any


def append(conn, ts, category, ticker, amount_usd, details=None):
    conn.execute(
        "INSERT INTO cost_ledger (ts, category, ticker, amount_usd, details) VALUES (?, ?, ?, ?, ?)",
        (ts, category, ticker, amount_usd, json.dumps(details) if details else None),
    )


def cumulative_llm_usd(conn):
    row = conn.execute("SELECT COALESCE(SUM(amount_usd), 0) FROM cost_ledger WHERE category='llm'").fetchone()
    return float(row[0])


def cumulative_by_ticker(conn, ticker):
    rows = conn.execute(
        "SELECT category, SUM(amount_usd) FROM cost_ledger WHERE ticker=? GROUP BY category", (ticker,)
    ).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def can_afford(conn, proposed_usd, ceiling_usd):
    current = cumulative_llm_usd(conn)
    return (current + proposed_usd) <= ceiling_usd
