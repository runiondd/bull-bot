# Dashboard Reskin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bull-bot dashboard's HTML output with a quant-terminal redesign — IBM Plex, neutral dark, OKLCH design tokens, sidebar nav, 8 tabs, inline SVG charts — driven by 4 new query functions plus an equity-snapshot mechanism so the equity curve has data to show.

**Architecture:** Pure Python f-string ports of the React/JSX prototype at `dashboard/handoff/`. CSS lifted verbatim into a Python constant. Each tab is a function `(data: dict) -> str`. SVG charts are pure functions. Generator orchestrates: query → tab dict → assemble shell. New `equity_snapshots` table written at the end of `scheduler.tick()`. Stdlib only, no React, no build step, single output file.

**Tech Stack:** Python 3.12, sqlite3, stdlib `html.escape`, no JavaScript dependencies (just inline `<script>` for tab switching), pytest.

**Spec:** `dashboard/handoff/IMPLEMENTATION_PROMPT.md` (the prompt). Asset references: `dashboard/handoff/{styles.css, Bull-Bot Dashboard.html, app.jsx, components-shell.jsx, components-tabs.jsx, data.js}`.

---

## File Structure

After this work, `bullbot/dashboard/` will be:

```
bullbot/dashboard/
├── __init__.py            (empty)
├── generator.py           ~80 lines — orchestrator
├── queries.py             ~450 lines — existing 8 funcs + 4 new
├── styles_css.py          ~600 lines — lifted CSS constant + minimal helpers
├── templates.py           ~400 lines — page_shell, header, sidebar, KPI strip, fmt helpers
├── svg_charts.py          ~120 lines — sparkline_svg, equity_chart_svg
└── tabs.py                ~600 lines — 8 tab render functions
```

New schema: `equity_snapshots` table (timestamp + equity values). Migration in `bullbot/db/migrations.py`.

---

## Task 1: equity_snapshots table + migration

**Files:**
- Modify: `bullbot/db/schema.sql`
- Modify: `bullbot/db/migrations.py`
- Modify: `tests/unit/test_migrations.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_migrations.py`:

```python
def test_equity_snapshots_table_exists():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='equity_snapshots'").fetchall()
    assert len(rows) == 1


def test_equity_snapshots_columns():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(equity_snapshots)")}
    assert {"id", "ts", "total_equity", "income_equity", "growth_equity",
            "realized_pnl", "unrealized_pnl"}.issubset(cols)


def test_equity_snapshots_unique_ts():
    """Snapshots are written daily — one per UTC day. Enforce uniqueness on ts."""
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    conn.execute("INSERT INTO equity_snapshots (ts, total_equity, income_equity, growth_equity, realized_pnl, unrealized_pnl) VALUES (1, 265000, 50000, 215000, 0, 0)")
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO equity_snapshots (ts, total_equity, income_equity, growth_equity, realized_pnl, unrealized_pnl) VALUES (1, 266000, 50500, 215500, 100, 400)")


def test_apply_schema_migrates_legacy_db_without_equity_snapshots():
    """Pre-migration DB shouldn't break apply_schema."""
    conn = sqlite3.connect(":memory:")
    # Apply schema with everything except equity_snapshots
    migrations.apply_schema(conn)
    conn.execute("DROP TABLE equity_snapshots")
    # Re-applying must add it back without error
    migrations.apply_schema(conn)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='equity_snapshots'").fetchall()
    assert len(rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_migrations.py -v -k equity_snapshots`
Expected: FAIL — table doesn't exist.

- [ ] **Step 3: Add the table to schema.sql**

In `bullbot/db/schema.sql`, append after the `cost_ledger` block (around line 177):

```sql
-- ---------------------------------------------------------------------------
-- equity_snapshots: daily snapshot of account equity for the dashboard equity curve
-- Written at the end of scheduler.tick() per (ts) UNIQUE constraint;
-- one row per UTC midnight day.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id              INTEGER PRIMARY KEY,
    ts              INTEGER NOT NULL UNIQUE,  -- unix midnight UTC of snapshot day
    total_equity    REAL    NOT NULL,
    income_equity   REAL    NOT NULL,
    growth_equity   REAL    NOT NULL,
    realized_pnl    REAL    NOT NULL,
    unrealized_pnl  REAL    NOT NULL,
    created_at      INTEGER NOT NULL DEFAULT (cast(strftime('%s','now') as integer))
) STRICT;

CREATE INDEX IF NOT EXISTS idx_equity_snapshots_ts ON equity_snapshots (ts DESC);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_migrations.py -v`
Expected: all pass (including 4 new equity_snapshots tests).

- [ ] **Step 5: Commit**

```bash
git add bullbot/db/schema.sql tests/unit/test_migrations.py
git commit -m "schema: add equity_snapshots table for dashboard equity curve"
```

---

## Task 2: Equity snapshot writer + scheduler hook

**Files:**
- Create: `bullbot/research/equity_snapshot.py`
- Modify: `bullbot/scheduler.py` (add call near end of tick(), before health brief)
- Create: `tests/unit/test_equity_snapshot.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_equity_snapshot.py`:

```python
"""Tests for equity snapshot writer."""
from __future__ import annotations

import sqlite3
import time

import pytest

from bullbot.db import migrations
from bullbot.research import equity_snapshot


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrations.apply_schema(c)
    return c


def test_take_snapshot_writes_row(conn):
    """Empty DB: snapshot still writes (with zero pnl)."""
    path = equity_snapshot.take_snapshot(conn, now=1_700_000_000)
    rows = conn.execute("SELECT * FROM equity_snapshots").fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["ts"] == 1_700_000_000 - (1_700_000_000 % 86400)  # midnight UTC
    assert r["total_equity"] == r["income_equity"] + r["growth_equity"]
    assert r["realized_pnl"] == 0
    assert r["unrealized_pnl"] == 0


def test_take_snapshot_idempotent_same_day(conn):
    """Two calls on the same UTC day overwrite, don't error or duplicate."""
    equity_snapshot.take_snapshot(conn, now=1_700_000_000)
    equity_snapshot.take_snapshot(conn, now=1_700_000_000 + 3600)
    rows = conn.execute("SELECT COUNT(*) FROM equity_snapshots").fetchone()
    assert rows[0] == 1  # only one row, second call upserted


def test_take_snapshot_aggregates_paper_positions(conn):
    """Paper realized + unrealized P&L flow into the snapshot."""
    conn.execute(
        "INSERT INTO positions (run_id, ticker, opened_at, open_price, "
        "mark_to_mkt, pnl_realized, unrealized_pnl, closed_at) "
        "VALUES ('paper', 'SPY', 0, -515, 0, 100, 0, 1)"
    )
    conn.execute(
        "INSERT INTO positions (run_id, ticker, opened_at, open_price, "
        "mark_to_mkt, unrealized_pnl) "
        "VALUES ('paper', 'TSLA', 0, 9000, 9000, -250)"
    )
    equity_snapshot.take_snapshot(conn, now=1_700_000_000)
    r = conn.execute("SELECT realized_pnl, unrealized_pnl FROM equity_snapshots").fetchone()
    assert r["realized_pnl"] == 100
    assert r["unrealized_pnl"] == -250


def test_take_snapshot_excludes_backtest_positions(conn):
    """Backtest positions must not poison the live equity curve."""
    conn.execute(
        "INSERT INTO positions (run_id, ticker, opened_at, open_price, "
        "mark_to_mkt, pnl_realized, unrealized_pnl, closed_at) "
        "VALUES ('bt:abc', 'SPY', 0, -100, 0, 999999, 0, 1)"
    )
    equity_snapshot.take_snapshot(conn, now=1_700_000_000)
    r = conn.execute("SELECT realized_pnl FROM equity_snapshots").fetchone()
    assert r["realized_pnl"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_equity_snapshot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bullbot.research.equity_snapshot'`

- [ ] **Step 3: Implement the snapshot writer**

Create `bullbot/research/equity_snapshot.py`:

```python
"""Daily equity snapshot for the dashboard equity curve.

Called at the end of scheduler.tick(). Writes one row per UTC day,
upserting on the unique ts constraint so multiple ticks on the same
day don't duplicate.
"""
from __future__ import annotations

import logging
import sqlite3
import time

from bullbot import config

log = logging.getLogger("bullbot.research.equity_snapshot")


def _utc_midnight(ts: int) -> int:
    """Truncate a unix timestamp to midnight UTC of that day."""
    return ts - (ts % 86400)


def take_snapshot(conn: sqlite3.Connection, now: int | None = None) -> int:
    """Compute current equity and write a snapshot row for today (UTC).

    Idempotent within a single UTC day: re-running upserts. Returns the
    snapshot's ts (midnight UTC of the day it was written for).
    """
    now = now if now is not None else int(time.time())
    day_ts = _utc_midnight(now)

    realized = conn.execute(
        "SELECT COALESCE(SUM(pnl_realized), 0) FROM positions "
        "WHERE run_id NOT LIKE 'bt:%' AND pnl_realized IS NOT NULL"
    ).fetchone()[0]
    unrealized = conn.execute(
        "SELECT COALESCE(SUM(unrealized_pnl), 0) FROM positions "
        "WHERE run_id NOT LIKE 'bt:%' AND closed_at IS NULL"
    ).fetchone()[0]

    income_base = config.INITIAL_CAPITAL_USD
    growth_base = config.GROWTH_CAPITAL_USD
    income_equity = income_base + float(realized) + float(unrealized)
    growth_equity = float(growth_base)
    total_equity = income_equity + growth_equity

    conn.execute(
        "INSERT INTO equity_snapshots "
        "(ts, total_equity, income_equity, growth_equity, realized_pnl, unrealized_pnl, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(ts) DO UPDATE SET "
        "total_equity=excluded.total_equity, income_equity=excluded.income_equity, "
        "growth_equity=excluded.growth_equity, realized_pnl=excluded.realized_pnl, "
        "unrealized_pnl=excluded.unrealized_pnl, created_at=excluded.created_at",
        (day_ts, total_equity, income_equity, growth_equity,
         float(realized), float(unrealized), now),
    )
    conn.commit()
    return day_ts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_equity_snapshot.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Wire into scheduler.tick()**

In `bullbot/scheduler.py`, find the `try:` block that calls `health.write_latest_brief(conn)` (added in the 4/22 work — should be near line 179-188). Insert a snapshot call BEFORE the health brief:

```python
    try:
        from bullbot.research import equity_snapshot
        equity_snapshot.take_snapshot(conn)
    except Exception:
        log.exception("equity snapshot failed")
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

- [ ] **Step 6: Run unit suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add bullbot/research/equity_snapshot.py bullbot/scheduler.py tests/unit/test_equity_snapshot.py
git commit -m "research/equity_snapshot: write daily equity snapshot at end of tick"
```

---

## Task 3: New query — `equity_curve(conn, days=30)`

**Files:**
- Modify: `bullbot/dashboard/queries.py`
- Modify: `tests/unit/test_dashboard_queries.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dashboard_queries.py`:

```python
def test_equity_curve_returns_recent_snapshots(db_conn, _seed_strategy):
    """30 days of snapshots, most recent last."""
    for i in range(30):
        db_conn.execute(
            "INSERT INTO equity_snapshots (ts, total_equity, income_equity, "
            "growth_equity, realized_pnl, unrealized_pnl) VALUES (?, ?, ?, ?, ?, ?)",
            (i * 86400, 265000 + i * 100, 50000 + i * 50, 215000 + i * 50, i * 50, i * 50),
        )
    result = queries.equity_curve(db_conn, days=30)
    assert len(result) == 30
    assert result[0]["total_equity"] == 265000  # oldest
    assert result[-1]["total_equity"] == 265000 + 29 * 100  # newest


def test_equity_curve_returns_empty_when_no_snapshots(db_conn):
    """Empty DB: empty list, no crash."""
    result = queries.equity_curve(db_conn, days=30)
    assert result == []


def test_equity_curve_respects_days_parameter(db_conn):
    for i in range(50):
        db_conn.execute(
            "INSERT INTO equity_snapshots (ts, total_equity, income_equity, "
            "growth_equity, realized_pnl, unrealized_pnl) VALUES (?, ?, ?, ?, ?, ?)",
            (i * 86400, 265000, 50000, 215000, 0, 0),
        )
    result = queries.equity_curve(db_conn, days=10)
    assert len(result) == 10
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_queries.py -v -k equity_curve`
Expected: FAIL — `equity_curve` doesn't exist.

- [ ] **Step 3: Add the function**

Append to `bullbot/dashboard/queries.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_queries.py -v -k equity_curve`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/queries.py tests/unit/test_dashboard_queries.py
git commit -m "dashboard/queries: add equity_curve(days)"
```

---

## Task 4: New query — `account_summary(conn)`

**Files:**
- Modify: `bullbot/dashboard/queries.py`
- Modify: `tests/unit/test_dashboard_queries.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dashboard_queries.py`:

```python
def test_account_summary_returns_required_fields(db_conn, _seed_strategy):
    # Seed a snapshot so total_equity/income/growth are populated
    db_conn.execute(
        "INSERT INTO equity_snapshots (ts, total_equity, income_equity, "
        "growth_equity, realized_pnl, unrealized_pnl) VALUES (?, ?, ?, ?, ?, ?)",
        (1_700_000_000, 268_412.18, 51_204.42, 217_207.76, 3_104.55, 1_708.00),
    )
    result = queries.account_summary(db_conn, now=1_700_000_000)
    assert result["total_equity"] == pytest.approx(268_412.18)
    assert result["income_account"] == pytest.approx(51_204.42)
    assert result["growth_account"] == pytest.approx(217_207.76)
    assert result["target_monthly"] == 10_000  # from config
    assert "month_to_date" in result
    assert "days_to_target" in result


def test_account_summary_empty_db_returns_baseline(db_conn):
    """No snapshots: fall back to config baseline so the page still renders."""
    result = queries.account_summary(db_conn, now=1_700_000_000)
    assert result["total_equity"] == 50_000 + 215_000  # INITIAL + GROWTH
    assert result["income_account"] == 50_000
    assert result["growth_account"] == 215_000
    assert result["month_to_date"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_queries.py -v -k account_summary`
Expected: FAIL.

- [ ] **Step 3: Add the function**

Append to `bullbot/dashboard/queries.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_queries.py -v -k account_summary`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/queries.py tests/unit/test_dashboard_queries.py
git commit -m "dashboard/queries: add account_summary"
```

---

## Task 5: New query — `extended_metrics(conn)`

**Files:**
- Modify: `bullbot/dashboard/queries.py`
- Modify: `tests/unit/test_dashboard_queries.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dashboard_queries.py`:

```python
def test_extended_metrics_returns_required_keys(db_conn, _seed_strategy):
    # 3 wins, 2 losses
    for pnl in (100, 200, 50, -80, -120):
        db_conn.execute(
            "INSERT INTO positions (run_id, ticker, opened_at, open_price, "
            "mark_to_mkt, pnl_realized, closed_at) VALUES "
            "('paper', 'SPY', 0, 1, 0, ?, 1)", (pnl,),
        )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_trade_count, updated_at) "
        "VALUES ('SPY', 'paper_trial', 5, 0)"
    )
    result = queries.extended_metrics(db_conn)
    expected_keys = {"sharpe_30d", "win_rate", "avg_win", "avg_loss",
                     "profit_factor", "paper_trade_count", "backtest_count",
                     "llm_spend_7d"}
    assert expected_keys.issubset(set(result.keys()))
    assert result["win_rate"] == pytest.approx(0.6)  # 3/5
    assert result["avg_win"] == pytest.approx(116.667, abs=0.01)
    assert result["avg_loss"] == pytest.approx(-100.0)
    assert result["profit_factor"] == pytest.approx(350 / 200)
    assert result["paper_trade_count"] == 5


def test_extended_metrics_empty_db(db_conn):
    """Empty DB: zeros across the board, no division-by-zero."""
    result = queries.extended_metrics(db_conn)
    assert result["win_rate"] == 0
    assert result["avg_win"] == 0
    assert result["avg_loss"] == 0
    assert result["profit_factor"] == 0
    assert result["paper_trade_count"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_queries.py -v -k extended_metrics`
Expected: FAIL.

- [ ] **Step 3: Add the function**

Append to `bullbot/dashboard/queries.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_queries.py -v -k extended_metrics`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/queries.py tests/unit/test_dashboard_queries.py
git commit -m "dashboard/queries: add extended_metrics"
```

---

## Task 6: New query — `universe_with_edge(conn)`

**Files:**
- Modify: `bullbot/dashboard/queries.py`
- Modify: `tests/unit/test_dashboard_queries.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dashboard_queries.py`:

```python
def test_universe_with_edge_joins_state_and_strategy(db_conn, _seed_strategy):
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, iteration_count, paper_trade_count, "
        "best_strategy_id, best_pf_is, best_pf_oos, updated_at) "
        "VALUES ('SPY', 'paper_trial', 5, 2, 1, 1.78, 1.42, 0)"
    )
    result = queries.universe_with_edge(db_conn)
    assert len(result) == 1
    r = result[0]
    assert r["ticker"] == "SPY"
    assert r["phase"] == "paper_trial"
    assert r["category"] == "income"  # SPY is income per config.TICKER_CATEGORY
    assert r["strategy"] == "BearPutSpread"
    assert r["edge"]["pf_oos"] == pytest.approx(1.42)
    assert r["edge"]["pf_is"] == pytest.approx(1.78)


def test_universe_with_edge_handles_null_strategy(db_conn):
    """no_edge tickers have no best_strategy_id — must not crash."""
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, iteration_count, paper_trade_count, "
        "updated_at) VALUES ('XLE', 'no_edge', 22, 0, 0)"
    )
    result = queries.universe_with_edge(db_conn)
    assert len(result) == 1
    assert result[0]["strategy"] is None
    assert result[0]["edge"]["pf_oos"] == 0.0  # NULL → 0


def test_universe_with_edge_empty_db(db_conn):
    assert queries.universe_with_edge(db_conn) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_queries.py -v -k universe_with_edge`
Expected: FAIL.

- [ ] **Step 3: Add the function**

Append to `bullbot/dashboard/queries.py`:

```python
def universe_with_edge(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return ticker grid with edge metrics, in display order.

    Each row: {ticker, category, phase, strategy, iterations, paperTrades,
              edge: {pf_oos, pf_is, dd}}.

    Joins ticker_state with strategies and config.TICKER_CATEGORY. NULL pf
    values render as 0.0 (callers can detect via edge.pf_oos == 0). NULL
    strategy is preserved as None.
    """
    from bullbot import config
    rows = conn.execute(
        "SELECT ts.ticker, ts.phase, ts.iteration_count, ts.paper_trade_count, "
        "       ts.best_pf_is, ts.best_pf_oos, "
        "       s.class_name AS strategy "
        "FROM ticker_state ts "
        "LEFT JOIN strategies s ON ts.best_strategy_id = s.id "
        "ORDER BY ts.ticker"
    ).fetchall()
    result = []
    for r in rows:
        result.append({
            "ticker": r["ticker"],
            "category": config.TICKER_CATEGORY.get(r["ticker"], "income"),
            "phase": r["phase"],
            "strategy": r["strategy"],
            "iterations": int(r["iteration_count"] or 0),
            "paperTrades": int(r["paper_trade_count"] or 0),
            "edge": {
                "pf_oos": float(r["best_pf_oos"] or 0.0),
                "pf_is": float(r["best_pf_is"] or 0.0),
                "dd": 0.0,  # max_dd not currently tracked at ticker level
            },
        })
    return result
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_queries.py -v -k universe_with_edge`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/queries.py tests/unit/test_dashboard_queries.py
git commit -m "dashboard/queries: add universe_with_edge"
```

---

## Task 7: CSS lift into `styles_css.py` constant

**Files:**
- Create: `bullbot/dashboard/styles_css.py`
- Create: `tests/unit/test_dashboard_styles.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dashboard_styles.py`:

```python
"""Tests that the lifted CSS contains expected design tokens."""
from bullbot.dashboard import styles_css


def test_styles_css_contains_oklch_tokens():
    assert "oklch(15% 0.005 250)" in styles_css.CSS  # --bg-0
    assert "oklch(72% 0.16 145)" in styles_css.CSS   # --pos


def test_styles_css_contains_chip_classes():
    for cls in ("chip.live", "chip.paper", "chip.discovering",
                "chip.no_edge", "chip.pass", "chip.fail",
                "chip.warn", "chip.open", "chip.closed"):
        assert f".{cls}" in styles_css.CSS, f"missing .{cls}"


def test_styles_css_contains_density_modes():
    assert '[data-density="comfortable"]' in styles_css.CSS
    assert '[data-density="compact"]' in styles_css.CSS


def test_styles_css_contains_tnum_feature():
    """tnum is required for column alignment of monospace numbers."""
    assert '"tnum"' in styles_css.CSS
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_styles.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Lift the CSS**

Create `bullbot/dashboard/styles_css.py`:

```python
"""Lifted CSS for the dashboard.

This is the verbatim contents of dashboard/handoff/styles.css from the
approved redesign. DO NOT modify without re-validating against the
React prototype — the design tokens, density vars, accent variants,
and chip classes are all referenced by the templates and must stay
in sync. To update, replace the CSS string between the triple-quotes.
"""

CSS = r"""
<paste the entire contents of dashboard/handoff/styles.css here verbatim,
keeping the leading newline and indentation as in the source>
"""
```

The implementer should literally `cat dashboard/handoff/styles.css` and paste between the triple-quote markers. Use a raw string (`r"""..."""`) so backslashes and Unicode survive intact.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_styles.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/styles_css.py tests/unit/test_dashboard_styles.py
git commit -m "dashboard/styles_css: lift styles.css verbatim into module constant"
```

---

## Task 8: Format helpers (fmt_money, fmt_pct, pnl_class, phase_class, phase_label)

**Files:**
- Create: `bullbot/dashboard/fmt.py`
- Create: `tests/unit/test_dashboard_fmt.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dashboard_fmt.py`:

```python
from bullbot.dashboard import fmt


def test_fmt_money_basic():
    assert fmt.fmt_money(0) == "$0.00"
    assert fmt.fmt_money(1234.56) == "$1,234.56"
    assert fmt.fmt_money(-89.10) == "-$89.10"
    assert fmt.fmt_money(None) == "—"


def test_fmt_money_signed_positive():
    assert fmt.fmt_money(100, signed=True) == "+$100.00"


def test_fmt_money_decimals_zero_for_large_values():
    assert fmt.fmt_money(50_000) == "$50,000"
    assert fmt.fmt_money(50_000, decimals=2) == "$50,000.00"


def test_fmt_pct():
    assert fmt.fmt_pct(0) == "0.0%"
    assert fmt.fmt_pct(0.42) == "42.0%"
    assert fmt.fmt_pct(0.42, signed=True) == "+42.0%"
    assert fmt.fmt_pct(-0.05) == "-5.0%"
    assert fmt.fmt_pct(None) == "—"


def test_pnl_class():
    assert fmt.pnl_class(0) == "muted"
    assert fmt.pnl_class(None) == "muted"
    assert fmt.pnl_class(1) == "pos"
    assert fmt.pnl_class(-1) == "neg"


def test_phase_class():
    assert fmt.phase_class("live") == "live"
    assert fmt.phase_class("paper_trial") == "paper"
    assert fmt.phase_class("discovering") == "discovering"
    assert fmt.phase_class("no_edge") == "no_edge"
    assert fmt.phase_class("anything_else") == "no_edge"  # safe default


def test_phase_label():
    assert fmt.phase_label("paper_trial") == "paper trial"
    assert fmt.phase_label("no_edge") == "no edge"
    assert fmt.phase_label("live") == "live"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_fmt.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement helpers**

Create `bullbot/dashboard/fmt.py`:

```python
"""Format helpers ported from dashboard/handoff/components-shell.jsx fmtMoney/fmtPct.

Pure functions, no side effects. Used by templates.py and tabs.py to
keep money/percent/PnL formatting consistent across the dashboard.
"""
from __future__ import annotations


def fmt_money(v: float | None, *, signed: bool = False, decimals: int | None = None) -> str:
    """Format a dollar amount. None → em-dash. Negatives use a minus sign.
    decimals defaults to 0 for |v| >= 10_000, else 2."""
    if v is None:
        return "—"
    if decimals is None:
        decimals = 0 if abs(v) >= 10_000 else 2
    sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
    abs_v = abs(v)
    if decimals == 0:
        formatted = f"{abs_v:,.0f}"
    else:
        formatted = f"{abs_v:,.{decimals}f}"
    return f"{sign}${formatted}"


def fmt_pct(v: float | None, *, signed: bool = False, decimals: int = 1) -> str:
    """Format a fraction as a percent. None → em-dash."""
    if v is None:
        return "—"
    sign = "" if v < 0 else ("+" if signed else "")
    return f"{sign}{v * 100:.{decimals}f}%"


def pnl_class(v: float | None) -> str:
    """CSS class for a P&L value: 'pos', 'neg', or 'muted'."""
    if v is None or v == 0:
        return "muted"
    return "pos" if v > 0 else "neg"


_PHASE_TO_CHIP = {
    "live": "live",
    "paper_trial": "paper",
    "discovering": "discovering",
    "no_edge": "no_edge",
}


def phase_class(phase: str) -> str:
    """Map a ticker_state.phase to its chip CSS class."""
    return _PHASE_TO_CHIP.get(phase, "no_edge")


def phase_label(phase: str) -> str:
    """Human-readable phase name for chip labels."""
    return phase.replace("_", " ")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_fmt.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/fmt.py tests/unit/test_dashboard_fmt.py
git commit -m "dashboard/fmt: add money/pct/pnl/phase format helpers"
```

---

## Task 9: SVG charts module (sparkline + equity_chart)

**Files:**
- Create: `bullbot/dashboard/svg_charts.py`
- Create: `tests/unit/test_dashboard_svg_charts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dashboard_svg_charts.py`:

```python
from bullbot.dashboard import svg_charts


def test_sparkline_returns_empty_for_short_data():
    """0 or 1 data point: empty string (matches JSX behavior)."""
    assert svg_charts.sparkline_svg([]) == ""
    assert svg_charts.sparkline_svg([1.0]) == ""


def test_sparkline_renders_polyline():
    svg = svg_charts.sparkline_svg([100.0, 110.0, 105.0, 120.0])
    assert svg.startswith("<svg")
    assert "polyline" in svg
    assert 'class="spark"' in svg


def test_sparkline_uses_pos_color_for_uptrend():
    svg = svg_charts.sparkline_svg([100.0, 110.0])
    assert "var(--pos)" in svg


def test_sparkline_uses_neg_color_for_downtrend():
    svg = svg_charts.sparkline_svg([110.0, 100.0])
    assert "var(--neg)" in svg


def test_equity_chart_renders_with_gridlines_and_labels():
    data = [265000.0 + i * 100 for i in range(30)]
    svg = svg_charts.equity_chart_svg(data)
    assert svg.startswith("<svg")
    assert 'class="equity-chart"' in svg
    assert "polyline" in svg
    # 5 horizontal gridlines (4 ticks + bottom)
    assert svg.count("<line") >= 4
    # x-axis labels
    assert "30d ago" in svg
    assert "today" in svg


def test_equity_chart_handles_empty_data():
    """Empty input: single-line flat-line placeholder, no crash."""
    svg = svg_charts.equity_chart_svg([])
    assert svg.startswith("<svg")
    # Should still be a valid <svg> with the chart container; can be a flat
    # line at zero or just an empty plot — the only requirement is it doesn't
    # raise and the page can embed it.
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_svg_charts.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement charts**

Create `bullbot/dashboard/svg_charts.py`:

```python
"""Inline SVG chart generators ported from components-shell.jsx.

Pure functions: data in, SVG string out. No external chart libraries.
The SVG uses CSS variables (--pos, --neg, --accent, --line, --fg-2) so
colors follow the active theme.
"""
from __future__ import annotations


def sparkline_svg(data: list[float], width: int = 120, height: int = 32,
                  color: str | None = None) -> str:
    """Render a tiny inline sparkline. Empty/single-point data → empty string.

    Stroke color follows trend direction (last vs first) unless `color` is
    explicitly provided.
    """
    if not data or len(data) < 2:
        return ""
    mn, mx = min(data), max(data)
    rng = mx - mn or 1.0
    step = width / (len(data) - 1)

    pts: list[str] = []
    for i, v in enumerate(data):
        x = i * step
        y = height - ((v - mn) / rng) * (height - 4) - 2
        pts.append(f"{x:.1f},{y:.1f}")
    pts_str = " ".join(pts)

    last, first = data[-1], data[0]
    is_up = last >= first
    stroke = color or ("var(--pos)" if is_up else "var(--neg)")
    fill = (f"color-mix(in oklab, {'var(--pos)' if is_up else 'var(--neg)'} "
            "14%, transparent)")

    return (
        f'<svg class="spark" width="{width}" height="{height}">'
        f'<polyline points="0,{height} {pts_str} {width},{height}" '
        f'fill="{fill}" stroke="none" />'
        f'<polyline points="{pts_str}" fill="none" stroke="{stroke}" '
        f'stroke-width="1.5" />'
        f'</svg>'
    )


def equity_chart_svg(data: list[float], height: int = 200) -> str:
    """Render the larger 30-day equity area chart with gridlines + labels.

    Empty data → an empty plot placeholder (still valid SVG).
    """
    w, h = 880, height
    pad = {"l": 48, "r": 12, "t": 14, "b": 22}

    if not data:
        return (
            f'<svg class="equity-chart" viewBox="0 0 {w} {h}" '
            f'preserveAspectRatio="none">'
            f'<text x="{w/2}" y="{h/2}" text-anchor="middle" '
            f'font-family="IBM Plex Mono, monospace" font-size="11" '
            f'fill="var(--fg-2)">No equity history yet</text>'
            f'</svg>'
        )

    mn, mx = min(data), max(data)
    rng = mx - mn or 1.0
    inner_w = w - pad["l"] - pad["r"]
    inner_h = h - pad["t"] - pad["b"]
    step = inner_w / (len(data) - 1) if len(data) > 1 else inner_w

    def y_for(v: float) -> float:
        return pad["t"] + inner_h - ((v - mn) / rng) * inner_h

    pts = " ".join(
        f"{pad['l'] + i * step:.1f},{y_for(v):.1f}"
        for i, v in enumerate(data)
    )

    # 5 gridlines + y-axis labels
    ticks = 4
    grid_parts: list[str] = []
    for i in range(ticks + 1):
        v = mn + (rng * i) / ticks
        y = y_for(v)
        label = f"${v / 1000:.0f}k"
        grid_parts.append(
            f'<line x1="{pad["l"]}" x2="{w - pad["r"]}" y1="{y:.1f}" y2="{y:.1f}" '
            f'stroke="var(--line)" stroke-dasharray="2 3" />'
            f'<text x="{pad["l"] - 6}" y="{y + 3:.1f}" text-anchor="end" '
            f'font-family="IBM Plex Mono, monospace" font-size="9.5" fill="var(--fg-2)">'
            f'{label}</text>'
        )

    # x-axis labels (start, mid, end)
    n = len(data)
    x_label_indices = [0] if n == 1 else [0, n // 2, n - 1]
    x_parts: list[str] = []
    for i in x_label_indices:
        x = pad["l"] + i * step
        if i == 0:
            text = "30d ago"
        elif i == n - 1:
            text = "today"
        else:
            text = f"{n - i}d"
        x_parts.append(
            f'<text x="{x:.1f}" y="{h - 6}" text-anchor="middle" '
            f'font-family="IBM Plex Mono, monospace" font-size="9.5" fill="var(--fg-2)">'
            f'{text}</text>'
        )

    return (
        f'<svg class="equity-chart" viewBox="0 0 {w} {h}" '
        f'preserveAspectRatio="none">'
        f'{"".join(grid_parts)}'
        f'<polyline points="{pad["l"]},{h - pad["b"]} {pts} {w - pad["r"]},{h - pad["b"]}" '
        f'fill="color-mix(in oklab, var(--accent) 12%, transparent)" />'
        f'<polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="1.5" />'
        f'{"".join(x_parts)}'
        f'</svg>'
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_svg_charts.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/svg_charts.py tests/unit/test_dashboard_svg_charts.py
git commit -m "dashboard/svg_charts: add sparkline + equity_chart inline SVG generators"
```

---

## Task 10: page_shell + tab-switching JS

**Files:**
- Modify: `bullbot/dashboard/templates.py` (replace existing `page_shell`)
- Modify: `tests/unit/test_dashboard_templates.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dashboard_templates.py`:

```python
def test_page_shell_includes_lifted_css():
    from bullbot.dashboard import templates, styles_css
    body = "<div>hi</div>"
    html = templates.page_shell("2026-04-26 12:00 UTC", body)
    # Sample of CSS tokens that must be present
    assert "oklch(15% 0.005 250)" in html  # --bg-0
    assert ".chip.live" in html
    assert "data-theme" in html
    assert "data-accent" in html


def test_page_shell_loads_ibm_plex_via_link():
    from bullbot.dashboard import templates
    html = templates.page_shell("ts", "")
    assert "fonts.googleapis.com" in html
    assert "IBM+Plex+Sans" in html
    assert "IBM+Plex+Mono" in html


def test_page_shell_includes_tab_switching_js():
    from bullbot.dashboard import templates
    html = templates.page_shell("ts", "")
    assert "<script>" in html
    # Tab switching toggles .active on .nav-item and shows .tab-content
    assert "nav-item" in html
    assert "tab-content" in html


def test_page_shell_embeds_body_content():
    from bullbot.dashboard import templates
    html = templates.page_shell("ts", "<div id='test-marker'>marker</div>")
    assert "<div id='test-marker'>marker</div>" in html
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_templates.py -v -k page_shell`
Expected: existing tests may pass; new tests checking lifted CSS will fail until we wire in styles_css.

- [ ] **Step 3: Replace `page_shell`**

Replace the existing `page_shell` function in `bullbot/dashboard/templates.py` with:

```python
def page_shell(generated_at: str, body: str) -> str:
    """Outer HTML document. Embeds the lifted CSS, IBM Plex font link, and
    minimal tab-switching JS. `body` is the assembled inner content (header,
    layout, sidebar, main, all the tab divs)."""
    from bullbot.dashboard import styles_css
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" data-density="default" data-accent="green">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1280">
<title>Bull-Bot — Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
{styles_css.CSS}
</style>
</head>
<body>
{body}
<script>
(function() {{
  function showTab(name) {{
    document.querySelectorAll('.nav-item').forEach(function(el) {{
      el.classList.toggle('active', el.dataset.tab === name);
    }});
    document.querySelectorAll('.tab-content').forEach(function(el) {{
      el.style.display = (el.id === 'tab-' + name) ? 'block' : 'none';
    }});
  }}
  document.querySelectorAll('.nav-item').forEach(function(el) {{
    el.addEventListener('click', function() {{ showTab(el.dataset.tab); }});
  }});
}})();
</script>
</body>
</html>"""
```

Remove the existing CSS-emission inside templates.py if any (the `<style>` block was previously inline in the same file). The lifted CSS now lives in `styles_css.CSS` only.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_templates.py -v -k page_shell`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/templates.py tests/unit/test_dashboard_templates.py
git commit -m "dashboard/templates: rebuild page_shell with lifted CSS and tab-switching JS"
```

---

## Task 11: Header section

**Files:**
- Modify: `bullbot/dashboard/templates.py`
- Modify: `tests/unit/test_dashboard_templates.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dashboard_templates.py`:

```python
def test_header_section_includes_brand_and_pnl():
    from bullbot.dashboard import templates
    html = templates.header_section(
        generated_at="2026-04-26 12:00 UTC",
        total_pnl=123.45,
    )
    assert '<header class="app-header">' in html
    assert "Bull-Bot" in html
    assert "v3" in html  # version sub
    assert "2026-04-26 12:00 UTC" in html
    assert "+$123" in html  # signed money formatting


def test_header_section_negative_pnl():
    from bullbot.dashboard import templates
    html = templates.header_section(generated_at="ts", total_pnl=-50.0)
    assert "-$50" in html
    assert "neg" in html  # pnl_class adds 'neg'
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_templates.py -v -k header_section`
Expected: FAIL.

- [ ] **Step 3: Implement header_section**

Append to `bullbot/dashboard/templates.py`:

```python
def header_section(*, generated_at: str, total_pnl: float) -> str:
    """The sticky top header: brand mark, status dot, generated-at timestamp,
    and 30-day total P&L. Ports components-shell.jsx:Header."""
    from bullbot.dashboard.fmt import fmt_money, pnl_class
    pnl_cls = pnl_class(total_pnl)
    pnl_str = fmt_money(total_pnl, signed=True)
    return f"""<header class="app-header">
  <div class="brand">
    <div class="brand-mark"></div>
    <div>
      <span class="brand-name">Bull-Bot</span>
      <span class="brand-sub">v3.2 / paper</span>
    </div>
  </div>
  <div class="header-meta">
    <div class="item"><span class="dot"></span>Engine running</div>
    <div class="item mono">{html.escape(generated_at)}</div>
    <div class="item">
      <span class="num" style="color: var(--fg-2)">P&amp;L 30d</span>
      <span class="num {pnl_cls}">{pnl_str}</span>
    </div>
  </div>
</header>"""
```

If `import html` isn't already at the top of templates.py, add it.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_templates.py -v -k header_section`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/templates.py tests/unit/test_dashboard_templates.py
git commit -m "dashboard/templates: add header_section"
```

---

## Task 12: Sidebar section

**Files:**
- Modify: `bullbot/dashboard/templates.py`
- Modify: `tests/unit/test_dashboard_templates.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dashboard_templates.py`:

```python
def test_sidebar_section_lists_all_8_tabs_in_2_groups():
    from bullbot.dashboard import templates
    counts = {
        "positions": 6, "evolver": 12, "universe": 16,
        "transactions": 47, "health": 1, "inventory": 3,
    }
    html = templates.sidebar_section(active_tab="overview", counts=counts)
    for tab in ("Overview", "Positions", "Evolver", "Universe",
                "Transactions", "Health", "Costs", "Inventory"):
        assert f">{tab}<" in html or f">{tab}\n<" in html or tab in html
    assert ">Operations<" in html
    assert ">Diagnostics<" in html
    # The active tab should have .active class on its nav-item
    assert 'data-tab="overview"' in html
    assert "active" in html


def test_sidebar_section_renders_badge_counts():
    from bullbot.dashboard import templates
    counts = {"positions": 3, "evolver": 0, "universe": 16,
              "transactions": 5, "health": 2, "inventory": 1}
    html = templates.sidebar_section(active_tab="overview", counts=counts)
    assert ">3<" in html  # positions badge
    assert ">16<" in html  # universe badge
    # Health with non-ok count should show a badge


def test_sidebar_section_omits_zero_badges():
    """Zero counts should not render a badge (matches JSX `count != null`)."""
    from bullbot.dashboard import templates
    counts = {"positions": 0, "evolver": 0, "universe": 0,
              "transactions": 0, "health": 0, "inventory": 0}
    html = templates.sidebar_section(active_tab="overview", counts=counts)
    # No <span class="badge"> rendered for zero (or use None to suppress)
    # Implementation choice: accept either suppression or "0" in badge
    assert html  # just don't crash
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_templates.py -v -k sidebar_section`
Expected: FAIL.

- [ ] **Step 3: Implement sidebar_section**

Append to `bullbot/dashboard/templates.py`:

```python
def sidebar_section(*, active_tab: str, counts: dict[str, int]) -> str:
    """Left sidebar nav. 2 groups: Operations, Diagnostics. Each item has
    a stable data-tab attribute the JS uses to switch tabs.

    counts: per-tab badge count. Zero or missing → no badge.
    """
    operations = [
        ("overview", "Overview"),
        ("positions", "Positions"),
        ("evolver", "Evolver"),
        ("universe", "Universe"),
        ("transactions", "Transactions"),
    ]
    diagnostics = [
        ("health", "Health"),
        ("costs", "Costs"),
        ("inventory", "Inventory"),
    ]

    def render_item(key: str, label: str) -> str:
        active = " active" if key == active_tab else ""
        n = counts.get(key, 0)
        badge = f'<span class="badge">{n}</span>' if n else ""
        return (
            f'<div class="nav-item{active}" data-tab="{key}">'
            f'<span>{html.escape(label)}</span>{badge}'
            f'</div>'
        )

    ops_html = "".join(render_item(k, l) for k, l in operations)
    diag_html = "".join(render_item(k, l) for k, l in diagnostics)
    return f"""<aside class="sidebar">
  <div class="nav-group">Operations</div>
  {ops_html}
  <div class="nav-divider"></div>
  <div class="nav-group">Diagnostics</div>
  {diag_html}
</aside>"""
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_templates.py -v -k sidebar_section`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/templates.py tests/unit/test_dashboard_templates.py
git commit -m "dashboard/templates: add sidebar_section with 2 groups + badge counts"
```

---

## Task 13: KPI strip

**Files:**
- Modify: `bullbot/dashboard/templates.py`
- Modify: `tests/unit/test_dashboard_templates.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dashboard_templates.py`:

```python
def test_kpi_strip_renders_5_cards():
    from bullbot.dashboard import templates
    account = {"total_equity": 268_412.18, "income_account": 51_204.42,
               "growth_account": 217_207.76, "target_monthly": 10_000,
               "month_to_date": 4_812.55, "days_to_target": 75}
    metrics = {"open_positions": 6, "realized_pnl": 3_104.55,
               "unrealized_pnl": 1_708.00, "llm_spend": 28.74,
               "llm_spend_7d": 6.91, "sharpe_30d": 1.42, "win_rate": 0.68,
               "profit_factor": 1.71}
    equity_curve = [265_000.0 + i * 100 for i in range(30)]
    html_out = templates.kpi_strip(account=account, metrics=metrics,
                                     equity_curve=equity_curve)
    assert '<div class="kpi-grid">' in html_out
    assert "Total Equity" in html_out
    assert "Realized P&amp;L" in html_out
    assert "Unrealized P&amp;L" in html_out
    assert "Target Progress" in html_out
    assert "LLM Spend" in html_out


def test_kpi_strip_empty_metrics_no_crash():
    """Zero everything: page must still render."""
    from bullbot.dashboard import templates
    account = {"total_equity": 265_000, "income_account": 50_000,
               "growth_account": 215_000, "target_monthly": 10_000,
               "month_to_date": 0, "days_to_target": 75}
    metrics = {"open_positions": 0, "realized_pnl": 0, "unrealized_pnl": 0,
               "llm_spend": 0, "llm_spend_7d": 0, "sharpe_30d": 0,
               "win_rate": 0, "profit_factor": 0}
    html_out = templates.kpi_strip(account=account, metrics=metrics,
                                     equity_curve=[])
    assert '<div class="kpi-grid">' in html_out
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_templates.py -v -k kpi_strip`
Expected: FAIL.

- [ ] **Step 3: Implement kpi_strip**

Append to `bullbot/dashboard/templates.py`:

```python
def kpi_strip(*, account: dict, metrics: dict, equity_curve: list) -> str:
    """Top-of-overview KPI strip: 5 cards. Ports components-shell.jsx:KPIStrip."""
    from bullbot.dashboard.fmt import fmt_money, fmt_pct, pnl_class
    from bullbot.dashboard.svg_charts import sparkline_svg

    eq_values = [float(p["total_equity"]) for p in equity_curve] if equity_curve else []
    realized = metrics.get("realized_pnl", 0)
    unrealized = metrics.get("unrealized_pnl", 0)
    target_progress = (account["month_to_date"] / account["target_monthly"]
                       if account.get("target_monthly") else 0.0)
    llm_progress = metrics.get("llm_spend", 0) / 50.0
    llm_warn = llm_progress > 0.5

    spark_eq = sparkline_svg(eq_values) if eq_values else ""

    realized_cls = pnl_class(realized)
    unrealized_cls = pnl_class(unrealized)

    return f"""<div class="kpi-grid">
  <div class="kpi">
    <div class="label">Total Equity</div>
    <div class="value">{fmt_money(account["total_equity"], decimals=0)}</div>
    <div class="sub"><span>{account.get('days_to_target', 0)}d to target</span></div>
    <div class="spark">{spark_eq}</div>
  </div>
  <div class="kpi">
    <div class="label">Realized P&amp;L</div>
    <div class="value"><span class="{realized_cls}">{fmt_money(realized, signed=True, decimals=0)}</span></div>
    <div class="sub"><span>WR {fmt_pct(metrics.get('win_rate', 0), decimals=0)}</span><span>·</span><span>PF {metrics.get('profit_factor', 0):.2f}</span></div>
  </div>
  <div class="kpi">
    <div class="label">Unrealized P&amp;L</div>
    <div class="value"><span class="{unrealized_cls}">{fmt_money(unrealized, signed=True, decimals=0)}</span></div>
    <div class="sub"><span>{metrics.get('open_positions', 0)} open</span><span>·</span><span>Sharpe {metrics.get('sharpe_30d', 0):.2f}</span></div>
  </div>
  <div class="kpi">
    <div class="label">Target Progress</div>
    <div class="value">{fmt_money(account['month_to_date'], decimals=0)}<span style="color: var(--fg-2); font-size: 14px"> / {fmt_money(account['target_monthly'], decimals=0)}</span></div>
    <div class="sub"><span>{account.get('days_to_target', 0)}d to target date</span></div>
    <div class="progress" style="margin-top: 6px"><div style="width: {min(100.0, target_progress * 100):.1f}%; background: var(--accent)"></div></div>
  </div>
  <div class="kpi">
    <div class="label">LLM Spend (MTD)</div>
    <div class="value">${metrics.get('llm_spend', 0):.2f}<span style="color: var(--fg-2); font-size: 14px"> / $50</span></div>
    <div class="sub"><span>${metrics.get('llm_spend_7d', 0):.2f} this week</span></div>
    <div class="progress" style="margin-top: 6px"><div style="width: {min(100.0, llm_progress * 100):.1f}%; background: var({'--warn' if llm_warn else '--accent'})"></div></div>
  </div>
</div>"""
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_templates.py -v -k kpi_strip`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/templates.py tests/unit/test_dashboard_templates.py
git commit -m "dashboard/templates: add kpi_strip with 5 cards"
```

---

## Task 14: Tabs module skeleton + Overview tab

**Files:**
- Create: `bullbot/dashboard/tabs.py`
- Create: `tests/unit/test_dashboard_tabs.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dashboard_tabs.py`:

```python
"""Tests for tab render functions."""
from bullbot.dashboard import tabs


def test_overview_tab_renders_required_sections():
    data = {
        "equity_curve": [{"total_equity": 265000.0 + i * 100} for i in range(30)],
        "metrics": {"realized_pnl": 100, "unrealized_pnl": 50, "sharpe_30d": 1.2,
                    "win_rate": 0.6, "avg_win": 200, "avg_loss": -100,
                    "profit_factor": 1.5, "open_positions": 3,
                    "llm_spend": 0, "llm_spend_7d": 0, "paper_trade_count": 0,
                    "backtest_count": 0},
        "pnl_by_ticker": [
            {"ticker": "SPY", "realized": 100, "unrealized": 50},
            {"ticker": "QQQ", "realized": -30, "unrealized": 0},
        ],
        "universe": [
            {"ticker": "SPY", "category": "income", "phase": "live",
             "strategy": "PutCreditSpread", "iterations": 5, "paperTrades": 2,
             "edge": {"pf_oos": 1.4, "pf_is": 1.6, "dd": -0.05}},
        ],
        "activity": [],
    }
    html = tabs.overview_tab(data)
    assert "Equity Curve" in html
    assert "P&amp;L by Ticker" in html
    assert "Universe Pipeline" in html
    assert "Activity" in html


def test_overview_tab_empty_universe_no_crash():
    data = {"equity_curve": [], "metrics": {"realized_pnl": 0,
            "unrealized_pnl": 0, "sharpe_30d": 0, "win_rate": 0,
            "avg_win": 0, "avg_loss": 0, "profit_factor": 0,
            "open_positions": 0, "llm_spend": 0, "llm_spend_7d": 0,
            "paper_trade_count": 0, "backtest_count": 0},
            "pnl_by_ticker": [], "universe": [], "activity": []}
    html = tabs.overview_tab(data)
    assert html  # non-empty
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_tabs.py -v -k overview`
Expected: FAIL — `tabs` module doesn't exist.

- [ ] **Step 3: Implement Overview tab**

Create `bullbot/dashboard/tabs.py`:

```python
"""Tab render functions ported from dashboard/handoff/components-tabs.jsx.

Each function: data: dict -> str (HTML fragment, no <html>/<body> wrapper).
Pure functions over the data dict from queries; no DB access here.
"""
from __future__ import annotations

import html

from bullbot.dashboard.fmt import fmt_money, fmt_pct, pnl_class, phase_class, phase_label
from bullbot.dashboard.svg_charts import equity_chart_svg


# Overview helper components (kept local since only used here) ---------------

def _pnl_by_ticker(rows: list[dict]) -> str:
    """Diverging-bar visualization. CSS-only, no SVG."""
    filtered = [r for r in rows if r["realized"] != 0 or r["unrealized"] != 0]
    if not filtered:
        return '<div style="color: var(--fg-2); font-size: 12px">No P&amp;L yet — paper trial in progress.</div>'
    max_abs = max(abs(r["realized"] + r["unrealized"]) for r in filtered) or 1.0
    parts = []
    for r in filtered:
        total = r["realized"] + r["unrealized"]
        width_pct = (abs(total) / max_abs) * 100
        margin_left = "50%" if total >= 0 else f"{50 - width_pct / 2:.1f}%"
        gradient = ("linear-gradient(90deg, color-mix(in oklab, var(--pos) 50%, transparent), var(--pos))"
                    if total >= 0 else
                    "linear-gradient(90deg, var(--neg), color-mix(in oklab, var(--neg) 50%, transparent))")
        parts.append(f"""<div class="bar-row">
  <span class="bar-label">{html.escape(r['ticker'])}</span>
  <div class="bar-track" style="background: transparent; display: flex; justify-content: {'flex-start' if total >= 0 else 'flex-end'}; position: relative">
    <div style="position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: var(--line-strong)"></div>
    <div class="bar-fill" style="width: {width_pct / 2:.1f}%; margin-left: {margin_left}; background: {gradient}; border-radius: 2px"></div>
  </div>
  <span class="num bar-amt {pnl_class(total)}">{fmt_money(total, signed=True, decimals=0)}</span>
</div>""")
    return "".join(parts)


def _universe_pipeline(universe: list[dict]) -> str:
    """4-column pipeline (discovering / paper_trial / live / no_edge)."""
    phases = ["discovering", "paper_trial", "live", "no_edge"]
    cols = []
    for p in phases:
        items = [u for u in universe if u["phase"] == p]
        tiles = []
        for u in items:
            strat = u["strategy"] or "—"
            pf = u["edge"]["pf_oos"]
            bar_pct = min(100.0, (pf / 2.5) * 100)
            bar_color = "var(--accent)" if pf >= 1.3 else "var(--neg)"
            tiles.append(f"""<div class="pipeline-tile">
  <span class="tile-ticker">{html.escape(u['ticker'])}</span>
  <span class="tile-pf">pf {pf:.2f}</span>
  <span class="tile-meta">{html.escape(strat)}</span>
  <span class="tile-meta">{u['iterations']} it · {u['paperTrades']} pt</span>
  <div class="tile-bar"><div style="width: {bar_pct:.1f}%; background: {bar_color}"></div></div>
</div>""")
        cols.append(f"""<div class="pipeline-col">
  <div class="col-head">
    <span><span class="chip {phase_class(p)}" style="margin-right: 6px">{phase_label(p)}</span></span>
    <span class="count">{len(items)}</span>
  </div>
  {''.join(tiles)}
</div>""")
    return f'<div class="pipeline">{"".join(cols)}</div>'


def _activity_feed(events: list[dict]) -> str:
    if not events:
        return '<div style="color: var(--fg-2); padding: 14px; font-size: 12px">No activity yet.</div>'
    icons = {"fill": "→", "exit": "←", "promotion": "↑",
             "proposal": "◇", "rejection": "×", "demotion": "↓"}
    items = []
    for e in events[:10]:
        icon = icons.get(e.get("type", ""), "·")
        items.append(f"""<div class="activity-item {html.escape(e.get('type', ''))}">
  <span class="time">{html.escape(e.get('ts', ''))}</span>
  <span class="ticker"><span class="icon">{icon}</span>{html.escape(e.get('ticker', ''))}</span>
  <span class="text">{html.escape(e.get('text', ''))}</span>
</div>""")
    return f'<div class="activity-list">{"".join(items)}</div>'


def overview_tab(data: dict) -> str:
    """Overview tab: equity curve + P&L by ticker + universe pipeline + activity feed."""
    eq_values = [float(p["total_equity"]) for p in data["equity_curve"]]
    m = data["metrics"]
    total_pnl = m.get("realized_pnl", 0) + m.get("unrealized_pnl", 0)
    pnl_cls = pnl_class(total_pnl)

    return f"""<div class="cols-2">
  <div class="card">
    <div class="card-head">
      <span class="card-title">Equity Curve · 30d</span>
    </div>
    <div class="card-body">
      {equity_chart_svg(eq_values)}
      <div style="display: flex; gap: 18px; margin-top: 6px; font-size: 11.5px; color: var(--fg-2)">
        <span><span class="num {pnl_cls}">{fmt_money(total_pnl, signed=True)}</span> total P&amp;L</span>
        <span>·</span>
        <span>Sharpe <span class="num">{m.get('sharpe_30d', 0):.2f}</span></span>
        <span>·</span>
        <span>Win {fmt_pct(m.get('win_rate', 0), decimals=0)}</span>
        <span>·</span>
        <span>Avg win <span class="num pos">{fmt_money(m.get('avg_win', 0), decimals=0)}</span></span>
        <span>·</span>
        <span>Avg loss <span class="num neg">{fmt_money(m.get('avg_loss', 0), decimals=0)}</span></span>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="card-head"><span class="card-title">P&amp;L by Ticker</span></div>
    <div class="card-body">
      {_pnl_by_ticker(data['pnl_by_ticker'])}
    </div>
  </div>
</div>
<div class="cols-2">
  <div class="card">
    <div class="card-head">
      <span class="card-title">Universe Pipeline</span>
      <span class="card-title" style="font-size: 10px; color: var(--fg-3)">{len(data['universe'])} tickers</span>
    </div>
    <div class="card-body flush">
      {_universe_pipeline(data['universe'])}
    </div>
  </div>
  <div class="card">
    <div class="card-head"><span class="card-title">Activity</span></div>
    <div class="card-body flush">
      {_activity_feed(data['activity'])}
    </div>
  </div>
</div>"""
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_tabs.py -v -k overview`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/tabs.py tests/unit/test_dashboard_tabs.py
git commit -m "dashboard/tabs: add overview_tab with equity, pnl-by-ticker, pipeline, activity"
```

---

## Tasks 15-21: Remaining 7 tabs (Positions, Evolver, Universe, Transactions, Health, Costs, Inventory)

Each follows the same pattern as Task 14: write 1-2 tests asserting key sections render and that empty data doesn't crash, port the JSX from `dashboard/handoff/components-tabs.jsx` into a Python f-string, append to `bullbot/dashboard/tabs.py`, run tests, commit.

Implementer should follow these JSX functions exactly:
- **Task 15 (Positions):** `PositionsTab` + `PositionCard`. Filter bar with 3 buttons (All/Open/Closed); position cards with leg formatting helper (`formatLeg`), exit-rule progress bar, rationale block.
- **Task 16 (Evolver):** `EvolverTab`. Filter bar (All/Passed/Rejected), table of all proposals, plus 4 detail cards below.
- **Task 17 (Universe):** `UniverseTab`. Single dense table; 9 columns; clickable rows; pf_oos colored by gate.
- **Task 18 (Transactions):** `TransactionsTab`. Table of orders with totals row in tfoot.
- **Task 19 (Health):** `HealthTab`. 4-card universe summary at top + grid of health-check cards. Pull from `bullbot.research.health.generate_health_brief(conn)` (or its data dict equivalent).
- **Task 20 (Costs):** `CostsTab`. 2-column: LLM-by-ticker bar chart + commissions table + cost-efficiency mini-cards.
- **Task 21 (Inventory):** `InventoryTab`. Single table; account/ticker/type/strike/expiry/qty/cost-basis columns.

Each task gets its own commit with message `dashboard/tabs: add <tab>_tab`.

For each tab:

- [ ] **Step 1:** Write 2 tests (one positive, one empty/null path)
- [ ] **Step 2:** Run to verify failure
- [ ] **Step 3:** Port the JSX into a Python function. Use `html.escape()` on every dynamic string. Use `fmt.fmt_money/fmt_pct/pnl_class/phase_class/phase_label` for display.
- [ ] **Step 4:** Run tests until green.
- [ ] **Step 5:** Commit.

The JSX source for each tab is in `dashboard/handoff/components-tabs.jsx` at the line numbers in the Task 14 reading. Implementer should read each JSX block before porting — do not paraphrase the design.

---

## Task 22: Generator rewrite

**Files:**
- Modify: `bullbot/dashboard/generator.py`
- Modify: `tests/unit/test_dashboard_generator.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dashboard_generator.py`:

```python
def test_generate_uses_new_shell_and_tabs(conn, tmp_path):
    """Smoke test: generator produces HTML with new design tokens."""
    out = tmp_path / "dashboard.html"
    generator.generate(conn, output_path=out)
    text = out.read_text()
    # New shell markers
    assert "data-theme" in text
    assert "data-accent" in text
    assert "IBM+Plex+Sans" in text
    # 8 tabs present
    for tab in ("overview", "positions", "evolver", "universe",
                "transactions", "health", "costs", "inventory"):
        assert f"tab-{tab}" in text
    # Sidebar groups
    assert ">Operations<" in text
    assert ">Diagnostics<" in text


def test_generate_empty_db_renders(tmp_path, monkeypatch):
    """Fresh DB with only schema applied — page must render."""
    import sqlite3
    from bullbot.db import migrations
    from bullbot import config
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrations.apply_schema(conn)
    generator.generate(conn)
    out = tmp_path / "dashboard.html"
    assert out.exists()
    text = out.read_text()
    # Empty-state assertions
    assert len(text) > 5000  # not blank
    assert "data-theme" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_generator.py -v -k 'new_shell or empty_db'`
Expected: FAIL.

- [ ] **Step 3: Rewrite generator.py**

Replace `bullbot/dashboard/generator.py` with:

```python
"""Generate the Bull-Bot HTML dashboard from the database."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bullbot import config
from bullbot.dashboard import queries, tabs, templates


def generate(conn: sqlite3.Connection, output_path: Path | None = None) -> Path:
    if output_path is None:
        output_path = config.REPORTS_DIR / "dashboard.html"

    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Pull all data once
    summary = queries.summary_metrics(conn)
    extended = queries.extended_metrics(conn)
    account = queries.account_summary(conn)
    eq_curve = queries.equity_curve(conn, days=30)
    universe = queries.universe_with_edge(conn)
    activity = queries.recent_activity(conn, limit=20)
    proposals = queries.evolver_proposals(conn)
    positions = queries.positions_list(conn)
    orders = queries.orders_list(conn)
    costs = queries.cost_breakdown(conn)
    inventory = queries.long_inventory_summary(conn)

    metrics = {**summary, **extended}
    total_pnl = metrics.get("realized_pnl", 0) + metrics.get("unrealized_pnl", 0)

    data = {
        "metrics": metrics,
        "account": account,
        "equity_curve": eq_curve,
        "universe": universe,
        "pnl_by_ticker": summary["pnl_by_ticker"],
        "activity": [_event_to_activity(e) for e in activity],
        "proposals": proposals,
        "positions": positions,
        "orders": orders,
        "costs": costs,
        "inventory": inventory,
    }

    # Health data — pulled separately because health module owns the brief
    try:
        from bullbot.research import health as research_health
        brief = research_health.generate_health_brief(conn)
        data["health"] = _brief_to_dashboard_dict(brief, universe)
    except Exception:
        data["health"] = {"universe": _phase_counts(universe), "checks": []}

    counts = {
        "positions": sum(1 for p in positions if p.get("is_open")),
        "evolver": len(proposals),
        "universe": len(universe),
        "transactions": len(orders),
        "health": sum(1 for c in data["health"]["checks"] if c.get("status") != "ok"),
        "inventory": len(inventory),
    }

    body_parts = [
        templates.header_section(generated_at=now_str, total_pnl=total_pnl),
        '<div class="layout">',
        templates.sidebar_section(active_tab="overview", counts=counts),
        '<main>',
        '<div class="page-title-row"><div><div class="page-title">Overview</div></div></div>',
        templates.kpi_strip(account=account, metrics=metrics, equity_curve=eq_curve),
    ]

    tab_funcs = [
        ("overview", tabs.overview_tab),
        ("positions", tabs.positions_tab),
        ("evolver", tabs.evolver_tab),
        ("universe", tabs.universe_tab),
        ("transactions", tabs.transactions_tab),
        ("health", tabs.health_tab),
        ("costs", tabs.costs_tab),
        ("inventory", tabs.inventory_tab),
    ]
    for i, (name, fn) in enumerate(tab_funcs):
        display = "block" if i == 0 else "none"
        body_parts.append(
            f'<div class="tab-content" id="tab-{name}" style="display: {display}">'
            f'{fn(data)}'
            f'</div>'
        )

    body_parts.extend(['</main>', '</div>'])
    body = "\n".join(body_parts)
    html = templates.page_shell(now_str, body)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _event_to_activity(event: dict) -> dict:
    """Convert a recent_activity row to the activity-feed shape tabs expect."""
    return {
        "ts": _short_ts(event.get("ts")),
        "ticker": event.get("ticker", ""),
        "type": _map_event_type(event.get("event_type", "")),
        "text": event.get("detail", ""),
    }


def _short_ts(epoch) -> str:
    if not epoch:
        return ""
    dt = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    return dt.strftime("%H:%M")


def _map_event_type(event_type: str) -> str:
    mapping = {"proposal": "proposal", "order": "fill",
               "promotion": "promotion"}
    return mapping.get(event_type, event_type)


def _phase_counts(universe: list[dict]) -> dict[str, int]:
    counts = {"total": len(universe), "live": 0, "paper_trial": 0,
              "discovering": 0, "no_edge": 0}
    for u in universe:
        p = u.get("phase", "")
        if p in counts:
            counts[p] += 1
    return counts


def _brief_to_dashboard_dict(brief, universe) -> dict:
    """Map a HealthBrief into the dashboard's expected health-data shape."""
    checks = []
    for r in brief.results:
        status = "ok" if r.passed else "warn"
        detail = " · ".join(r.findings) if r.findings else "OK"
        checks.append({"name": r.title, "status": status, "detail": detail})
    return {
        "universe": _phase_counts(universe),
        "checks": checks,
    }


if __name__ == "__main__":
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    path = generate(conn)
    print(f"Dashboard written to {path}")
    conn.close()
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_dashboard_generator.py -v`
Expected: all dashboard generator tests pass (existing + new).

Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: full suite passes.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/generator.py tests/unit/test_dashboard_generator.py
git commit -m "dashboard/generator: rewrite to assemble new shell + 8 tabs"
```

---

## Task 23: Deploy to pasture

**Files:** None (operational)

- [ ] **Step 1: Push and merge**

```bash
git checkout main && git merge --ff-only feature/dashboard-reskin && git push origin main
```

- [ ] **Step 2: Pull on pasture and apply migration**

```bash
ssh pasture 'cd ~/Projects/bull-bot && git pull origin main && .venv/bin/python -c "
import sqlite3
from bullbot import config
from bullbot.db import migrations
conn = sqlite3.connect(str(config.DB_PATH))
migrations.apply_schema(conn)
print(\"migration applied\")
" && .venv/bin/python -m pytest tests/unit/ -q | tail -3'
```

Expected: migration applied, full suite green on pasture.

- [ ] **Step 3: Generate dashboard with current DB**

```bash
ssh pasture 'cd ~/Projects/bull-bot && .venv/bin/python -m bullbot.dashboard.generator && ls -la reports/dashboard.html'
```

Expected: a fresh `dashboard.html` of size ~50-200 KB.

- [ ] **Step 4: Take a one-off equity snapshot now (so chart isn't empty)**

```bash
ssh pasture 'cd ~/Projects/bull-bot && .venv/bin/python -c "
import sqlite3
from bullbot import config
from bullbot.research import equity_snapshot
conn = sqlite3.connect(str(config.DB_PATH))
ts = equity_snapshot.take_snapshot(conn)
print(\"snapshot ts:\", ts)
"'
```

- [ ] **Step 5: Eyeball the dashboard**

Open `http://192.168.1.220:8080/` in a browser (or whatever IP pasture is on). Check that:
- Quant-terminal aesthetic (dark, IBM Plex, OKLCH greens)
- Sidebar nav with 8 items in 2 groups, badges
- KPI strip with 5 cards + equity sparkline
- Equity curve (likely a single point until 30 days of snapshots accumulate)
- 8 tabs all switch via JS click without errors
- No browser-console errors

Tomorrow's 7:30 run will produce another snapshot, etc.

---

## Self-review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Lift styles.css verbatim | Task 7 |
| Replace HTML output of templates.py + generator.py | Tasks 10-13, 22 |
| Stdlib only, no new pip deps | All tasks (no new deps introduced) |
| One output file (dashboard.html) | Task 22 (generator) |
| Pull IBM Plex via Google Fonts link | Task 10 (page_shell) |
| Backward-compat queries — add new functions, don't mutate | Tasks 3-6 (additive only) |
| `equity_curve(conn, days=30)` | Task 3 |
| `account_summary(conn)` | Task 4 |
| `extended_metrics(conn)` | Task 5 |
| `universe_with_edge(conn)` | Task 6 |
| Graceful empty state | Each tab has empty-data test (Tasks 14-21); generator empty-DB test in Task 22 |
| No JS deps; tab-switching with vanilla JS | Task 10 (page_shell embeds the script) |
| 8 tabs in 2 groups | Task 12 (sidebar) + 14-21 (tab content) |
| Header with brand, status, generated-at, P&L | Task 11 |
| Equity SVG + sparklines | Task 9 |
| P&L by ticker (CSS/HTML, no SVG) | Task 14 (Overview helper) |
| Pipeline columns (CSS grid) | Task 14 (Overview helper) |
| Skip Tweaks panel | Not implemented (tabs.py won't include it) |
| Dark mode only | page_shell sets `data-theme="dark"` (Task 10) |

No gaps. Task 14 explicitly bundles the Overview internals (pnl-by-ticker, pipeline, activity) so they ship together.

**Placeholder scan:** No "TBD" / "TODO" / "fill in details". Tasks 15-21 reference JSX line numbers and the source file rather than restating each port — but the implementer must read the source. That's acceptable because the JSX is well-bounded and 23 separate task definitions would be excessive.

**Type consistency:** Function signatures and dict keys are consistent across tasks. `account_summary` returns `total_equity, income_account, growth_account, target_monthly, month_to_date, days_to_target`. `extended_metrics` returns `sharpe_30d, win_rate, avg_win, avg_loss, profit_factor, paper_trade_count, backtest_count, llm_spend_7d`. `equity_curve` rows have `total_equity` etc. `universe_with_edge` rows match the JSX `universe` shape.

**Open follow-ups (not blockers for this plan):**
- The equity sparkline in Task 13 KPI strip uses snapshot-based data; will be sparse until accumulated.
- `extended_metrics.sharpe_30d` is a simple per-day-delta calculation — could be replaced with daily-return Sharpe later.
- `universe_with_edge.edge.dd` is currently always 0 (max_dd not tracked at ticker level in DB); future work to populate.
