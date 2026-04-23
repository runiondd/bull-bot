# Research Health Brief Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `bullbot.research.health` module that produces a machine-generated research-health brief at the end of every `scheduler.tick()`, writes it to a markdown archive file, and embeds a rendered HTML version as a new "Health" tab in the daily dashboard.

**Architecture:** Pure-function checks over a sqlite connection compose into a `HealthBrief` dataclass with `to_markdown()` and `to_html()` renderers. The scheduler calls `write_latest_brief(conn)` before the existing dashboard generator; the dashboard generator calls `generate_health_brief(conn).to_html()` inline for a new tab. Four MVP checks: data shortfalls, pf_oos anomalies, dead paper trials, iteration failures. All wrapped in per-check and scheduler-level try/excepts so a crash in the health module never fails the tick.

**Tech Stack:** Python 3.12, stdlib `dataclasses` + `sqlite3` + `datetime` + `html.escape`, `pytest` with the existing `db_conn` conftest fixture.

**Spec:** `docs/superpowers/specs/2026-04-22-research-health-brief-design.md` (read this first — everything below is implementation, not rationale).

---

## Task 1: Add the three config constants

**Files:**
- Modify: `bullbot/config.py` (append near the other tuning constants, after `GROWTH_EDGE_TRADE_COUNT_MIN`)
- Modify: `tests/unit/test_config.py` (add one test)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_config.py`:

```python
def test_health_brief_config():
    assert config.HEALTH_DEAD_PAPER_DAYS == 3
    assert config.HEALTH_MIN_BARS_FOR_WF == config.WF_WINDOW_MONTHS * 21
    assert config.HEALTH_PF_OOS_ABSURD_THRESHOLD == 1e10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_config.py::test_health_brief_config -v`
Expected: FAIL with `AttributeError: module 'bullbot.config' has no attribute 'HEALTH_DEAD_PAPER_DAYS'`.

- [ ] **Step 3: Add the constants**

Append to `bullbot/config.py` after the existing `GROWTH_EDGE_TRADE_COUNT_MIN = 5` line (around line 80):

```python
# --- Research health brief ---

HEALTH_DEAD_PAPER_DAYS = 3
HEALTH_MIN_BARS_FOR_WF = WF_WINDOW_MONTHS * 21   # ~504 for 24mo walkforward window
HEALTH_PF_OOS_ABSURD_THRESHOLD = 1e10            # catches IEEE inf and absurdly-large pf_oos values
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_config.py -v`
Expected: all tests PASS including the new one.

- [ ] **Step 5: Commit**

```bash
git add bullbot/config.py tests/unit/test_config.py
git commit -m "config: add research-health-brief tuning constants"
```

---

## Task 2: Research module skeleton — dataclasses + _safe_check

**Files:**
- Create: `bullbot/research/__init__.py` (empty)
- Create: `bullbot/research/health.py`
- Create: `tests/unit/test_research_health.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_research_health.py`:

```python
"""Unit tests for bullbot.research.health."""
from __future__ import annotations

import sqlite3
import time

import pytest

from bullbot.research import health as H


# --- Dataclasses ------------------------------------------------------------

def test_check_result_is_frozen():
    r = H.CheckResult(title="X", passed=True, findings=[])
    with pytest.raises(Exception):  # FrozenInstanceError subclass of AttributeError
        r.title = "Y"


def test_check_result_findings_empty_when_passed():
    # Convention, not a hard constraint, but most call sites assume this.
    r = H.CheckResult(title="X", passed=True, findings=[])
    assert r.passed is True
    assert r.findings == []


def test_health_brief_holds_structured_state():
    brief = H.HealthBrief(
        generated_at=1_700_000_000,
        header={"Universe": "16 tickers"},
        results=[H.CheckResult(title="X", passed=True, findings=[])],
    )
    assert brief.generated_at == 1_700_000_000
    assert brief.header["Universe"] == "16 tickers"
    assert len(brief.results) == 1


# --- _safe_check ------------------------------------------------------------

def test_safe_check_returns_result_from_healthy_fn():
    def clean(conn):
        return H.CheckResult(title="clean", passed=True, findings=[])
    result = H._safe_check(clean, conn=None)
    assert result.title == "clean"
    assert result.passed is True


def test_safe_check_converts_exception_to_findings():
    def boom(conn):
        raise ValueError("explicit failure")
    result = H._safe_check(boom, conn=None)
    assert result.title == "boom"
    assert result.passed is False
    assert any("ValueError" in f and "explicit failure" in f for f in result.findings)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bullbot.research'`.

- [ ] **Step 3: Create the module**

Create `bullbot/research/__init__.py` as an empty file.

Create `bullbot/research/health.py`:

```python
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
```

- [ ] **Step 4: Run to verify tests pass**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/research/__init__.py bullbot/research/health.py tests/unit/test_research_health.py
git commit -m "research/health: scaffold module with CheckResult, HealthBrief, _safe_check"
```

---

## Task 3: `check_data_shortfalls`

**Files:**
- Modify: `bullbot/research/health.py` (add `check_data_shortfalls`)
- Modify: `tests/unit/test_research_health.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_research_health.py`:

```python
from bullbot import config


def _make_conn_with_bars(bars_by_ticker: dict[str, int]) -> sqlite3.Connection:
    """Minimal DB with a bars table populated by per-ticker row count."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE bars (
            ticker TEXT, timeframe TEXT, ts INTEGER,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER
        )
    """)
    for ticker, n in bars_by_ticker.items():
        for i in range(n):
            c.execute(
                "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
                "VALUES (?, '1d', ?, 100, 101, 99, 100, 0)",
                (ticker, i),
            )
    return c


def test_check_data_shortfalls_passes_when_all_tickers_have_enough_bars(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY", "QQQ"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 10)
    conn = _make_conn_with_bars({"SPY": 50, "QQQ": 20})
    result = H.check_data_shortfalls(conn)
    assert result.passed is True
    assert result.findings == []


def test_check_data_shortfalls_flags_under_threshold_tickers(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY", "XLK", "HYG"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 500)
    conn = _make_conn_with_bars({"SPY": 1000, "XLK": 257, "HYG": 257})
    result = H.check_data_shortfalls(conn)
    assert result.passed is False
    assert len(result.findings) == 2
    assert any("XLK" in f and "257" in f and "500" in f for f in result.findings)
    assert any("HYG" in f for f in result.findings)
    # SPY passes, so no finding for it
    assert not any("SPY" in f for f in result.findings)
```

- [ ] **Step 2: Run to verify tests fail**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v -k data_shortfalls`
Expected: FAIL with `AttributeError: module 'bullbot.research.health' has no attribute 'check_data_shortfalls'`.

- [ ] **Step 3: Implement the check**

Append to `bullbot/research/health.py` (after `_safe_check`):

```python
from bullbot import config


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
```

- [ ] **Step 4: Run to verify tests pass**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v`
Expected: all tests PASS (7 total now).

- [ ] **Step 5: Commit**

```bash
git add bullbot/research/health.py tests/unit/test_research_health.py
git commit -m "research/health: add check_data_shortfalls"
```

---

## Task 4: `check_pf_inf`

**Files:**
- Modify: `bullbot/research/health.py`
- Modify: `tests/unit/test_research_health.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_research_health.py`:

```python
def _make_conn_with_ticker_state() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE ticker_state (
            id INTEGER PRIMARY KEY,
            ticker TEXT UNIQUE,
            phase TEXT,
            iteration_count INTEGER DEFAULT 0,
            plateau_counter INTEGER DEFAULT 0,
            best_strategy_id INTEGER,
            best_pf_is REAL,
            best_pf_oos REAL,
            cumulative_llm_usd REAL DEFAULT 0,
            paper_started_at INTEGER,
            paper_trade_count INTEGER DEFAULT 0,
            live_started_at INTEGER,
            verdict_at INTEGER,
            retired INTEGER DEFAULT 0,
            updated_at INTEGER
        )
    """)
    return c


def test_check_pf_inf_passes_when_all_pf_values_reasonable():
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('SPY', 'paper_trial', 1.8, 10, 0)"
    )
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('QQQ', 'discovering', NULL, NULL, 0)"
    )
    result = H.check_pf_inf(conn)
    assert result.passed is True
    assert result.findings == []


def test_check_pf_inf_flags_infinite_and_absurd_pf_values():
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('AAPL', 'no_edge', ?, 123, 0)", (float("inf"),),
    )
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('TSLA', 'paper_trial', 1e12, 114, 0)"
    )
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('MSFT', 'discovering', 2.5, 99, 0)"
    )
    result = H.check_pf_inf(conn)
    assert result.passed is False
    assert len(result.findings) == 2
    assert any("AAPL" in f and "inf" in f and "123" in f for f in result.findings)
    assert any("TSLA" in f and "114" in f for f in result.findings)
    # MSFT's pf_oos=2.5 is reasonable, should not be flagged
    assert not any("MSFT" in f for f in result.findings)
```

- [ ] **Step 2: Run to verify tests fail**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v -k pf_inf`
Expected: FAIL (missing `check_pf_inf`).

- [ ] **Step 3: Implement the check**

Append to `bullbot/research/health.py`:

```python
import math


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
```

- [ ] **Step 4: Run to verify tests pass**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v`
Expected: all tests PASS (9 total now).

- [ ] **Step 5: Commit**

```bash
git add bullbot/research/health.py tests/unit/test_research_health.py
git commit -m "research/health: add check_pf_inf"
```

---

## Task 5: `check_dead_paper_trials`

**Files:**
- Modify: `bullbot/research/health.py`
- Modify: `tests/unit/test_research_health.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_research_health.py`:

```python
def test_check_dead_paper_trials_passes_when_all_healthy(monkeypatch):
    monkeypatch.setattr(config, "HEALTH_DEAD_PAPER_DAYS", 3)
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    # freshly promoted, not yet past threshold
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, verdict_at, updated_at) "
        "VALUES ('GOOGL', 'paper_trial', NULL, 0, ?, ?)",
        (now - 1 * 86400, now),
    )
    # actively trading
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, updated_at) "
        "VALUES ('SPY', 'paper_trial', ?, 5, ?)",
        (now - 10 * 86400, now),
    )
    result = H.check_dead_paper_trials(conn, now=now)
    assert result.passed is True


def test_check_dead_paper_trials_flags_never_started(monkeypatch):
    monkeypatch.setattr(config, "HEALTH_DEAD_PAPER_DAYS", 3)
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, verdict_at, updated_at) "
        "VALUES ('SATS', 'paper_trial', NULL, 0, ?, ?)",
        (now - 5 * 86400, now),
    )
    result = H.check_dead_paper_trials(conn, now=now)
    assert result.passed is False
    assert len(result.findings) == 1
    assert "SATS" in result.findings[0]
    assert "never fired" in result.findings[0] or "never started" in result.findings[0]


def test_check_dead_paper_trials_flags_zero_trades_after_threshold(monkeypatch):
    monkeypatch.setattr(config, "HEALTH_DEAD_PAPER_DAYS", 3)
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, updated_at) "
        "VALUES ('XLF', 'paper_trial', ?, 0, ?)",
        (now - 5 * 86400, now),
    )
    result = H.check_dead_paper_trials(conn, now=now)
    assert result.passed is False
    assert len(result.findings) == 1
    assert "XLF" in result.findings[0]
    assert "0 live trades" in result.findings[0] or "0 trades" in result.findings[0]
```

- [ ] **Step 2: Run to verify tests fail**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v -k dead_paper`
Expected: FAIL.

- [ ] **Step 3: Implement the check**

Append to `bullbot/research/health.py`:

```python
import time


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
```

- [ ] **Step 4: Run to verify tests pass**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v`
Expected: all tests PASS (12 total now).

- [ ] **Step 5: Commit**

```bash
git add bullbot/research/health.py tests/unit/test_research_health.py
git commit -m "research/health: add check_dead_paper_trials"
```

---

## Task 6: `check_iteration_failures`

**Files:**
- Modify: `bullbot/research/health.py`
- Modify: `tests/unit/test_research_health.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_research_health.py`:

```python
def _add_iteration_failures_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE iteration_failures (
            id INTEGER PRIMARY KEY,
            ts INTEGER NOT NULL,
            ticker TEXT,
            phase TEXT NOT NULL,
            exc_type TEXT NOT NULL,
            exc_message TEXT NOT NULL,
            traceback TEXT
        )
    """)


def test_check_iteration_failures_passes_when_no_recent_failures():
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    _add_iteration_failures_table(conn)
    # one old failure, outside 24h window
    conn.execute(
        "INSERT INTO iteration_failures (ts, ticker, phase, exc_type, exc_message) "
        "VALUES (?, 'AAPL', 'discovering', 'ValueError', 'old')",
        (now - 2 * 86400,),
    )
    result = H.check_iteration_failures(conn, now=now)
    assert result.passed is True


def test_check_iteration_failures_flags_and_groups():
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    _add_iteration_failures_table(conn)
    # two recent, same ticker + exc type
    for _ in range(2):
        conn.execute(
            "INSERT INTO iteration_failures (ts, ticker, phase, exc_type, exc_message) "
            "VALUES (?, 'AAPL', 'discovering', 'DailyRefreshError', 'bad bar')",
            (now - 3600,),
        )
    # one recent, different ticker
    conn.execute(
        "INSERT INTO iteration_failures (ts, ticker, phase, exc_type, exc_message) "
        "VALUES (?, 'QQQ', 'paper_trial', 'ZeroDivisionError', 'div by zero')",
        (now - 7200,),
    )
    result = H.check_iteration_failures(conn, now=now)
    assert result.passed is False
    assert len(result.findings) == 2
    assert any("AAPL" in f and "DailyRefreshError" in f and "2" in f for f in result.findings)
    assert any("QQQ" in f and "ZeroDivisionError" in f for f in result.findings)
```

- [ ] **Step 2: Run to verify tests fail**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v -k iteration_failures`
Expected: FAIL.

- [ ] **Step 3: Implement the check**

Append to `bullbot/research/health.py`:

```python
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
```

- [ ] **Step 4: Run to verify tests pass**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v`
Expected: all tests PASS (14 total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/research/health.py tests/unit/test_research_health.py
git commit -m "research/health: add check_iteration_failures"
```

---

## Task 7: Header block + `generate_health_brief` orchestrator

**Files:**
- Modify: `bullbot/research/health.py`
- Modify: `tests/unit/test_research_health.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_research_health.py`:

```python
from datetime import date, datetime, time as dtime, timezone


def _today_utc_ts() -> int:
    return int(datetime.combine(date.today(), dtime.min, tzinfo=timezone.utc).timestamp())


def _make_full_conn() -> sqlite3.Connection:
    """Connection with all tables needed by generate_health_brief."""
    c = _make_conn_with_ticker_state()
    _add_iteration_failures_table(c)
    c.execute("""
        CREATE TABLE bars (
            ticker TEXT, timeframe TEXT, ts INTEGER,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE strategies (
            id INTEGER PRIMARY KEY, class_name TEXT, class_version INTEGER,
            params TEXT, params_hash TEXT, parent_id INTEGER, created_at INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY, run_id TEXT, ticker TEXT, strategy_id INTEGER,
            legs TEXT, contracts INTEGER, open_price REAL, close_price REAL,
            mark_to_mkt REAL, opened_at INTEGER, closed_at INTEGER,
            pnl_realized REAL, exit_rules TEXT
        )
    """)
    c.execute("""
        CREATE TABLE cost_ledger (
            id INTEGER PRIMARY KEY, ts INTEGER, category TEXT, ticker TEXT,
            amount_usd REAL, details TEXT
        )
    """)
    return c


def test_generate_health_brief_returns_populated_header(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 10)
    today = _today_utc_ts()
    conn = _make_full_conn()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'discovering', ?)",
        (today,),
    )
    conn.execute(
        "INSERT INTO strategies (class_name, class_version, params, params_hash, created_at) "
        "VALUES ('PutCreditSpread', 1, '{}', 'hash1', ?)", (today,),
    )
    conn.execute(
        "INSERT INTO cost_ledger (ts, category, amount_usd) VALUES (?, 'llm', 0.42)",
        (today + 100,),
    )
    brief = H.generate_health_brief(conn)
    assert isinstance(brief, H.HealthBrief)
    assert "Universe" in brief.header
    assert "1 tickers" in brief.header["Universe"] or "1 ticker" in brief.header["Universe"]
    assert "1 discovering" in brief.header["Universe"]
    assert brief.header["Strategy pool"].startswith("1")
    assert "+1 today" in brief.header["Strategy pool"]
    assert "$0.42" in brief.header["LLM spend today"]


def test_generate_health_brief_runs_all_four_checks(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 10)
    conn = _make_full_conn()
    brief = H.generate_health_brief(conn)
    titles = {r.title for r in brief.results}
    assert {
        "Data shortfalls",
        "pf_oos anomalies",
        "Dead paper trials",
        "Iteration failures (24h)",
    }.issubset(titles)
    assert len(brief.results) == 4
```

- [ ] **Step 2: Run to verify tests fail**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v -k generate_health_brief`
Expected: FAIL (missing `generate_health_brief`).

- [ ] **Step 3: Implement the header builder + orchestrator**

Append to `bullbot/research/health.py`:

```python
from datetime import date, datetime, time as dtime, timezone


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
```

- [ ] **Step 4: Run to verify tests pass**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v`
Expected: all tests PASS (16 total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/research/health.py tests/unit/test_research_health.py
git commit -m "research/health: add header builder + generate_health_brief orchestrator"
```

---

## Task 8: `HealthBrief.to_markdown()`

**Files:**
- Modify: `bullbot/research/health.py`
- Modify: `tests/unit/test_research_health.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_research_health.py`:

```python
def _sample_brief() -> H.HealthBrief:
    return H.HealthBrief(
        generated_at=1_700_000_000,
        header={"Universe": "16 tickers (6 discovering)", "LLM spend today": "$0.38"},
        results=[
            H.CheckResult(
                title="Data shortfalls", passed=False,
                findings=["XLK: 257 bars (need ~504)", "HYG: 257 bars (need ~504)"],
            ),
            H.CheckResult(title="pf_oos anomalies", passed=True, findings=[]),
            H.CheckResult(
                title="Dead paper trials", passed=False,
                findings=["SATS: promoted 2 days ago, dispatch never fired"],
            ),
            H.CheckResult(title="Iteration failures (24h)", passed=True, findings=[]),
        ],
    )


def test_to_markdown_includes_header_and_timestamp():
    md = _sample_brief().to_markdown()
    assert md.startswith("# Research Health")
    assert "2023-11-14" in md  # 1_700_000_000 -> 2023-11-14T22:13:20Z
    assert "**Universe:** 16 tickers (6 discovering)" in md
    assert "**LLM spend today:** $0.38" in md


def test_to_markdown_flag_sections_have_count_and_findings():
    md = _sample_brief().to_markdown()
    assert "## Data shortfalls — FLAG (2)" in md
    assert "- XLK: 257 bars (need ~504)" in md
    assert "- HYG: 257 bars (need ~504)" in md
    assert "## Dead paper trials — FLAG (1)" in md
    assert "- SATS: promoted 2 days ago" in md


def test_to_markdown_ok_sections_are_single_line():
    md = _sample_brief().to_markdown()
    assert "## pf_oos anomalies — OK" in md
    assert "## Iteration failures (24h) — OK" in md
    # No bulleted findings under an OK section
    ok_idx = md.index("## pf_oos anomalies — OK")
    next_section_idx = md.index("## Dead paper trials", ok_idx)
    between = md[ok_idx:next_section_idx]
    assert "- " not in between
```

- [ ] **Step 2: Run to verify tests fail**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v -k to_markdown`
Expected: FAIL (`HealthBrief` has no attribute `to_markdown`).

- [ ] **Step 3: Implement the renderer**

Add `to_markdown` as a method on `HealthBrief` (edit the existing `@dataclass(frozen=True)` block in `bullbot/research/health.py`):

```python
@dataclass(frozen=True)
class HealthBrief:
    generated_at: int
    header: dict[str, str]
    results: list[CheckResult]

    def to_markdown(self) -> str:
        ts = datetime.fromtimestamp(self.generated_at, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%MZ"
        )
        lines = [f"# Research Health — {ts}", ""]
        for label, value in self.header.items():
            lines.append(f"**{label}:** {value}")
        lines.append("")
        for check in self.results:
            if check.passed:
                lines.append(f"## {check.title} — OK")
                lines.append("")
            else:
                lines.append(f"## {check.title} — FLAG ({len(check.findings)})")
                for finding in check.findings:
                    lines.append(f"- {finding}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 4: Run to verify tests pass**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v`
Expected: all tests PASS (19 total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/research/health.py tests/unit/test_research_health.py
git commit -m "research/health: add HealthBrief.to_markdown renderer"
```

---

## Task 9: `HealthBrief.to_html()`

**Files:**
- Modify: `bullbot/research/health.py`
- Modify: `tests/unit/test_research_health.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_research_health.py`:

```python
def test_to_html_has_expected_structure():
    html = _sample_brief().to_html()
    assert '<section class="research-health">' in html
    assert '<h2>Research Health' in html
    assert '<dl class="health-header">' in html
    assert '<dt>Universe</dt>' in html
    assert '<dd>16 tickers (6 discovering)</dd>' in html
    assert '<section class="check check-flag">' in html
    assert '<section class="check check-ok">' in html
    assert '<h3>Data shortfalls — FLAG (2)</h3>' in html
    assert '<h3>pf_oos anomalies — OK</h3>' in html


def test_to_html_escapes_user_content():
    brief = H.HealthBrief(
        generated_at=1_700_000_000,
        header={"Universe": "1 tickers (<script>alert(1)</script>)"},
        results=[
            H.CheckResult(title="X", passed=False, findings=["<script>evil</script>"]),
        ],
    )
    html = brief.to_html()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
```

- [ ] **Step 2: Run to verify tests fail**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v -k to_html`
Expected: FAIL.

- [ ] **Step 3: Implement the renderer**

Add `to_html` method to the same `HealthBrief` dataclass (and import `html` at the top of the file):

```python
# top of file, with other imports:
import html as htmllib

# inside class HealthBrief, after to_markdown:
    def to_html(self) -> str:
        ts = datetime.fromtimestamp(self.generated_at, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%MZ"
        )
        parts = [
            '<section class="research-health">',
            f"<h2>Research Health — {htmllib.escape(ts)}</h2>",
            '<dl class="health-header">',
        ]
        for label, value in self.header.items():
            parts.append(f"<dt>{htmllib.escape(label)}</dt>")
            parts.append(f"<dd>{htmllib.escape(value)}</dd>")
        parts.append("</dl>")
        for check in self.results:
            title_esc = htmllib.escape(check.title)
            if check.passed:
                parts.append('<section class="check check-ok">')
                parts.append(f"<h3>{title_esc} — OK</h3>")
                parts.append("</section>")
            else:
                parts.append('<section class="check check-flag">')
                parts.append(
                    f"<h3>{title_esc} — FLAG ({len(check.findings)})</h3>"
                )
                parts.append("<ul>")
                for finding in check.findings:
                    parts.append(f"<li>{htmllib.escape(finding)}</li>")
                parts.append("</ul>")
                parts.append("</section>")
        parts.append("</section>")
        return "\n".join(parts)
```

- [ ] **Step 4: Run to verify tests pass**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v`
Expected: all tests PASS (21 total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/research/health.py tests/unit/test_research_health.py
git commit -m "research/health: add HealthBrief.to_html renderer with escaping"
```

---

## Task 10: `write_latest_brief`

**Files:**
- Modify: `bullbot/research/health.py`
- Modify: `tests/unit/test_research_health.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_research_health.py`:

```python
def test_write_latest_brief_creates_file_with_expected_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 10)
    conn = _make_full_conn()
    path = H.write_latest_brief(conn, reports_dir=tmp_path)
    assert path.exists()
    assert path.parent == tmp_path
    assert path.name.startswith("research_health_")
    assert path.suffix == ".md"
    content = path.read_text()
    assert content.startswith("# Research Health")
    assert "Universe" in content


def test_write_latest_brief_defaults_to_reports_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 10)
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    conn = _make_full_conn()
    path = H.write_latest_brief(conn)
    assert path.parent == tmp_path
```

- [ ] **Step 2: Run to verify tests fail**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v -k write_latest_brief`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `bullbot/research/health.py`:

```python
from pathlib import Path


def write_latest_brief(
    conn: sqlite3.Connection, reports_dir: Path | None = None
) -> Path:
    """Generate a brief and persist it as a timestamped markdown archive."""
    brief = generate_health_brief(conn)
    target_dir = reports_dir if reports_dir is not None else config.REPORTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"research_health_{brief.generated_at}.md"
    path.write_text(brief.to_markdown(), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run to verify tests pass**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py -v`
Expected: all tests PASS (23 total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/research/health.py tests/unit/test_research_health.py
git commit -m "research/health: add write_latest_brief archival writer"
```

---

## Task 11: Wire into `scheduler.tick()`

**Files:**
- Modify: `bullbot/scheduler.py` (around lines 179-183)
- Modify: `tests/integration/test_regime_scheduler.py` OR a new `tests/integration/test_scheduler_health.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_scheduler_health.py`:

```python
"""Scheduler integration: confirm a research_health_*.md file is produced."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bullbot import config


def _seed_minimal_db(conn: sqlite3.Connection) -> None:
    # Just enough for tick() + health checks to not crash; no LLM calls.
    from bullbot.db import migrations
    migrations.apply_schema(conn)


def test_scheduler_tick_writes_health_brief(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(config, "UNIVERSE", [])  # skip ticker dispatch entirely

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed_minimal_db(conn)

    # Provide minimal fake clients — tick() tolerates them when UNIVERSE is empty
    class _Nop:
        pass

    from bullbot import scheduler
    scheduler.tick(conn=conn, anthropic_client=_Nop(), data_client=_Nop())

    briefs = list(tmp_path.glob("research_health_*.md"))
    assert len(briefs) == 1
    assert briefs[0].read_text().startswith("# Research Health")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_scheduler_health.py -v`
Expected: FAIL (no brief file appears).

- [ ] **Step 3: Wire into scheduler**

In `bullbot/scheduler.py`, find the end of `tick()` (around lines 179-183). Insert the health-brief call *before* the dashboard generator:

```python
    try:
        from bullbot.research import health
        health.write_latest_brief(conn)
    except Exception:
        log.exception("health brief generation failed")
    try:
        from bullbot.dashboard import generator
        generator.generate(conn)
    except Exception:
        log.exception("dashboard generation failed")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_scheduler_health.py -v`
Expected: PASS.

Also run the full unit suite to make sure nothing regressed:
Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/scheduler.py tests/integration/test_scheduler_health.py
git commit -m "scheduler: write research health brief before dashboard generation"
```

---

## Task 12: Dashboard integration — "Health" tab + CSS

**Files:**
- Modify: `bullbot/dashboard/generator.py` (lines 18-41 area)
- Modify: `bullbot/dashboard/templates.py` (add CSS in the existing stylesheet block)
- Modify: `tests/unit/test_dashboard_generator.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dashboard_generator.py`:

```python
def test_generate_includes_health_tab(conn, tmp_path):
    out = tmp_path / "dashboard.html"
    generator.generate(conn, output_path=out)
    html = out.read_text()
    assert "tab-Health" in html
    assert 'class="research-health"' in html
    assert ">Health<" in html  # tab button text
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_generator.py -v -k health_tab`
Expected: FAIL.

- [ ] **Step 3: Add the tab to the generator**

Modify `bullbot/dashboard/generator.py` — add a health section generator and insert "Health" into the tabs dict as the second tab (after Overview):

```python
# Near the top, with existing imports:
from bullbot.research import health as research_health

# Inside generate(), after the existing content assembly (around line 32):
    try:
        health_html = research_health.generate_health_brief(conn).to_html()
    except Exception:
        # Dashboard must render even if health module breaks
        health_html = '<p class="research-health-error">Health brief unavailable this run.</p>'

# Change the tabs dict to:
    tabs = {
        "Overview": overview_html,
        "Health": health_html,
        "Evolver": evolver_html,
        "Positions": positions_html,
        "Transactions": transactions_html,
        "Costs": costs_html,
        "Inventory": inventory_html,
    }
```

- [ ] **Step 4: Add CSS to templates.py**

In `bullbot/dashboard/templates.py`, find the existing `<style>` block inside `page_shell` (grep for `<style>` if needed) and append these rules before `</style>`:

```css
.research-health { padding: 16px; }
.research-health h2 { margin-top: 0; }
.research-health .health-header { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin-bottom: 16px; }
.research-health .health-header dt { font-weight: 600; }
.research-health .check { margin-top: 16px; padding: 12px; border-radius: 6px; border: 1px solid rgba(128,128,128,0.3); }
.research-health .check-ok { border-left: 4px solid #3fb950; }
.research-health .check-flag { border-left: 4px solid #f0883e; }
.research-health .check h3 { margin: 0 0 8px 0; font-size: 1em; }
.research-health .check ul { margin: 0; padding-left: 20px; }
```

- [ ] **Step 5: Run tests to verify passing**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_generator.py -v`
Expected: all PASS.

Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: full suite PASS.

- [ ] **Step 6: Commit**

```bash
git add bullbot/dashboard/generator.py bullbot/dashboard/templates.py tests/unit/test_dashboard_generator.py
git commit -m "dashboard: add Health tab with research-health brief and styling"
```

---

## Task 13: Deploy to pasture

**Files:** None (operational task)

- [ ] **Step 1: Push to origin**

```bash
git push origin main
```
Expected: successful push of Tasks 1-12 commits.

- [ ] **Step 2: Pull on pasture and run full test suite**

```bash
ssh pasture 'cd ~/Projects/bull-bot && git pull origin main && .venv/bin/python -m pytest tests/unit/ tests/integration/ -q 2>&1 | tail -10'
```
Expected: all tests PASS on pasture.

- [ ] **Step 3: Generate a brief manually to verify end-to-end**

```bash
ssh pasture 'cd ~/Projects/bull-bot && .venv/bin/python -c "
import sqlite3
from bullbot import config
from bullbot.research import health
conn = sqlite3.connect(str(config.DB_PATH))
conn.row_factory = sqlite3.Row
path = health.write_latest_brief(conn)
print(\"wrote:\", path)
print(\"---\")
print(path.read_text())
"'
```
Expected:
- A new `reports/research_health_<ts>.md` file is written.
- Its content includes `# Research Health`, the header block (with `Universe: 16 tickers ...`), and all four check sections — at least "Data shortfalls — OK" (since we backfilled yesterday) and "Iteration failures (24h) — OK".

- [ ] **Step 4: Verify the brief matches real DB state**

Eyeball the output. Specifically confirm:
- `pf_oos anomalies` flags the known inf/absurd entries we found earlier (AAPL, TSLA, NVDA, GOOGL, etc.)
- `Dead paper trials` flags SATS (promoted 2026-04-20, still not dispatching)

If something doesn't match expectations, debug before proceeding to Step 5.

- [ ] **Step 5: Wait for tomorrow's 07:30 run; verify dashboard shows the Health tab**

After 07:30 UTC-whatever the next morning:

```bash
ssh pasture 'cd ~/Projects/bull-bot && ls -lt reports/research_health_*.md | head -3'
ssh pasture 'cd ~/Projects/bull-bot && grep -c "tab-Health" reports/dashboard.html'
```
Expected:
- A new `research_health_*.md` file exists with today's timestamp.
- `tab-Health` appears in `dashboard.html` (grep returns ≥ 1).

- [ ] **Step 6: No commit needed — deploy is stateless**

---

## Self-review summary

- **Spec coverage:** all goals (1-4) and non-goals acknowledged. Every check in the spec maps to Tasks 3–6; rendering to Tasks 8–9; integration to Tasks 11–12; deploy to Task 13.
- **Placeholders scanned:** no TBD/TODO/"handle edge cases"/"similar to Task N" language.
- **Type consistency:** `CheckResult(title, passed, findings)`, `HealthBrief(generated_at, header, results)`, and the four check function signatures are identical across all task code blocks.
- **Open follow-ups (not blockers for this plan):**
  - After deploy, the brief may surface issues worth fixing as separate tasks (e.g., investigating why `pf_oos=inf` is emitted — that's the OOS-gate bug noted in the spec, out of scope here).
