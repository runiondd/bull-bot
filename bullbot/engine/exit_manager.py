"""
Engine-level exit manager.

Checks open positions against their stored exit rules on every bar.
Executes closes through the existing fill model when conditions are met.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from bullbot.data.schemas import Leg
from bullbot.engine import fill_model

log = logging.getLogger("bullbot.exit_manager")


def check_exits(
    conn: sqlite3.Connection,
    run_id: str,
    ticker: str,
    cursor: int,
    chain_rows: dict[str, dict[str, Any]],
) -> list[int]:
    """Check all open positions for exit conditions. Returns list of closed position IDs."""
    positions = conn.execute(
        "SELECT * FROM positions WHERE run_id=? AND ticker=? AND closed_at IS NULL",
        (run_id, ticker),
    ).fetchall()

    closed_ids: list[int] = []
    for pos in positions:
        exit_rules_raw = pos["exit_rules"]
        if exit_rules_raw is None:
            continue
        rules = json.loads(exit_rules_raw)
        if not rules:
            continue

        legs = [Leg(**l) for l in json.loads(pos["legs"])]
        reason = _should_exit(pos, legs, rules, cursor, chain_rows)
        if reason is None:
            continue

        try:
            _execute_close(conn, pos, legs, run_id, cursor, chain_rows, reason)
            closed_ids.append(pos["id"])
        except fill_model.FillRejected:
            log.debug("exit fill rejected for position %d: %s", pos["id"], reason)

    return closed_ids


def _should_exit(
    pos: sqlite3.Row,
    legs: list[Leg],
    rules: dict[str, Any],
    cursor: int,
    chain_rows: dict[str, dict[str, Any]],
) -> str | None:
    """Return exit reason string if any condition is met, else None."""
    # --- DTE close (check first: no chain pricing needed) ---
    min_dte = rules.get("min_dte_close")
    if min_dte is not None:
        cursor_date = datetime.fromtimestamp(cursor, tz=timezone.utc).date()
        nearest_expiry = min(
            datetime.strptime(l.expiry, "%Y-%m-%d").date() for l in legs
        )
        dte = (nearest_expiry - cursor_date).days
        if dte <= min_dte:
            return f"dte_close: {dte} DTE <= {min_dte}"

    # --- Price-based exits need current mark ---
    try:
        close_cost, _ = fill_model.simulate_close_multi_leg(
            legs, chain_rows, pos["contracts"],
        )
    except (fill_model.FillRejected, KeyError):
        return None

    open_price = pos["open_price"]
    is_credit = open_price < 0
    credit = abs(open_price)

    if is_credit:
        unrealized_pnl = credit - close_cost
    else:
        unrealized_pnl = -close_cost - open_price

    # --- Stop loss ---
    stop_mult = rules.get("stop_loss_mult")
    if stop_mult is not None:
        max_loss = stop_mult * credit if is_credit else stop_mult * open_price
        if unrealized_pnl < 0 and abs(unrealized_pnl) >= max_loss:
            return f"stop_loss: loss ${abs(unrealized_pnl):.2f} >= {stop_mult}x ${credit:.2f}"

    # --- Profit target ---
    target_pct = rules.get("profit_target_pct")
    if target_pct is not None:
        target_profit = target_pct * credit if is_credit else target_pct * open_price
        if unrealized_pnl >= target_profit:
            return f"profit_target: profit ${unrealized_pnl:.2f} >= {target_pct:.0%} of ${credit:.2f}"

    return None


def _execute_close(
    conn: sqlite3.Connection,
    pos: sqlite3.Row,
    legs: list[Leg],
    run_id: str,
    cursor: int,
    chain_rows: dict[str, dict[str, Any]],
    reason: str,
) -> None:
    """Close a position and record the order."""
    net_close, _ = fill_model.simulate_close_multi_leg(
        legs, chain_rows, pos["contracts"],
    )
    pnl = pos["open_price"] - net_close
    comm = fill_model.commission(pos["contracts"], len(legs))

    conn.execute(
        "UPDATE positions SET closed_at=?, close_price=?, pnl_realized=?, mark_to_mkt=0.0 WHERE id=?",
        (cursor, net_close, pnl - comm, pos["id"]),
    )
    conn.execute(
        "INSERT INTO orders (run_id, ticker, strategy_id, placed_at, legs, intent, status, commission, pnl_realized) "
        "VALUES (?, ?, ?, ?, ?, 'close', 'filled', ?, ?)",
        (run_id, pos["ticker"], pos["strategy_id"], cursor, pos["legs"], comm, pnl - comm),
    )
    log.info("exit_manager closed position %d: %s (pnl=%.2f)", pos["id"], reason, pnl - comm)
