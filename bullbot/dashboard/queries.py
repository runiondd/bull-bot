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


def account_summary(conn: sqlite3.Connection, now: int | None = None) -> dict[str, Any]:
    """Return account-level totals for the KPI strip.

    Reads the most-recent equity snapshot if any; falls back to config
    baseline (INITIAL_CAPITAL_USD + GROWTH_CAPITAL_USD) when no snapshots
    exist. month_to_date is realized P&L on positions closed since the
    1st of the current UTC month. days_to_target is days remaining until
    config.TARGET_DATE.
    """
    import time as _time
    from datetime import datetime, date, timezone
    from bullbot import config

    now = now if now is not None else int(_time.time())

    snap = conn.execute(
        "SELECT total_equity, income_equity, growth_equity FROM equity_snapshots "
        "ORDER BY ts DESC LIMIT 1"
    ).fetchone()

    if snap is not None:
        total_equity = float(snap["total_equity"])
        income_account = float(snap["income_equity"])
        growth_account = float(snap["growth_equity"])
    else:
        income_account = float(config.INITIAL_CAPITAL_USD)
        growth_account = float(config.GROWTH_CAPITAL_USD)
        total_equity = income_account + growth_account

    # Month-to-date realized P&L (paper only)
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    month_start = datetime(now_dt.year, now_dt.month, 1, tzinfo=timezone.utc)
    month_start_ts = int(month_start.timestamp())
    mtd_row = conn.execute(
        "SELECT COALESCE(SUM(pnl_realized), 0) FROM positions "
        "WHERE run_id NOT LIKE 'bt:%' AND closed_at >= ? AND pnl_realized IS NOT NULL",
        (month_start_ts,),
    ).fetchone()
    month_to_date = float(mtd_row[0])

    # Days to target
    target = date.fromisoformat(config.TARGET_DATE)
    today = now_dt.date()
    days_to_target = max(0, (target - today).days)

    return {
        "total_equity": total_equity,
        "income_account": income_account,
        "growth_account": growth_account,
        "target_monthly": config.TARGET_MONTHLY_PNL_USD,
        "month_to_date": month_to_date,
        "days_to_target": days_to_target,
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


# ---------------------------------------------------------------------------
# extended_metrics
# ---------------------------------------------------------------------------


def extended_metrics(conn: sqlite3.Connection, now: int | None = None) -> dict[str, Any]:
    """Return extended dashboard metrics: win rate, profit factor, sharpe, etc.

    All metrics computed on paper positions only (run_id NOT LIKE 'bt:%').
    Empty DB → all zeros, no division-by-zero.
    """
    import time as _time
    now = now if now is not None else int(_time.time())

    # Win/loss aggregates from closed paper positions
    rows = conn.execute(
        "SELECT pnl_realized FROM positions "
        "WHERE run_id NOT LIKE 'bt:%' AND pnl_realized IS NOT NULL"
    ).fetchall()
    pnls = [r[0] for r in rows if r[0] is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    closed_count = len(pnls)
    win_rate = len(wins) / closed_count if closed_count else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    profit_factor = gross_win / gross_loss if gross_loss > 0 else 0.0

    # Sharpe over last 30 daily snapshots (simple impl: mean/stdev of daily delta)
    sharpe_30d = 0.0
    snaps = conn.execute(
        "SELECT total_equity FROM equity_snapshots "
        "ORDER BY ts DESC LIMIT 30"
    ).fetchall()
    if len(snaps) >= 3:
        eqs = [float(r[0]) for r in reversed(snaps)]
        deltas = [eqs[i+1] - eqs[i] for i in range(len(eqs)-1)]
        if len(deltas) > 1:
            mean = sum(deltas) / len(deltas)
            var = sum((d - mean) ** 2 for d in deltas) / (len(deltas) - 1)
            stdev = var ** 0.5
            if stdev > 0:
                sharpe_30d = (mean / stdev) * (252 ** 0.5)  # annualized

    # Trade counts
    paper_count = conn.execute(
        "SELECT COALESCE(SUM(paper_trade_count), 0) FROM ticker_state"
    ).fetchone()[0]
    bt_count = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE run_id LIKE 'bt:%'"
    ).fetchone()[0]

    # LLM spend last 7 days
    cutoff_7d = now - 7 * 86400
    llm_7d = conn.execute(
        "SELECT COALESCE(SUM(amount_usd), 0) FROM cost_ledger "
        "WHERE category='llm' AND ts >= ?", (cutoff_7d,),
    ).fetchone()[0]

    return {
        "sharpe_30d": float(sharpe_30d),
        "win_rate": float(win_rate),
        "avg_win": float(avg_win),
        "avg_loss": float(avg_loss),
        "profit_factor": float(profit_factor),
        "paper_trade_count": int(paper_count),
        "backtest_count": int(bt_count),
        "llm_spend_7d": float(llm_7d),
    }
