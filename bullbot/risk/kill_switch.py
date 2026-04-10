"""Kill switch — trips on daily loss, drawdown, or research ratthole."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import timezone
from pathlib import Path

from bullbot import config
from bullbot.clock import et_now


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _realized_loss_today(conn: sqlite3.Connection) -> float:
    """Return total realized loss (positive = loss) on run_id='live' today in ET."""
    now_et = et_now()
    start_of_day_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    start_epoch = int(start_of_day_et.astimezone(timezone.utc).timestamp())
    row = conn.execute(
        "SELECT COALESCE(SUM(pnl_realized), 0) FROM positions "
        "WHERE run_id='live' AND closed_at >= ? AND pnl_realized IS NOT NULL",
        (start_epoch,),
    ).fetchone()
    total = float(row[0])
    # Return as a positive loss value
    return -total if total < 0 else 0.0


def _peak_to_trough_dd(conn: sqlite3.Connection) -> float:
    """Return peak-to-trough drawdown (positive value) on run_id='live'."""
    rows = conn.execute(
        "SELECT pnl_realized FROM positions "
        "WHERE run_id='live' AND pnl_realized IS NOT NULL ORDER BY closed_at ASC"
    ).fetchall()
    if not rows:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for (pnl,) in rows:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _cumulative_llm_usd(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_usd), 0) FROM cost_ledger WHERE category='llm'"
    ).fetchone()
    return float(row[0])


def _count_live_tickers(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM ticker_state WHERE phase='live'"
    ).fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_tripped(conn: sqlite3.Connection) -> bool:
    """Return True if kill_state row exists and active=1."""
    row = conn.execute(
        "SELECT active FROM kill_state WHERE id=1"
    ).fetchone()
    if row is None:
        return False
    return bool(row[0])


def should_trip_now(conn: sqlite3.Connection) -> bool:
    """Evaluate all three trip conditions; return True if any fires."""
    # 1. Daily realized loss
    daily_loss = _realized_loss_today(conn)
    if daily_loss >= config.KILL_DAILY_LOSS_USD:
        return True

    # 2. Peak-to-trough drawdown on live
    dd = _peak_to_trough_dd(conn)
    if dd >= config.KILL_TOTAL_DD_USD:
        return True

    # 3. Research ratthole: LLM spend >= threshold with zero live tickers
    llm_usd = _cumulative_llm_usd(conn)
    live_tickers = _count_live_tickers(conn)
    if llm_usd >= config.KILL_RESEARCH_RATTHOLE_USD and live_tickers == 0:
        return True

    return False


def trip(conn: sqlite3.Connection, reason: str) -> None:
    """Activate the kill switch, mark live tickers as 'killed', write a report."""
    now = int(time.time())
    conn.execute(
        "INSERT OR REPLACE INTO kill_state (id, active, reason, trigger_rule, tripped_at) "
        "VALUES (1, 1, ?, ?, ?)",
        (reason, reason, now),
    )
    # Update all live tickers to 'killed'
    conn.execute(
        "UPDATE ticker_state SET phase='killed', updated_at=? WHERE phase='live'",
        (now,),
    )

    # Write kill report
    reports_dir = config.REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"kill_report_{now}.json"
    report = {
        "tripped_at": now,
        "reason": reason,
        "daily_loss_usd": _realized_loss_today(conn),
        "drawdown_usd": _peak_to_trough_dd(conn),
        "llm_spend_usd": _cumulative_llm_usd(conn),
        "live_tickers_at_trip": _count_live_tickers(conn),
    }
    report_path.write_text(json.dumps(report, indent=2))


def rearm(conn: sqlite3.Connection) -> None:
    """Deactivate the kill switch (manual override)."""
    conn.execute("UPDATE kill_state SET active=0 WHERE id=1")
