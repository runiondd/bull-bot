"""Data query layer for the Bull-Bot dashboard.

Each function takes a ``sqlite3.Connection`` and returns plain dicts/lists.
Zero external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _parse_json(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _abbreviate_legs(legs: list[dict] | None) -> str:
    """Format legs as 'L 1x SYM / S 1x SYM'."""
    if not legs:
        return ""
    parts = []
    for leg in legs:
        side_char = "L" if leg.get("side", "").lower().startswith("l") else "S"
        qty = leg.get("qty", leg.get("contracts", 1))
        symbol = leg.get("symbol", leg.get("ticker", leg.get("type", "?")))
        parts.append(f"{side_char} {qty}x {symbol}")
    return " / ".join(parts)


# ---------------------------------------------------------------------------
# summary_metrics
# ---------------------------------------------------------------------------


def summary_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return high-level dashboard summary.

    Keys: open_positions, realized_pnl, unrealized_pnl, paper_pnl (sum),
    llm_spend, pnl_by_ticker. Excludes backtest positions (run_id LIKE 'bt:%').

    `unrealized_pnl` reads the positions.unrealized_pnl column populated by
    exit_manager on every tick. `paper_pnl` is kept for backwards compatibility
    as realized_pnl + unrealized_pnl.
    """
    row = conn.execute(
        "SELECT"
        "  COALESCE(SUM(CASE WHEN closed_at IS NULL THEN 1 ELSE 0 END), 0) AS open_positions,"
        "  COALESCE(SUM(COALESCE(pnl_realized, 0)), 0) AS realized_pnl,"
        "  COALESCE(SUM(CASE WHEN closed_at IS NULL THEN COALESCE(unrealized_pnl, 0) ELSE 0 END), 0) AS unrealized_pnl"
        " FROM positions"
        " WHERE run_id NOT LIKE 'bt:%'"
    ).fetchone()

    llm_row = conn.execute(
        "SELECT COALESCE(SUM(cumulative_llm_usd), 0) AS llm_spend FROM ticker_state"
    ).fetchone()

    pnl_by_ticker: list[dict[str, Any]] = []
    for r in conn.execute("""
        SELECT ticker,
               SUM(CASE WHEN closed_at IS NOT NULL THEN COALESCE(pnl_realized, 0) ELSE 0 END) AS realized,
               SUM(CASE WHEN closed_at IS NULL THEN COALESCE(unrealized_pnl, 0) ELSE 0 END) AS unrealized
        FROM positions WHERE run_id NOT LIKE 'bt:%'
        GROUP BY ticker ORDER BY ticker
    """).fetchall():
        pnl_by_ticker.append({
            "ticker": r["ticker"],
            "realized": float(r["realized"]),
            "unrealized": float(r["unrealized"]),
        })

    return {
        "open_positions": row["open_positions"],
        "realized_pnl": row["realized_pnl"],
        "unrealized_pnl": row["unrealized_pnl"],
        "paper_pnl": row["realized_pnl"] + row["unrealized_pnl"],
        "llm_spend": llm_row["llm_spend"],
        "pnl_by_ticker": pnl_by_ticker,
    }


# ---------------------------------------------------------------------------
# ticker_grid
# ---------------------------------------------------------------------------


def ticker_grid(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return list of dicts with ticker lifecycle info + best strategy name."""
    rows = conn.execute(
        "SELECT ts.ticker, ts.phase, ts.iteration_count, ts.paper_trade_count,"
        "       s.class_name AS strategy"
        " FROM ticker_state ts"
        " LEFT JOIN strategies s ON ts.best_strategy_id = s.id"
        " ORDER BY ts.ticker"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# recent_activity
# ---------------------------------------------------------------------------


def recent_activity(
    conn: sqlite3.Connection, limit: int = 20
) -> list[dict[str, Any]]:
    """Merge recent events from proposals, orders, and paper promotions.

    Returns events sorted by timestamp descending.
    """
    events: list[dict[str, Any]] = []

    # evolver proposals
    for r in conn.execute(
        "SELECT created_at AS ts, ticker, 'proposal' AS event_type,"
        "       rationale AS detail"
        " FROM evolver_proposals"
    ).fetchall():
        events.append(_row_to_dict(r))

    # non-backtest orders
    for r in conn.execute(
        "SELECT placed_at AS ts, ticker, 'order' AS event_type,"
        "       intent || ' ' || status AS detail"
        " FROM orders"
        " WHERE run_id NOT LIKE 'bt:%'"
    ).fetchall():
        events.append(_row_to_dict(r))

    # paper promotions (ticker_state rows with paper_started_at set)
    for r in conn.execute(
        "SELECT paper_started_at AS ts, ticker, 'promotion' AS event_type,"
        "       'promoted to paper_trial' AS detail"
        " FROM ticker_state"
        " WHERE paper_started_at IS NOT NULL"
    ).fetchall():
        events.append(_row_to_dict(r))

    events.sort(key=lambda e: e["ts"], reverse=True)
    return events[:limit]


# ---------------------------------------------------------------------------
# evolver_proposals
# ---------------------------------------------------------------------------


def evolver_proposals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all proposals with strategy class_name and parsed params."""
    rows = conn.execute(
        "SELECT ep.*, s.class_name, s.params AS params_json"
        " FROM evolver_proposals ep"
        " JOIN strategies s ON ep.strategy_id = s.id"
        " ORDER BY ep.ticker, ep.iteration"
    ).fetchall()

    result = []
    for r in rows:
        d = _row_to_dict(r)
        d["params"] = _parse_json(d.pop("params_json"))
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# positions_list
# ---------------------------------------------------------------------------


def positions_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all positions with parsed JSON fields and computed flags."""
    rows = conn.execute(
        "SELECT * FROM positions ORDER BY opened_at DESC"
    ).fetchall()

    result = []
    for r in rows:
        d = _row_to_dict(r)
        d["legs"] = _parse_json(d["legs"])
        d["exit_rules"] = _parse_json(d.get("exit_rules"))
        d["is_open"] = d["closed_at"] is None
        d["is_backtest"] = d["run_id"].startswith("bt:")
        d["legs_abbrev"] = _abbreviate_legs(d["legs"])
        bar = conn.execute(
            "SELECT close FROM bars WHERE ticker=? AND timeframe='1d' AND ts<=? ORDER BY ts DESC LIMIT 1",
            (d["ticker"], d["opened_at"]),
        ).fetchone()
        d["entry_spot"] = float(bar["close"]) if bar else None
        proposal = conn.execute(
            "SELECT rationale FROM evolver_proposals WHERE strategy_id=? ORDER BY iteration DESC LIMIT 1",
            (d.get("strategy_id"),),
        ).fetchone()
        d["rationale"] = proposal["rationale"] if proposal else None
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# orders_list
# ---------------------------------------------------------------------------


def orders_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all orders with parsed legs JSON and is_backtest flag."""
    rows = conn.execute(
        "SELECT * FROM orders ORDER BY placed_at DESC"
    ).fetchall()

    result = []
    for r in rows:
        d = _row_to_dict(r)
        d["legs"] = _parse_json(d["legs"])
        d["is_backtest"] = d["run_id"].startswith("bt:")
        d["legs_abbrev"] = _abbreviate_legs(d["legs"])
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# cost_breakdown
# ---------------------------------------------------------------------------


def long_inventory_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return active long inventory positions for dashboard display."""
    try:
        rows = conn.execute(
            "SELECT * FROM long_inventory WHERE removed_at IS NULL ORDER BY account, ticker, kind, expiry"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# cost_breakdown
# ---------------------------------------------------------------------------


def cost_breakdown(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return cost breakdown: LLM per ticker, ledger total, commissions."""
    # LLM costs per ticker from ticker_state
    llm_per_ticker: dict[str, float] = {}
    for r in conn.execute(
        "SELECT ticker, cumulative_llm_usd FROM ticker_state"
        " WHERE cumulative_llm_usd > 0"
    ).fetchall():
        llm_per_ticker[r["ticker"]] = r["cumulative_llm_usd"]

    # total from cost_ledger (category='llm')
    ledger_row = conn.execute(
        "SELECT COALESCE(SUM(amount_usd), 0) AS total FROM cost_ledger"
        " WHERE category = 'llm'"
    ).fetchone()

    # paper commissions (non-backtest orders)
    paper_comm = conn.execute(
        "SELECT COALESCE(SUM(commission), 0) AS total FROM orders"
        " WHERE run_id NOT LIKE 'bt:%'"
    ).fetchone()

    # backtest commissions
    bt_comm = conn.execute(
        "SELECT COALESCE(SUM(commission), 0) AS total FROM orders"
        " WHERE run_id LIKE 'bt:%'"
    ).fetchone()

    return {
        "llm_per_ticker": llm_per_ticker,
        "llm_ledger_total": ledger_row["total"],
        "paper_commissions": paper_comm["total"],
        "backtest_commissions": bt_comm["total"],
    }


# ---------------------------------------------------------------------------
# equity_curve
# ---------------------------------------------------------------------------


def equity_curve(conn: sqlite3.Connection, days: int = 30) -> list[dict[str, Any]]:
    """Return the last `days` equity snapshots, oldest first.

    Reads from equity_snapshots (written by bullbot.research.equity_snapshot
    at the end of every scheduler.tick()). Empty DB → empty list. Caller is
    responsible for handling the empty case (e.g. flat-line chart).
    """
    rows = conn.execute(
        "SELECT ts, total_equity, income_equity, growth_equity, "
        "       realized_pnl, unrealized_pnl "
        "FROM equity_snapshots "
        "ORDER BY ts DESC "
        "LIMIT ?",
        (days,),
    ).fetchall()
    return [_row_to_dict(r) for r in reversed(rows)]
