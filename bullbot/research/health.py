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
from dataclasses import dataclass

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
