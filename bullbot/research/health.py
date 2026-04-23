"""Research health brief: produces a structured summary of bull-bot's
research state after each scheduler tick.

Public API:
    CheckResult           — dataclass, one per check
    HealthBrief           — dataclass with to_markdown() / to_html() renderers
    generate_health_brief — build the full brief from a sqlite connection
    write_latest_brief    — serialize to reports/research_health_<ts>.md

Check functions and helpers are module-private.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timezone

from bullbot import config

log = logging.getLogger("bullbot.research.health")


@dataclass(frozen=True)
class CheckResult:
    title: str
    passed: bool
    findings: list[str]


@dataclass(frozen=True)
class HealthBrief:
    generated_at: int
    header: dict[str, str]
    results: list[CheckResult]

    # Renderers come in later tasks (Tasks 8 and 9).


def _safe_check(fn, conn: sqlite3.Connection | None) -> CheckResult:
    """Run a check function, converting any exception into a failure result.

    The check's title is taken from fn.__name__ so the crash trace is attributable.
    """
    try:
        return fn(conn)
    except Exception as exc:
        log.exception("health check %s crashed", fn.__name__)
        return CheckResult(
            title=fn.__name__,
            passed=False,
            findings=[f"check crashed: {type(exc).__name__}: {exc}"],
        )


def check_data_shortfalls(conn: sqlite3.Connection) -> CheckResult:
    """Flag UNIVERSE tickers with insufficient bar history for walkforward."""
    min_bars = config.HEALTH_MIN_BARS_FOR_WF
    findings: list[str] = []
    for ticker in config.UNIVERSE:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM bars WHERE ticker=? AND timeframe='1d'",
            (ticker,),
        ).fetchone()
        n = row[0] if row else 0
        if n < min_bars:
            findings.append(f"{ticker}: {n} bars (need ~{min_bars} for walkforward)")
    return CheckResult(
        title="Data shortfalls",
        passed=not findings,
        findings=findings,
    )


def check_pf_inf(conn: sqlite3.Connection) -> CheckResult:
    """Flag ticker_state rows whose best_pf_oos is IEEE inf or absurdly large."""
    threshold = config.HEALTH_PF_OOS_ABSURD_THRESHOLD
    rows = conn.execute(
        "SELECT ticker, best_pf_oos, best_strategy_id "
        "FROM ticker_state "
        "WHERE best_pf_oos IS NOT NULL AND best_pf_oos > ?",
        (threshold,),
    ).fetchall()
    findings: list[str] = []
    for row in rows:
        ticker = row[0]
        pf = row[1]
        strat_id = row[2]
        pf_str = "inf" if math.isinf(pf) else f"{pf:.4g}"
        sid_str = f"strategy {strat_id}" if strat_id is not None else "no strategy_id"
        findings.append(
            f"{ticker}: best_pf_oos={pf_str} ({sid_str}) — "
            f"likely sample-size artifact or /0"
        )
    return CheckResult(
        title="pf_oos anomalies",
        passed=not findings,
        findings=findings,
    )


def check_dead_paper_trials(conn: sqlite3.Connection, now: int | None = None) -> CheckResult:
    """Flag tickers promoted to paper_trial that aren't actually trading."""
    now = now if now is not None else int(time.time())
    cutoff = now - config.HEALTH_DEAD_PAPER_DAYS * 86400
    findings: list[str] = []

    # Condition A: promoted (verdict_at set) but paper_started_at never set
    rows_a = conn.execute(
        "SELECT ticker, verdict_at FROM ticker_state "
        "WHERE phase='paper_trial' "
        "  AND paper_started_at IS NULL "
        "  AND verdict_at IS NOT NULL "
        "  AND verdict_at < ?",
        (cutoff,),
    ).fetchall()
    for row in rows_a:
        days = (now - row[1]) // 86400
        findings.append(
            f"{row[0]}: promoted {days} days ago, paper_trial dispatch has never fired"
        )

    # Condition B: paper trading started but zero live trades
    rows_b = conn.execute(
        "SELECT ticker, paper_started_at FROM ticker_state "
        "WHERE phase='paper_trial' "
        "  AND paper_started_at IS NOT NULL "
        "  AND paper_trade_count = 0 "
        "  AND paper_started_at < ?",
        (cutoff,),
    ).fetchall()
    for row in rows_b:
        days = (now - row[1]) // 86400
        findings.append(
            f"{row[0]}: started paper trading {days} days ago, 0 live trades"
        )

    return CheckResult(
        title="Dead paper trials",
        passed=not findings,
        findings=findings,
    )


def check_iteration_failures(conn: sqlite3.Connection, now: int | None = None) -> CheckResult:
    """Flag any iteration_failures rows recorded in the last 24 hours."""
    now = now if now is not None else int(time.time())
    cutoff = now - 86400
    rows = conn.execute(
        "SELECT ticker, exc_type, COUNT(*) AS n "
        "FROM iteration_failures "
        "WHERE ts > ? "
        "GROUP BY ticker, exc_type "
        "ORDER BY n DESC, ticker",
        (cutoff,),
    ).fetchall()
    findings = [
        f"{row[0]}: {row[2]} × {row[1]} (last 24h)"
        for row in rows
    ]
    return CheckResult(
        title="Iteration failures (24h)",
        passed=not findings,
        findings=findings,
    )


def _today_utc_ts() -> int:
    """Unix seconds at 00:00 UTC of the current calendar date."""
    return int(datetime.combine(date.today(), dtime.min, tzinfo=timezone.utc).timestamp())


def _build_header(conn: sqlite3.Connection) -> dict[str, str]:
    today = _today_utc_ts()

    # Universe
    universe_n = len(config.UNIVERSE)
    phase_rows = conn.execute(
        "SELECT phase, COUNT(*) FROM ticker_state GROUP BY phase"
    ).fetchall()
    phase_bits = ", ".join(f"{row[1]} {row[0]}" for row in phase_rows) or "no ticker_state rows"
    universe_line = f"{universe_n} tickers ({phase_bits})"

    # Strategy pool
    total_strats = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    new_today = conn.execute(
        "SELECT COUNT(*) FROM strategies WHERE created_at >= ?", (today,)
    ).fetchone()[0]
    strat_line = f"{total_strats} (+{new_today} today)"

    # LLM spend today
    llm_row = conn.execute(
        "SELECT COALESCE(SUM(amount_usd), 0) FROM cost_ledger "
        "WHERE category='llm' AND ts >= ?", (today,),
    ).fetchone()
    llm_line = f"${llm_row[0]:.2f}"

    # Live positions
    open_row = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE run_id='live' AND closed_at IS NULL"
    ).fetchone()
    closed_today_rows = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(pnl_realized),0) "
        "FROM positions WHERE run_id='live' AND closed_at >= ?", (today,),
    ).fetchone()
    positions_line = (
        f"{open_row[0]} open, {closed_today_rows[0]} closed today "
        f"(${closed_today_rows[1]:.2f} realized)"
    )

    return {
        "Universe": universe_line,
        "Strategy pool": strat_line,
        "LLM spend today": llm_line,
        "Live positions": positions_line,
    }


_CHECKS = (
    check_data_shortfalls,
    check_pf_inf,
    check_dead_paper_trials,
    check_iteration_failures,
)


def generate_health_brief(conn: sqlite3.Connection) -> HealthBrief:
    """Build a HealthBrief by running header + each check under _safe_check."""
    header = _build_header(conn)
    results = [_safe_check(fn, conn) for fn in _CHECKS]
    return HealthBrief(
        generated_at=int(time.time()),
        header=header,
        results=results,
    )
