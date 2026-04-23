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
import sqlite3
from dataclasses import dataclass

log = logging.getLogger(__name__)


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
