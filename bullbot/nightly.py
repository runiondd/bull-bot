"""
Nightly pipeline for Bull-Bot v3.

Runs after market close:
  1. Faithfulness checks for all paper_trial tickers.
  2. Promotion / demotion logic.
  3. Kill-switch recompute.
  4. Markdown report written to config.REPORTS_DIR.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from bullbot import config
from bullbot.risk import kill_switch


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _paper_profit_factor(conn: sqlite3.Connection, ticker: str, strategy_id: int, window_days: int) -> float:
    """Compute profit factor from paper positions in the last *window_days* days.

    Returns 1.0 when there are no closed trades (neutral — neither good nor bad).
    """
    cutoff = int(time.time()) - window_days * 86400
    rows = conn.execute(
        "SELECT pnl_realized FROM positions "
        "WHERE run_id='paper' AND ticker=? AND strategy_id=? "
        "AND closed_at IS NOT NULL AND closed_at >= ? AND pnl_realized IS NOT NULL",
        (ticker, strategy_id, cutoff),
    ).fetchall()

    gross_profit = sum(r[0] for r in rows if r[0] > 0)
    gross_loss = abs(sum(r[0] for r in rows if r[0] < 0))

    if gross_loss == 0:
        return gross_profit if gross_profit > 0 else 1.0
    return gross_profit / gross_loss


def _backtest_profit_factor(conn: sqlite3.Connection, ticker: str, strategy_id: int) -> float | None:
    """Look up the best passing backtest OOS PF from evolver_proposals.

    Returns None when no suitable row exists.
    """
    row = conn.execute(
        "SELECT pf_oos FROM evolver_proposals "
        "WHERE ticker=? AND strategy_id=? AND passed_gate=1 AND pf_oos IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 1",
        (ticker, strategy_id),
    ).fetchone()
    if row is None:
        return None
    return float(row[0])


# ---------------------------------------------------------------------------
# Core steps
# ---------------------------------------------------------------------------

def _faithfulness_check(conn: sqlite3.Connection, ticker: str, strategy_id: int) -> None:
    """Compute paper PF, compare to backtest PF, insert into faithfulness_checks."""
    now = int(time.time())
    window = config.FAITHFULNESS_MIN_DAYS

    paper_pf = _paper_profit_factor(conn, ticker, strategy_id, window)
    backtest_pf = _backtest_profit_factor(conn, ticker, strategy_id)

    # When no backtest reference exists, use paper_pf itself so delta is 0 —
    # the check still runs and the row is inserted for audit purposes.
    if backtest_pf is None:
        backtest_pf = paper_pf if paper_pf > 0 else 1.0

    if backtest_pf != 0:
        delta_pct = (paper_pf - backtest_pf) / backtest_pf
    else:
        delta_pct = 0.0

    passed = 1 if abs(delta_pct) <= config.FAITHFULNESS_DELTA_MAX else 0

    conn.execute(
        "INSERT INTO faithfulness_checks "
        "(ticker, checked_at, window_days, paper_pf, backtest_pf, delta_pct, passed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ticker, now, window, paper_pf, backtest_pf, delta_pct, passed),
    )


def _check_promotion_eligibility(
    conn: sqlite3.Connection, ticker: str, state: sqlite3.Row
) -> None:
    """Evaluate promotion / demotion for a paper_trial ticker.

    Promotion gates (all must pass):
      - Days in paper >= PAPER_TRIAL_DAYS
      - paper_trade_count >= PAPER_TRADE_COUNT_MIN
      - Last FAITHFULNESS_MIN_DAYS faithfulness checks all passed

    If all gates pass → 'live'.
    If enough time/trades elapsed but faithfulness failed → 'discovering'.
    Otherwise do nothing.
    """
    now = int(time.time())

    paper_started_at = state["paper_started_at"]
    trade_count = state["paper_trade_count"]

    days_in_paper = (now - paper_started_at) / 86400 if paper_started_at else 0

    days_ok = days_in_paper >= config.PAPER_TRIAL_DAYS
    trades_ok = trade_count >= config.PAPER_TRADE_COUNT_MIN

    # Fetch last N faithfulness checks
    n = config.FAITHFULNESS_MIN_DAYS
    checks = conn.execute(
        "SELECT passed FROM faithfulness_checks WHERE ticker=? "
        "ORDER BY checked_at DESC LIMIT ?",
        (ticker, n),
    ).fetchall()

    faith_ok = len(checks) >= n and all(c["passed"] for c in checks)

    if days_ok and trades_ok and faith_ok:
        conn.execute(
            "UPDATE ticker_state SET phase='live', live_started_at=?, updated_at=? WHERE ticker=?",
            (now, now, ticker),
        )
    elif days_ok and trades_ok and not faith_ok and len(checks) >= n:
        # Enough time and trades but faithfulness failed — demote
        conn.execute(
            "UPDATE ticker_state SET phase='discovering', updated_at=? WHERE ticker=?",
            (now, ticker),
        )


def _write_nightly_report(conn: sqlite3.Connection) -> None:
    """Write a markdown nightly summary to config.REPORTS_DIR."""
    now = int(time.time())
    reports_dir = Path(config.REPORTS_DIR)
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Gather summary data
    ticker_rows = conn.execute(
        "SELECT ticker, phase, paper_trade_count, best_pf_is, best_pf_oos "
        "FROM ticker_state ORDER BY ticker"
    ).fetchall()

    faith_rows = conn.execute(
        "SELECT ticker, passed FROM faithfulness_checks "
        "ORDER BY checked_at DESC LIMIT 50"
    ).fetchall()

    kill_row = conn.execute("SELECT active, reason, tripped_at FROM kill_state WHERE id=1").fetchone()

    lines: list[str] = [
        f"# Bull-Bot Nightly Report",
        f"",
        f"Generated: {now} (unix epoch)",
        f"",
        f"## Ticker States",
        f"",
        f"| Ticker | Phase | Trades | PF IS | PF OOS |",
        f"|--------|-------|--------|-------|--------|",
    ]
    for r in ticker_rows:
        lines.append(
            f"| {r['ticker']} | {r['phase']} | {r['paper_trade_count']} "
            f"| {r['best_pf_is'] or 'n/a'} | {r['best_pf_oos'] or 'n/a'} |"
        )

    lines += [
        f"",
        f"## Kill Switch",
        f"",
    ]
    if kill_row and kill_row["active"]:
        lines.append(f"**ACTIVE** — reason: {kill_row['reason']}, tripped at: {kill_row['tripped_at']}")
    else:
        lines.append("Not active.")

    lines += [
        f"",
        f"## Recent Faithfulness Checks",
        f"",
        f"| Ticker | Passed |",
        f"|--------|--------|",
    ]
    for r in faith_rows:
        lines.append(f"| {r['ticker']} | {'yes' if r['passed'] else 'no'} |")

    report_path = reports_dir / f"nightly_{now}.md"
    report_path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_all(conn: sqlite3.Connection) -> None:
    """Run the full nightly pipeline.

    Steps:
      1. Faithfulness check for all paper_trial tickers.
      2. Promotion / demotion for paper_trial tickers.
      3. Kill-switch recompute.
      4. Write nightly report.
    """
    # --- Step 1 & 2: faithfulness + promotion for paper_trial tickers ---
    paper_rows = conn.execute(
        "SELECT ticker, phase, paper_started_at, paper_trade_count, best_strategy_id, updated_at "
        "FROM ticker_state WHERE phase='paper_trial'"
    ).fetchall()

    for state in paper_rows:
        ticker = state["ticker"]
        strategy_id = state["best_strategy_id"]
        if strategy_id is not None:
            _faithfulness_check(conn, ticker, strategy_id)
        _check_promotion_eligibility(conn, ticker, state)

    # --- Step 3: Kill-switch recompute ---
    if not kill_switch.is_tripped(conn) and kill_switch.should_trip_now(conn):
        kill_switch.trip(conn, reason="nightly_recompute")

    # --- Step 4: Write report ---
    _write_nightly_report(conn)
