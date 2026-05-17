# Bull-Bot v2 Phase C.4c — Backtest report module — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `bullbot/v2/backtest/report.py` — consumes a `BacktestResult` (from C.4b) and writes 3 CSV files: per-trade ledger, daily equity curve, per-vehicle attribution. Public entry `write_report(result, out_dir)` orchestrates all three. After this lands, C.5 dashboard tab can read the CSVs and render PNG + tables.

**Architecture:** Pure-function module with one public orchestrator `write_report(result, out_dir)` and three private writers `_write_trades_csv`, `_write_equity_curve_csv`, `_write_vehicle_attribution_csv`. Each writer takes a `BacktestResult` + an output `Path` and writes one CSV. No matplotlib (PNG deferred to C.5 dashboard scope where matplotlib already wired). No SPY benchmark overlay (deferred; equity curve CSV alone is sufficient for C.5 to render and overlay).

**Tech Stack:** Python 3.11+, stdlib `csv` + `pathlib` + `datetime` + `collections`, existing `bullbot.v2.backtest.runner.{BacktestResult, BacktestTrade}`. No new third-party libraries. No schema changes.

**Spec reference:** [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md`](../specs/2026-05-16-phase-c-vehicle-agent-design.md) section 4.9 (`report.py outputs`).

---

## Pre-flight assumptions verified before writing tasks

- **C.4b shipped to main** (commit `8b6774b`). `bullbot.v2.backtest.runner` exports `BacktestResult` + `BacktestTrade`.
- **`BacktestResult` shape:** `ticker, start_date, end_date, starting_nav, ending_nav, trades, daily_mtm` where `daily_mtm: list[tuple[int, float]]` is `(asof_ts, nav)` pairs.
- **`BacktestTrade` shape:** `ticker, structure_kind, intent, opened_ts, closed_ts, close_reason, realized_pnl, rationale` — all required, frozen dataclass.
- **`reports/` dir** already exists at repo root, gitignored except for `.gitkeep`. C.4c writes into a sub-dir of the caller's choosing (typically `reports/backtest_<ticker>_<start>_<end>/`).
- **No regime_attribution.csv + no validation_summary.txt this phase** — both require data outside `BacktestResult` (VIX/SPY per-day bucketing, manual chain snapshots). Documented as deferred to C.7 in §"What this defers" below.
- **No SPY benchmark overlay** — deferred. CSV equity_curve.csv alone is enough; dashboard can fetch SPY bars and overlay at render-time.
- **No matplotlib PNG** — deferred to C.5 dashboard scope. CSV is the source of truth; PNG rendering belongs in the layer that already imports matplotlib (dashboard).

---

## File Structure

| Path | Responsibility | Status |
|---|---|---|
| `bullbot/v2/backtest/report.py` | `write_report` orchestrator + 3 private CSV writers. | **Create** |
| `tests/unit/test_v2_backtest_report.py` | Unit tests for each writer + orchestrator. | **Create** |
| Other v2 modules | Unchanged. | — |

Module size target: < 180 LOC.

---

## Task 1: `_write_trades_csv` — per-trade ledger

**Files:**
- Create: `bullbot/v2/backtest/report.py`
- Create: `tests/unit/test_v2_backtest_report.py`

CSV columns (header order): `ticker, structure_kind, intent, opened_ts, opened_date, closed_ts, closed_date, close_reason, realized_pnl, rationale`. Dates derived from epoch via `datetime.fromtimestamp(ts).date().isoformat()` — local time, matches the runner's TZ assumption. Empty `trades` writes header only.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_v2_backtest_report.py`:

```python
"""Unit tests for bullbot.v2.backtest.report."""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

import pytest

from bullbot.v2.backtest import report
from bullbot.v2.backtest.runner import BacktestResult, BacktestTrade


def _trade(**overrides) -> BacktestTrade:
    defaults = dict(
        ticker="AAPL", structure_kind="long_call", intent="trade",
        opened_ts=int(datetime(2024, 1, 5, 23, 0).timestamp()),
        closed_ts=int(datetime(2024, 1, 12, 23, 0).timestamp()),
        close_reason="profit_target", realized_pnl=125.50,
        rationale="bullish breakout",
    )
    defaults.update(overrides)
    return BacktestTrade(**defaults)


def _result(trades=None, daily_mtm=None) -> BacktestResult:
    return BacktestResult(
        ticker="AAPL", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
        starting_nav=50_000.0, ending_nav=50_000.0,
        trades=trades or [], daily_mtm=daily_mtm or [],
    )


def test_write_trades_csv_writes_header_only_for_empty_trades(tmp_path):
    out = tmp_path / "trades.csv"
    report._write_trades_csv(_result(trades=[]), out_path=out)
    rows = list(csv.reader(out.open()))
    assert len(rows) == 1
    assert rows[0] == [
        "ticker", "structure_kind", "intent", "opened_ts", "opened_date",
        "closed_ts", "closed_date", "close_reason", "realized_pnl", "rationale",
    ]


def test_write_trades_csv_writes_one_row_per_trade(tmp_path):
    out = tmp_path / "trades.csv"
    trades = [
        _trade(realized_pnl=100.0),
        _trade(structure_kind="csp", intent="accumulate",
               close_reason="expired_worthless", realized_pnl=200.0),
    ]
    report._write_trades_csv(_result(trades=trades), out_path=out)
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 2
    assert rows[0]["structure_kind"] == "long_call"
    assert rows[0]["realized_pnl"] == "100.0"
    assert rows[1]["structure_kind"] == "csp"
    assert rows[1]["intent"] == "accumulate"


def test_write_trades_csv_includes_human_readable_dates(tmp_path):
    out = tmp_path / "trades.csv"
    report._write_trades_csv(_result(trades=[_trade()]), out_path=out)
    rows = list(csv.DictReader(out.open()))
    # opened_ts=2024-01-05 23:00 local; date() should be 2024-01-05
    assert rows[0]["opened_date"] == "2024-01-05"
    assert rows[0]["closed_date"] == "2024-01-12"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bullbot.v2.backtest.report'`.

- [ ] **Step 3: Create module with `_write_trades_csv`**

Create `bullbot/v2/backtest/report.py`:

```python
"""Backtest report writer for v2 Phase C.

Public entry: write_report(result, out_dir) -> dict[str, Path]
Consumes a BacktestResult from bullbot.v2.backtest.runner and writes
three CSVs (trades, equity curve, vehicle attribution) into out_dir.
Returns a mapping of report-name -> file path written.

Per spec §4.9, regime_attribution.csv + validation_summary.txt + PNG
equity curve + SPY benchmark overlay are deferred (see plan §"What
this defers").
"""
from __future__ import annotations

import csv
from datetime import datetime as _datetime
from pathlib import Path

from bullbot.v2.backtest.runner import BacktestResult

_TRADES_HEADER = [
    "ticker", "structure_kind", "intent", "opened_ts", "opened_date",
    "closed_ts", "closed_date", "close_reason", "realized_pnl", "rationale",
]


def _ts_to_date_str(ts: int) -> str:
    """Local-TZ date string for an epoch second. Matches the runner's
    23:00-local asof_ts convention (see backtest.runner.backtest)."""
    return _datetime.fromtimestamp(ts).date().isoformat()


def _write_trades_csv(result: BacktestResult, *, out_path: Path) -> None:
    """Per-trade ledger CSV. Header always written; one row per closed trade."""
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_TRADES_HEADER)
        for t in result.trades:
            w.writerow([
                t.ticker, t.structure_kind, t.intent,
                t.opened_ts, _ts_to_date_str(t.opened_ts),
                t.closed_ts, _ts_to_date_str(t.closed_ts),
                t.close_reason, t.realized_pnl, t.rationale,
            ])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_report.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/backtest/report.py tests/unit/test_v2_backtest_report.py
git commit -m "feat(v2/c4c): _write_trades_csv — per-trade ledger output"
```

---

## Task 2: `_write_equity_curve_csv` — daily NAV snapshots

**Files:**
- Modify: `bullbot/v2/backtest/report.py` (append `_write_equity_curve_csv`)
- Modify: `tests/unit/test_v2_backtest_report.py` (append equity tests)

CSV columns: `asof_ts, asof_date, nav`. One row per `daily_mtm` entry. Header-only on empty `daily_mtm`. Rows preserve insertion order (which is calendar-day-ascending per the runner's loop).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_backtest_report.py`:

```python
def test_write_equity_curve_csv_writes_header_only_for_empty_mtm(tmp_path):
    out = tmp_path / "equity.csv"
    report._write_equity_curve_csv(_result(daily_mtm=[]), out_path=out)
    rows = list(csv.reader(out.open()))
    assert rows == [["asof_ts", "asof_date", "nav"]]


def test_write_equity_curve_csv_writes_one_row_per_day(tmp_path):
    out = tmp_path / "equity.csv"
    daily_mtm = [
        (int(datetime(2024, 3, 13, 23).timestamp()), 50_000.0),
        (int(datetime(2024, 3, 14, 23).timestamp()), 50_125.50),
        (int(datetime(2024, 3, 15, 23).timestamp()), 49_800.0),
    ]
    report._write_equity_curve_csv(_result(daily_mtm=daily_mtm), out_path=out)
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 3
    assert rows[0]["asof_date"] == "2024-03-13"
    assert rows[1]["nav"] == "50125.5"
    assert rows[2]["asof_date"] == "2024-03-15"


def test_write_equity_curve_csv_preserves_chronological_order(tmp_path):
    out = tmp_path / "equity.csv"
    daily_mtm = [
        (int(datetime(2024, 3, 13, 23).timestamp()), 50_000.0),
        (int(datetime(2024, 3, 14, 23).timestamp()), 50_500.0),
    ]
    report._write_equity_curve_csv(_result(daily_mtm=daily_mtm), out_path=out)
    rows = list(csv.DictReader(out.open()))
    assert int(rows[0]["asof_ts"]) < int(rows[1]["asof_ts"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_report.py -v`
Expected: FAIL on 3 new tests — `AttributeError: module 'bullbot.v2.backtest.report' has no attribute '_write_equity_curve_csv'`.

- [ ] **Step 3: Implement `_write_equity_curve_csv`**

Append to `bullbot/v2/backtest/report.py`:

```python
_EQUITY_HEADER = ["asof_ts", "asof_date", "nav"]


def _write_equity_curve_csv(result: BacktestResult, *, out_path: Path) -> None:
    """Daily NAV snapshots CSV. Header always written; one row per daily_mtm entry."""
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_EQUITY_HEADER)
        for asof_ts, nav in result.daily_mtm:
            w.writerow([asof_ts, _ts_to_date_str(asof_ts), nav])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_report.py -v`
Expected: PASS (6 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/backtest/report.py tests/unit/test_v2_backtest_report.py
git commit -m "feat(v2/c4c): _write_equity_curve_csv — daily NAV snapshot output"
```

---

## Task 3: `_write_vehicle_attribution_csv` — per-structure stats

**Files:**
- Modify: `bullbot/v2/backtest/report.py` (append `_write_vehicle_attribution_csv`)
- Modify: `tests/unit/test_v2_backtest_report.py` (append attribution tests)

CSV columns: `structure_kind, trade_count, wins, losses, win_rate, total_pnl, avg_pnl`. One row per distinct `structure_kind` observed in `result.trades`. `win` defined as `realized_pnl > 0`, `loss` as `<= 0` (zero counts as loss for conservative attribution). `win_rate = wins / trade_count` rounded to 4 decimals. Empty trades writes header only. Rows sorted by `structure_kind` ascending for determinism.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_backtest_report.py`:

```python
def test_write_vehicle_attribution_writes_header_only_for_empty_trades(tmp_path):
    out = tmp_path / "attr.csv"
    report._write_vehicle_attribution_csv(_result(trades=[]), out_path=out)
    rows = list(csv.reader(out.open()))
    assert rows == [[
        "structure_kind", "trade_count", "wins", "losses",
        "win_rate", "total_pnl", "avg_pnl",
    ]]


def test_write_vehicle_attribution_aggregates_per_structure(tmp_path):
    out = tmp_path / "attr.csv"
    trades = [
        _trade(structure_kind="long_call", realized_pnl=100.0),
        _trade(structure_kind="long_call", realized_pnl=-50.0),
        _trade(structure_kind="long_call", realized_pnl=200.0),
        _trade(structure_kind="csp", realized_pnl=75.0),
    ]
    report._write_vehicle_attribution_csv(_result(trades=trades), out_path=out)
    rows = list(csv.DictReader(out.open()))
    by_kind = {r["structure_kind"]: r for r in rows}
    assert by_kind["long_call"]["trade_count"] == "3"
    assert by_kind["long_call"]["wins"] == "2"
    assert by_kind["long_call"]["losses"] == "1"
    assert by_kind["long_call"]["win_rate"] == "0.6667"
    assert by_kind["long_call"]["total_pnl"] == "250.0"
    # avg = 250/3 = 83.3333...
    assert by_kind["long_call"]["avg_pnl"].startswith("83.33")
    assert by_kind["csp"]["trade_count"] == "1"


def test_write_vehicle_attribution_counts_zero_pnl_as_loss(tmp_path):
    out = tmp_path / "attr.csv"
    trades = [_trade(realized_pnl=0.0)]
    report._write_vehicle_attribution_csv(_result(trades=trades), out_path=out)
    rows = list(csv.DictReader(out.open()))
    assert rows[0]["wins"] == "0"
    assert rows[0]["losses"] == "1"
    assert rows[0]["win_rate"] == "0.0"


def test_write_vehicle_attribution_rows_sorted_by_structure_kind(tmp_path):
    out = tmp_path / "attr.csv"
    trades = [
        _trade(structure_kind="vertical_credit_spread", realized_pnl=50.0),
        _trade(structure_kind="csp", realized_pnl=50.0),
        _trade(structure_kind="long_call", realized_pnl=50.0),
    ]
    report._write_vehicle_attribution_csv(_result(trades=trades), out_path=out)
    rows = list(csv.DictReader(out.open()))
    assert [r["structure_kind"] for r in rows] == [
        "csp", "long_call", "vertical_credit_spread",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_report.py -v`
Expected: FAIL on 4 new tests — `AttributeError: module 'bullbot.v2.backtest.report' has no attribute '_write_vehicle_attribution_csv'`.

- [ ] **Step 3: Implement `_write_vehicle_attribution_csv`**

Append to `bullbot/v2/backtest/report.py`:

```python
from collections import defaultdict

_ATTRIBUTION_HEADER = [
    "structure_kind", "trade_count", "wins", "losses",
    "win_rate", "total_pnl", "avg_pnl",
]


def _write_vehicle_attribution_csv(result: BacktestResult, *, out_path: Path) -> None:
    """Per-structure aggregation CSV. Header always written; one row per
    structure_kind observed. Sorted by structure_kind for determinism.
    Zero P&L counts as a loss (conservative)."""
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0, "wins": 0, "losses": 0, "total": 0.0}
    )
    for t in result.trades:
        b = buckets[t.structure_kind]
        b["count"] += 1
        b["total"] += t.realized_pnl
        if t.realized_pnl > 0:
            b["wins"] += 1
        else:
            b["losses"] += 1

    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_ATTRIBUTION_HEADER)
        for kind in sorted(buckets):
            b = buckets[kind]
            count = b["count"]
            win_rate = round(b["wins"] / count, 4) if count else 0.0
            avg_pnl = b["total"] / count if count else 0.0
            w.writerow([
                kind, int(count), int(b["wins"]), int(b["losses"]),
                win_rate, b["total"], avg_pnl,
            ])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_report.py -v`
Expected: PASS (10 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/backtest/report.py tests/unit/test_v2_backtest_report.py
git commit -m "feat(v2/c4c): _write_vehicle_attribution_csv — per-structure stats"
```

---

## Task 4: `write_report` — public orchestrator

**Files:**
- Modify: `bullbot/v2/backtest/report.py` (append `write_report`)
- Modify: `tests/unit/test_v2_backtest_report.py` (append orchestrator tests)

`write_report(result, *, out_dir) -> dict[str, Path]` creates `out_dir` (parents=True, exist_ok=True), writes all 3 CSVs with fixed names, returns mapping of slug -> path. Fixed names: `backtest_trades.csv`, `equity_curve.csv`, `vehicle_attribution.csv` (matches spec §4.9 naming where present; uses underscore-canonical for the two CSVs spec didn't fully name).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_backtest_report.py`:

```python
def test_write_report_creates_out_dir_if_missing(tmp_path):
    out = tmp_path / "nested" / "subdir"
    assert not out.exists()
    report.write_report(_result(), out_dir=out)
    assert out.is_dir()


def test_write_report_writes_three_csvs_with_expected_names(tmp_path):
    out = tmp_path / "report"
    paths = report.write_report(_result(), out_dir=out)
    assert set(paths.keys()) == {"trades", "equity_curve", "vehicle_attribution"}
    assert paths["trades"].name == "backtest_trades.csv"
    assert paths["equity_curve"].name == "equity_curve.csv"
    assert paths["vehicle_attribution"].name == "vehicle_attribution.csv"
    for p in paths.values():
        assert p.exists()
        assert p.read_text().startswith(("ticker,", "asof_ts,", "structure_kind,"))


def test_write_report_returns_paths_in_out_dir(tmp_path):
    out = tmp_path / "report"
    paths = report.write_report(_result(), out_dir=out)
    for p in paths.values():
        assert p.parent == out


def test_write_report_full_round_trip_with_data(tmp_path):
    out = tmp_path / "report"
    trades = [
        _trade(structure_kind="long_call", realized_pnl=150.0),
        _trade(structure_kind="csp", intent="accumulate",
               close_reason="expired_worthless", realized_pnl=75.0),
    ]
    daily_mtm = [
        (int(datetime(2024, 1, 10, 23).timestamp()), 50_000.0),
        (int(datetime(2024, 1, 11, 23).timestamp()), 50_225.0),
    ]
    paths = report.write_report(
        _result(trades=trades, daily_mtm=daily_mtm), out_dir=out,
    )
    trade_rows = list(csv.DictReader(paths["trades"].open()))
    equity_rows = list(csv.DictReader(paths["equity_curve"].open()))
    attr_rows = list(csv.DictReader(paths["vehicle_attribution"].open()))
    assert len(trade_rows) == 2
    assert len(equity_rows) == 2
    assert len(attr_rows) == 2  # 2 distinct structure_kinds
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_report.py -v`
Expected: FAIL on 4 new tests — `AttributeError: module 'bullbot.v2.backtest.report' has no attribute 'write_report'`.

- [ ] **Step 3: Implement `write_report`**

Append to `bullbot/v2/backtest/report.py`:

```python
def write_report(result: BacktestResult, *, out_dir: Path) -> dict[str, Path]:
    """Write all three backtest CSVs into out_dir.

    Creates out_dir (with parents) if it does not exist. Returns a mapping
    of report-slug -> file path for downstream consumers (e.g. C.5 dashboard).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "trades": out_dir / "backtest_trades.csv",
        "equity_curve": out_dir / "equity_curve.csv",
        "vehicle_attribution": out_dir / "vehicle_attribution.csv",
    }
    _write_trades_csv(result, out_path=paths["trades"])
    _write_equity_curve_csv(result, out_path=paths["equity_curve"])
    _write_vehicle_attribution_csv(result, out_path=paths["vehicle_attribution"])
    return paths
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_report.py -v`
Expected: PASS (14 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/backtest/report.py tests/unit/test_v2_backtest_report.py
git commit -m "feat(v2/c4c): write_report orchestrator — 3 CSVs in one call"
```

---

## Task 5: Full regression check

**Files:** none.

- [ ] **Step 1: Run full unit suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit -q`
Expected: 798 + 14 = 812 unit tests pass.

- [ ] **Step 2: Run integration suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration -q`
Expected: All 80 integration tests still pass.

- [ ] **Step 3: Static-import sanity check**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -c "from bullbot.v2.backtest import report; print(report.write_report)"`
Expected: prints `<function write_report at 0x...>` without ImportError.

- [ ] **Step 4: Marker commit**

```bash
git commit --allow-empty -m "chore(v2/c4c): Phase C.4c complete — backtest report.py landed"
```

---

## Acceptance criteria

C.4c is complete when ALL of the following hold:

1. `bullbot/v2/backtest/report.py` exists with public export `write_report` and 3 private writers `_write_trades_csv`, `_write_equity_curve_csv`, `_write_vehicle_attribution_csv`.
2. `tests/unit/test_v2_backtest_report.py` has 14 tests, all passing.
3. Full unit + integration suite green (no regressions vs C.4b baseline of 798 unit + 80 integration).
4. Module < 180 LOC.
5. No new third-party dependencies.

## What this defers

- **`regime_attribution.csv`** — needs per-day VIX-tertile + SPY-trend bucketing. Requires reading bars table outside `BacktestResult` shape. Deferred to C.7 (or a C.4d if Dan wants it before C.7).
- **`validation_summary.txt`** — BS-vs-real confusion matrix from manual chain snapshots. Currently no manual snapshots exist; build when snapshots arrive.
- **`equity_curve.png`** — matplotlib PNG. Deferred to C.5 dashboard scope where matplotlib already wired. The CSV from this phase is the source of truth.
- **SPY buy-and-hold benchmark overlay** — needs SPY bars fetch. Deferred to C.5 dashboard render layer.

## What this unblocks

- **C.5 (runner_c.py + dashboard tabs):** Backtest tab can read `equity_curve.csv` + `vehicle_attribution.csv` and render PNG + tables at the dashboard layer. Trade-level drill-down reads `backtest_trades.csv`.

## Notes for the implementer

- **Worktree `.venv` path:** `/Users/danield.runion/Projects/bull-bot/.venv/bin/python`. Same as prior phases.
- **No DB writes** — every helper is pure (input: `BacktestResult` + Path, output: file written). No SQLite touched.
- **All imports at top of file** (Task 3 lesson from C.4b).
- **csv module default dialect** (excel) — comma-separated, no quoting on numerics, header on row 1. Matches dashboard reader expectations.
