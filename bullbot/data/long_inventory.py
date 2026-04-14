"""Long inventory queries and Fidelity CSV importer."""
from __future__ import annotations

import csv
import math
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Fidelity account number -> internal name
_ACCOUNT_MAP_BY_NUMBER = {
    "233084385": "ira",
    "X59844055": "taxable",
}

_ACCOUNT_MAP_BY_NAME = {
    "Dan's Brokerage": "taxable",
    "Rollover IRA": "ira",
}

# Symbols to skip during CSV import
_SKIP_PREFIXES = ("SPAXX", "CORE", "FCASH")

# Month abbreviation -> number
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Regex for option-style symbol: " -TSLA270617C300"
_OPTION_SYMBOL_RE = re.compile(r"^\s*-[A-Z]+\d+[CP]\d+")


def active_inventory(
    conn: sqlite3.Connection, ticker: str, account: Optional[str] = None
) -> list[dict]:
    """Return active (not removed) inventory rows for *ticker*."""
    sql = "SELECT * FROM long_inventory WHERE ticker = ? AND removed_at IS NULL"
    params: list = [ticker]
    if account is not None:
        sql += " AND account = ?"
        params.append(account)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def total_coverable_contracts(
    conn: sqlite3.Connection, ticker: str, account: Optional[str] = None
) -> int:
    """Sum of call quantities + floor(shares / 100) for active inventory."""
    rows = active_inventory(conn, ticker, account)
    calls = sum(int(r["quantity"]) for r in rows if r["kind"] == "call")
    shares = sum(int(r["quantity"]) for r in rows if r["kind"] == "shares")
    return calls + math.floor(shares / 100)


def _parse_call_description(desc: str) -> Optional[tuple[str, float, str]]:
    """Parse a Fidelity call description like 'TSLA JUN 17 2027 $300 CALL'.

    Returns (ticker, strike, expiry_iso) or None.
    """
    parts = desc.strip().split()
    if len(parts) < 6 or parts[-1] != "CALL":
        return None
    ticker = parts[0]
    month_str = parts[1]
    day_str = parts[2]
    year_str = parts[3]
    strike_str = parts[4].lstrip("$").replace(",", "")
    month = _MONTHS.get(month_str.upper())
    if month is None:
        return None
    try:
        strike = float(strike_str)
        day = int(day_str)
        year = int(year_str)
    except ValueError:
        return None
    expiry = f"{year:04d}-{month:02d}-{day:02d}"
    return (ticker, strike, expiry)


def seed_from_fidelity_csv(conn: sqlite3.Connection, csv_path: str | Path) -> int:
    """Parse a Fidelity portfolio CSV and insert long call + share positions.

    Returns the count of rows inserted.
    """
    csv_path = Path(csv_path)
    inserted = 0
    now_ts = int(time.time())

    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            symbol = (row.get("Symbol") or "").strip()
            acct_num = (row.get("Account Number") or "").strip()
            desc = (row.get("Description") or "").strip()
            qty_str = (row.get("Quantity") or "0").strip()
            avg_cost_str = (row.get("Average Cost Basis") or "").strip()

            # Resolve account by number or name
            acct_name = (row.get("Account Name") or "").strip()
            account = _ACCOUNT_MAP_BY_NUMBER.get(acct_num) or _ACCOUNT_MAP_BY_NAME.get(acct_name)
            if account is None:
                continue

            # Skip money-market / cash equivalents
            if any(symbol.startswith(p) for p in _SKIP_PREFIXES):
                continue

            # Skip empty / zero quantity
            try:
                quantity = float(qty_str)
            except ValueError:
                continue
            if quantity <= 0:
                continue

            # Parse cost basis
            try:
                cost_basis_per = float(avg_cost_str.replace(",", "").replace("$", ""))
            except (ValueError, AttributeError):
                cost_basis_per = None

            # Determine if this is a call option or shares
            if _OPTION_SYMBOL_RE.match(symbol):
                parsed = _parse_call_description(desc)
                if parsed is None:
                    continue
                ticker, strike, expiry = parsed
                conn.execute(
                    "INSERT INTO long_inventory (account, ticker, kind, strike, expiry, quantity, cost_basis_per, added_at) "
                    "VALUES (?, ?, 'call', ?, ?, ?, ?, ?)",
                    (account, ticker, strike, expiry, quantity, cost_basis_per, now_ts),
                )
                inserted += 1
            else:
                # Treat as equity shares -- skip non-alpha tickers (crypto, etc.)
                if not symbol.isalpha():
                    continue
                conn.execute(
                    "INSERT INTO long_inventory (account, ticker, kind, quantity, cost_basis_per, added_at) "
                    "VALUES (?, ?, 'shares', ?, ?, ?)",
                    (account, symbol, quantity, cost_basis_per, now_ts),
                )
                inserted += 1

    conn.commit()
    return inserted
