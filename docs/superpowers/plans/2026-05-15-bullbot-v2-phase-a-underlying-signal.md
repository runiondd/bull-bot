# Bull-Bot v2 — Phase A: Underlying Directional Signal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a daily-run module that emits a rules-based directional signal (`bullish`/`bearish`/`chop` + confidence) for every UNIVERSE ticker into a new `directional_signals` table, plus a dashboard tile showing today's signals. End-state visible result: Dan opens the dashboard tomorrow morning and sees a list of tickers tagged with the bot's current read on each one. No options data needed. No trades yet — Phase B adds that.

**Architecture:** New `bullbot/v2/` package, parallel to existing code. Phase A is read-only — does not interfere with v1's daemon, leaderboard, or paper_trial. Pure additive change: new tables, new run path, new dashboard tile. Pasture's existing daily job calls the new entry point after the existing bar refresh. Underlying agent uses deterministic rules over recent bars (50/200 SMA cross, slope, distance, ATR-based confidence). No LLM in Phase A — that's Phase D. Vehicle agent + paper trading are Phase B/C, separate plans.

**Tech Stack:** Python 3.12, sqlite3, pytest, existing `bullbot/data/cache.py` bar loader, existing dashboard generator. No new external dependencies.

---

## File Structure

**Create:**
- `bullbot/v2/__init__.py` — package marker, version constant
- `bullbot/v2/signals.py` — `DirectionalSignal` dataclass + DB read/write
- `bullbot/v2/underlying.py` — rules-based signal generator (takes bars, returns DirectionalSignal)
- `bullbot/v2/runner.py` — entry point: iterate UNIVERSE, build snapshot, generate signal, persist
- `scripts/run_v2_daily.py` — thin CLI wrapper around `runner.run_once()`
- `tests/unit/test_v2_signals.py`
- `tests/unit/test_v2_underlying.py`
- `tests/integration/test_v2_runner.py`

**Modify:**
- `bullbot/db/migrations.py` — add `directional_signals` table
- `bullbot/dashboard/tabs.py` — add a "v2 signals" tile/tab
- `bullbot/cli.py` — register `run-v2-daily` subcommand

---

### Task 1: Migration for directional_signals table

**Files:**
- Modify: `bullbot/db/migrations.py`
- Test: `tests/unit/test_migrations.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_migrations.py`:

```python
def test_migration_creates_directional_signals_table():
    import sqlite3
    from bullbot.db import migrations

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE ticker_state (id INTEGER PRIMARY KEY, ticker TEXT NOT NULL UNIQUE, phase TEXT NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE strategies (id INTEGER PRIMARY KEY, class_name TEXT NOT NULL, class_version INTEGER NOT NULL, params TEXT NOT NULL, params_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE evolver_proposals (id INTEGER PRIMARY KEY, ticker TEXT, strategy_id INTEGER, passed_gate INTEGER, trade_count INTEGER, score_a REAL, regime_label TEXT, pf_is REAL, pf_oos REAL, max_loss_per_trade REAL, size_units INTEGER, proposer_model TEXT, created_at INTEGER);
    """)
    migrations.apply_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(directional_signals)")}
    assert cols == {
        "id", "ticker", "asof_ts", "direction", "confidence",
        "horizon_days", "rationale", "rules_version", "created_at",
    }, f"unexpected columns: {cols}"
    # Idempotent
    migrations.apply_schema(conn)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_migrations.py::test_migration_creates_directional_signals_table -v`
Expected: FAIL — table doesn't exist.

- [ ] **Step 3: Add migration step**

In `bullbot/db/migrations.py`, after the existing `ticker_state.best_cagr_oos` block, insert:

```python
    # directional_signals — added 2026-05-15 for v2 decoupled architecture.
    # One row per (ticker, asof_ts) produced by the rules-based underlying
    # agent. `direction` is one of "bullish"/"bearish"/"chop"/"no_edge".
    # `confidence` is 0.0–1.0. `horizon_days` is the trade window the signal
    # is valid over. `rules_version` lets us A/B different rule packs.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS directional_signals (
            id              INTEGER PRIMARY KEY,
            ticker          TEXT    NOT NULL,
            asof_ts         INTEGER NOT NULL,
            direction       TEXT    NOT NULL,
            confidence      REAL    NOT NULL,
            horizon_days    INTEGER NOT NULL,
            rationale       TEXT,
            rules_version   TEXT    NOT NULL,
            created_at      INTEGER NOT NULL,
            UNIQUE (ticker, asof_ts, rules_version)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ds_ticker_ts ON directional_signals (ticker, asof_ts DESC)"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/db/migrations.py tests/unit/test_migrations.py
git commit -m "feat(v2): add directional_signals table

Phase A of the v2 decoupled architecture: a daily rules-based agent
classifies each UNIVERSE ticker as bullish/bearish/chop/no_edge with a
confidence and horizon. This table stores one row per ticker per day
per rules_version — keyed unique so re-runs are idempotent."
```

---

### Task 2: DirectionalSignal dataclass + persistence helpers

**Files:**
- Create: `bullbot/v2/__init__.py`
- Create: `bullbot/v2/signals.py`
- Test: `tests/unit/test_v2_signals.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_v2_signals.py
"""Unit tests for bullbot.v2.signals."""
from __future__ import annotations

import sqlite3

import pytest

from bullbot.v2 import signals


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE directional_signals (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            asof_ts INTEGER NOT NULL,
            direction TEXT NOT NULL,
            confidence REAL NOT NULL,
            horizon_days INTEGER NOT NULL,
            rationale TEXT,
            rules_version TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE (ticker, asof_ts, rules_version)
        );
    """)
    return c


def test_directional_signal_dataclass_basic():
    s = signals.DirectionalSignal(
        ticker="AAPL",
        asof_ts=1_700_000_000,
        direction="bullish",
        confidence=0.65,
        horizon_days=30,
        rationale="50/200 SMA cross + RSI > 55",
        rules_version="v1",
    )
    assert s.ticker == "AAPL"
    assert s.direction == "bullish"
    assert 0.0 <= s.confidence <= 1.0


def test_save_and_load_roundtrip(conn):
    s = signals.DirectionalSignal(
        ticker="AAPL", asof_ts=1_700_000_000, direction="bullish",
        confidence=0.65, horizon_days=30, rationale="x", rules_version="v1",
    )
    signals.save(conn, s)
    loaded = signals.latest_for(conn, "AAPL", rules_version="v1")
    assert loaded is not None
    assert loaded.ticker == "AAPL"
    assert loaded.direction == "bullish"
    assert loaded.confidence == pytest.approx(0.65)


def test_save_is_idempotent_on_unique_key(conn):
    """Re-saving the same (ticker, asof_ts, rules_version) must not raise — replace."""
    s = signals.DirectionalSignal(
        ticker="AAPL", asof_ts=1_700_000_000, direction="bullish",
        confidence=0.65, horizon_days=30, rationale="x", rules_version="v1",
    )
    signals.save(conn, s)
    s2 = signals.DirectionalSignal(
        ticker="AAPL", asof_ts=1_700_000_000, direction="chop",
        confidence=0.40, horizon_days=14, rationale="y", rules_version="v1",
    )
    signals.save(conn, s2)
    loaded = signals.latest_for(conn, "AAPL", rules_version="v1")
    assert loaded.direction == "chop"


def test_direction_must_be_valid():
    with pytest.raises(ValueError):
        signals.DirectionalSignal(
            ticker="AAPL", asof_ts=0, direction="moonshot",
            confidence=0.5, horizon_days=14, rationale="", rules_version="v1",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_signals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bullbot.v2'`.

- [ ] **Step 3: Create the package + module**

Create `bullbot/v2/__init__.py`:
```python
"""Bull-bot v2 — decoupled-architecture package.

Phase A: directional-signal generation (rules-based).
Phase B: paper-trading runner (planned).
Phase C: vehicle agent (planned).
Phase D: LLM annotation layer (planned).
"""

V2_VERSION = "0.1.0"
```

Create `bullbot/v2/signals.py`:
```python
"""DirectionalSignal — the output of the v2 underlying agent.

One row per (ticker, asof_ts, rules_version) in `directional_signals`.
Schema-validated at construction so persisted rows are always meaningful.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

VALID_DIRECTIONS = ("bullish", "bearish", "chop", "no_edge")


@dataclass(frozen=True)
class DirectionalSignal:
    ticker: str
    asof_ts: int
    direction: str
    confidence: float
    horizon_days: int
    rationale: str
    rules_version: str

    def __post_init__(self) -> None:
        if self.direction not in VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {VALID_DIRECTIONS}; got {self.direction!r}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1]; got {self.confidence}")


def save(conn: sqlite3.Connection, signal: DirectionalSignal) -> None:
    """Upsert a signal (replaces on (ticker, asof_ts, rules_version) collision)."""
    conn.execute(
        "INSERT OR REPLACE INTO directional_signals "
        "(ticker, asof_ts, direction, confidence, horizon_days, rationale, "
        " rules_version, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            signal.ticker, signal.asof_ts, signal.direction, signal.confidence,
            signal.horizon_days, signal.rationale, signal.rules_version,
            int(time.time()),
        ),
    )
    conn.commit()


def latest_for(
    conn: sqlite3.Connection, ticker: str, rules_version: str | None = None,
) -> DirectionalSignal | None:
    """Return the most recent signal for `ticker`. If rules_version is set, scope to it."""
    if rules_version is None:
        row = conn.execute(
            "SELECT * FROM directional_signals WHERE ticker=? "
            "ORDER BY asof_ts DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM directional_signals WHERE ticker=? AND rules_version=? "
            "ORDER BY asof_ts DESC LIMIT 1",
            (ticker, rules_version),
        ).fetchone()
    if row is None:
        return None
    return DirectionalSignal(
        ticker=row["ticker"],
        asof_ts=int(row["asof_ts"]),
        direction=row["direction"],
        confidence=float(row["confidence"]),
        horizon_days=int(row["horizon_days"]),
        rationale=row["rationale"] or "",
        rules_version=row["rules_version"],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_signals.py -v`
Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/__init__.py bullbot/v2/signals.py tests/unit/test_v2_signals.py
git commit -m "feat(v2): DirectionalSignal dataclass + persistence

Schema-validated at construction (direction in {bullish,bearish,chop,no_edge},
confidence in [0,1]). save() upserts on the (ticker, asof_ts, rules_version)
unique key so the daily run is idempotent. latest_for() reads the most
recent row, optionally scoped to a rules_version (for A/B comparison)."
```

---

### Task 3: Rules-based underlying signal generator

**Files:**
- Create: `bullbot/v2/underlying.py`
- Test: `tests/unit/test_v2_underlying.py`

**Rules (all from daily bars only — no options data needed):**
- 50-day SMA above 200-day SMA AND price > 50-SMA → `bullish`.
- 50-SMA below 200-SMA AND price < 50-SMA → `bearish`.
- Otherwise → `chop`.
- If fewer than 200 bars available → `no_edge`.
- Confidence: distance from price to 50-SMA, normalized by 20-day ATR. Clamped to [0,1].
- Horizon: fixed at 30 days for Phase A (Phase B will tune).
- Rationale: short text describing which rule fired.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_v2_underlying.py
"""Unit tests for bullbot.v2.underlying."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from bullbot.v2 import underlying
from bullbot.v2.signals import DirectionalSignal


@dataclass
class _FakeBar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def _trending_up_bars(n: int = 250, start: float = 100.0) -> list[_FakeBar]:
    bars = []
    for i in range(n):
        c = start + i * 0.5
        bars.append(_FakeBar(ts=1_700_000_000 + i * 86400, open=c - 0.1, high=c + 0.5, low=c - 0.5, close=c, volume=1_000_000))
    return bars


def _trending_down_bars(n: int = 250, start: float = 200.0) -> list[_FakeBar]:
    bars = []
    for i in range(n):
        c = start - i * 0.5
        bars.append(_FakeBar(ts=1_700_000_000 + i * 86400, open=c + 0.1, high=c + 0.5, low=c - 0.5, close=c, volume=1_000_000))
    return bars


def _flat_bars(n: int = 250, price: float = 100.0) -> list[_FakeBar]:
    return [_FakeBar(ts=1_700_000_000 + i * 86400, open=price, high=price + 0.5, low=price - 0.5, close=price, volume=1_000_000) for i in range(n)]


def test_classify_returns_bullish_on_uptrend():
    sig = underlying.classify(ticker="AAPL", bars=_trending_up_bars(), asof_ts=1_700_000_000 + 250 * 86400)
    assert sig.direction == "bullish"
    assert sig.confidence > 0.0
    assert sig.rules_version


def test_classify_returns_bearish_on_downtrend():
    sig = underlying.classify(ticker="AAPL", bars=_trending_down_bars(), asof_ts=1_700_000_000 + 250 * 86400)
    assert sig.direction == "bearish"


def test_classify_returns_chop_on_flat():
    sig = underlying.classify(ticker="AAPL", bars=_flat_bars(), asof_ts=1_700_000_000 + 250 * 86400)
    assert sig.direction == "chop"


def test_classify_returns_no_edge_with_too_few_bars():
    short = _trending_up_bars(n=50)
    sig = underlying.classify(ticker="AAPL", bars=short, asof_ts=1_700_000_000 + 50 * 86400)
    assert sig.direction == "no_edge"
    assert sig.confidence == 0.0


def test_confidence_is_clamped_to_unit_interval():
    # Extreme spike — confidence must still be <= 1.0.
    bars = _flat_bars(n=240)
    bars.append(_FakeBar(ts=bars[-1].ts + 86400, open=100, high=10000, low=100, close=10000, volume=1_000_000))
    sig = underlying.classify(ticker="AAPL", bars=bars, asof_ts=bars[-1].ts)
    assert 0.0 <= sig.confidence <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_underlying.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bullbot.v2.underlying'`.

- [ ] **Step 3: Implement the generator**

Create `bullbot/v2/underlying.py`:
```python
"""Rules-based directional-signal generator (Phase A).

Pure function over daily bars. No options data, no LLM. Outputs a
DirectionalSignal that the runner persists to the directional_signals
table. Rules version is "v1" — bumping the version triggers a parallel
write so A/B comparison is possible without overwriting old signals.
"""
from __future__ import annotations

from typing import Protocol

from bullbot.v2.signals import DirectionalSignal

RULES_VERSION = "v1"
LOOKBACK_REQUIRED = 200
HORIZON_DAYS = 30


class _BarLike(Protocol):
    close: float
    high: float
    low: float


def _sma(values: list[float], window: int) -> float:
    if len(values) < window:
        return float("nan")
    return sum(values[-window:]) / window


def _atr(bars: list[_BarLike], window: int = 20) -> float:
    """Simple ATR: mean of (high-low) over the last `window` bars."""
    if len(bars) < window:
        return 0.0
    recent = bars[-window:]
    return sum(b.high - b.low for b in recent) / window


def classify(ticker: str, bars: list[_BarLike], asof_ts: int) -> DirectionalSignal:
    """Return a DirectionalSignal for `ticker` at `asof_ts` from `bars`."""
    if len(bars) < LOOKBACK_REQUIRED:
        return DirectionalSignal(
            ticker=ticker, asof_ts=asof_ts, direction="no_edge",
            confidence=0.0, horizon_days=HORIZON_DAYS,
            rationale=f"insufficient bars ({len(bars)} < {LOOKBACK_REQUIRED})",
            rules_version=RULES_VERSION,
        )
    closes = [b.close for b in bars]
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    spot = closes[-1]
    atr = _atr(bars, 20) or 1e-9

    distance = abs(spot - sma50) / atr
    confidence = min(max(distance / 3.0, 0.0), 1.0)

    if sma50 > sma200 and spot > sma50:
        return DirectionalSignal(
            ticker=ticker, asof_ts=asof_ts, direction="bullish",
            confidence=confidence, horizon_days=HORIZON_DAYS,
            rationale=f"50-SMA {sma50:.2f} > 200-SMA {sma200:.2f} AND spot {spot:.2f} > 50-SMA",
            rules_version=RULES_VERSION,
        )
    if sma50 < sma200 and spot < sma50:
        return DirectionalSignal(
            ticker=ticker, asof_ts=asof_ts, direction="bearish",
            confidence=confidence, horizon_days=HORIZON_DAYS,
            rationale=f"50-SMA {sma50:.2f} < 200-SMA {sma200:.2f} AND spot {spot:.2f} < 50-SMA",
            rules_version=RULES_VERSION,
        )
    return DirectionalSignal(
        ticker=ticker, asof_ts=asof_ts, direction="chop",
        confidence=confidence, horizon_days=HORIZON_DAYS,
        rationale=f"no clear trend (50-SMA {sma50:.2f}, 200-SMA {sma200:.2f}, spot {spot:.2f})",
        rules_version=RULES_VERSION,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_underlying.py -v`
Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/underlying.py tests/unit/test_v2_underlying.py
git commit -m "feat(v2): rules-based directional-signal generator

Phase A. Pure function over daily bars — no options data, no LLM.
Bullish if 50-SMA > 200-SMA AND spot > 50-SMA; bearish on the inverse;
chop otherwise; no_edge if fewer than 200 bars. Confidence is distance
from 50-SMA in ATR units, clamped to [0,1]. Rules version 'v1' so we
can A/B against future rule packs."
```

---

### Task 4: Daily runner — iterate UNIVERSE, build snapshot, persist signal

**Files:**
- Create: `bullbot/v2/runner.py`
- Test: `tests/integration/test_v2_runner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_v2_runner.py
"""Integration tests for bullbot.v2.runner."""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def conn(monkeypatch):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE bars (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            UNIQUE(ticker, timeframe, ts)
        );
        CREATE TABLE directional_signals (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            asof_ts INTEGER NOT NULL,
            direction TEXT NOT NULL,
            confidence REAL NOT NULL,
            horizon_days INTEGER NOT NULL,
            rationale TEXT,
            rules_version TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE (ticker, asof_ts, rules_version)
        );
    """)
    # Seed 250 bullish bars for AAPL.
    for i in range(250):
        c.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES ('AAPL', '1d', ?, ?, ?, ?, ?, ?)",
            (1_700_000_000 + i * 86400, 100 + i * 0.5, 100.5 + i * 0.5, 99.5 + i * 0.5, 100 + i * 0.5, 1_000_000),
        )
    # Seed 250 bearish bars for TSLA.
    for i in range(250):
        c.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES ('TSLA', '1d', ?, ?, ?, ?, ?, ?)",
            (1_700_000_000 + i * 86400, 200 - i * 0.5, 200.5 - i * 0.5, 199.5 - i * 0.5, 200 - i * 0.5, 1_000_000),
        )
    monkeypatch.setattr("bullbot.config.UNIVERSE", ["AAPL", "TSLA"])
    return c


def test_run_once_writes_signals_for_universe(conn):
    from bullbot.v2 import runner

    n = runner.run_once(conn, asof_ts=1_700_000_000 + 250 * 86400)
    assert n == 2  # one signal per UNIVERSE ticker
    rows = conn.execute("SELECT ticker, direction FROM directional_signals ORDER BY ticker").fetchall()
    assert [(r["ticker"], r["direction"]) for r in rows] == [
        ("AAPL", "bullish"), ("TSLA", "bearish"),
    ]


def test_run_once_is_idempotent(conn):
    from bullbot.v2 import runner
    runner.run_once(conn, asof_ts=1_700_000_000 + 250 * 86400)
    runner.run_once(conn, asof_ts=1_700_000_000 + 250 * 86400)
    n = conn.execute("SELECT COUNT(*) FROM directional_signals").fetchone()[0]
    assert n == 2  # not 4 — upserted, not duplicated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration/test_v2_runner.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the runner**

Create `bullbot/v2/runner.py`:
```python
"""v2 daily runner — iterate UNIVERSE and emit one DirectionalSignal per ticker."""
from __future__ import annotations

import logging
import sqlite3
import time

from bullbot import config
from bullbot.v2 import signals, underlying

log = logging.getLogger("bullbot.v2.runner")


def _load_bars(conn: sqlite3.Connection, ticker: str, asof_ts: int, limit: int = 400):
    """Load daily bars for `ticker` with ts <= asof_ts, oldest-first."""
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM bars "
        "WHERE ticker=? AND timeframe='1d' AND ts<=? "
        "ORDER BY ts DESC LIMIT ?",
        (ticker, asof_ts, limit),
    ).fetchall()

    # SimpleNamespace so the underlying classifier's _BarLike Protocol is satisfied.
    from types import SimpleNamespace
    bars = [
        SimpleNamespace(
            ts=r["ts"], open=r["open"], high=r["high"],
            low=r["low"], close=r["close"], volume=r["volume"],
        )
        for r in rows
    ]
    bars.reverse()  # oldest-first
    return bars


def run_once(conn: sqlite3.Connection, asof_ts: int | None = None) -> int:
    """Run one v2 daily pass over config.UNIVERSE. Returns the number of signals written."""
    if asof_ts is None:
        asof_ts = int(time.time())
    n = 0
    for ticker in config.UNIVERSE:
        try:
            bars = _load_bars(conn, ticker, asof_ts)
            sig = underlying.classify(ticker=ticker, bars=bars, asof_ts=asof_ts)
            signals.save(conn, sig)
            log.info("v2.runner: %s -> %s conf=%.2f", ticker, sig.direction, sig.confidence)
            n += 1
        except Exception:
            log.exception("v2.runner: %s failed", ticker)
    return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration/test_v2_runner.py -v`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/runner.py tests/integration/test_v2_runner.py
git commit -m "feat(v2): daily runner — emit DirectionalSignal per UNIVERSE ticker

Phase A entry point. Iterates config.UNIVERSE, loads recent bars, runs
the rules-based classifier, persists. Idempotent on (ticker, asof_ts,
rules_version). Failures on one ticker are logged but don't kill the
whole pass — the rest still get signals."
```

---

### Task 5: CLI subcommand + launchd wiring

**Files:**
- Create: `scripts/run_v2_daily.py`
- Modify: `bullbot/cli.py` — add `run-v2-daily` subcommand
- Test: extend `tests/integration/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_cli.py`:

```python
def test_run_v2_daily_calls_runner(db_conn, monkeypatch):
    """`run-v2-daily` must invoke v2.runner.run_once and return its count."""
    calls: list[int] = []

    def fake_run_once(conn, asof_ts=None):
        calls.append(1)
        return 7

    monkeypatch.setattr("bullbot.cli._open_db", lambda: db_conn)
    monkeypatch.setattr("bullbot.v2.runner.run_once", fake_run_once)

    rc = cli.main(["run-v2-daily"])
    assert rc == 0
    assert calls == [1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration/test_cli.py::test_run_v2_daily_calls_runner -v`
Expected: FAIL — subcommand not registered.

- [ ] **Step 3: Add the subcommand + script**

In `bullbot/cli.py`, find the argparse setup and add a new subparser:

```python
def cmd_run_v2_daily(args):
    """v2 daily entry point — emit DirectionalSignal per UNIVERSE ticker."""
    import logging
    from bullbot.v2 import runner

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("bullbot.cli.run_v2_daily")

    conn = _open_db()
    n = runner.run_once(conn)
    log.info("run-v2-daily: wrote %d signals", n)
    conn.commit()
    return 0
```

In the argparse setup (look for existing `subparsers.add_parser(...)` calls), register the new command:
```python
sp_v2 = subparsers.add_parser("run-v2-daily", help="Run v2 daily underlying-signal pass")
sp_v2.set_defaults(func=cmd_run_v2_daily)
```

Create `scripts/run_v2_daily.py`:
```python
"""Thin CLI wrapper for the v2 daily run — for launchd / cron invocation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bullbot import cli


if __name__ == "__main__":
    raise SystemExit(cli.main(["run-v2-daily"]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration/test_cli.py -v`
Expected: PASS for new test; all existing CLI tests still pass.

- [ ] **Step 5: Commit**

```bash
git add bullbot/cli.py scripts/run_v2_daily.py tests/integration/test_cli.py
git commit -m "feat(v2): wire run-v2-daily CLI subcommand + script

CLI subcommand and launchable script for the v2 daily pass. Launchd
plist update lives in deploy/ but isn't touched here — Task 7 covers
the live deployment."
```

---

### Task 6: Dashboard tile for v2 signals

**Files:**
- Modify: `bullbot/dashboard/tabs.py`
- Test: `tests/unit/test_dashboard_v2_signals.py` (new file)

The dashboard generator already builds tabs from sqlite queries. Add a new tab "V2 Signals" that displays today's directional signals in a table: ticker, direction, confidence, rationale, asof.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_dashboard_v2_signals.py
"""Unit tests for the V2 Signals dashboard tab."""
from __future__ import annotations

import sqlite3
import time

import pytest


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE directional_signals (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            asof_ts INTEGER NOT NULL,
            direction TEXT NOT NULL,
            confidence REAL NOT NULL,
            horizon_days INTEGER NOT NULL,
            rationale TEXT,
            rules_version TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE (ticker, asof_ts, rules_version)
        );
    """)
    now = int(time.time())
    c.execute(
        "INSERT INTO directional_signals "
        "(ticker, asof_ts, direction, confidence, horizon_days, rationale, rules_version, created_at) "
        "VALUES ('AAPL', ?, 'bullish', 0.65, 30, '50/200 cross', 'v1', ?)",
        (now, now),
    )
    c.execute(
        "INSERT INTO directional_signals "
        "(ticker, asof_ts, direction, confidence, horizon_days, rationale, rules_version, created_at) "
        "VALUES ('TSLA', ?, 'bearish', 0.40, 30, '50/200 inverse', 'v1', ?)",
        (now, now),
    )
    return c


def test_v2_signals_tab_renders_latest_signal_per_ticker(conn):
    from bullbot.dashboard import tabs
    html = tabs.render_v2_signals_tab(conn)
    assert "AAPL" in html
    assert "bullish" in html
    assert "TSLA" in html
    assert "bearish" in html
    assert "0.65" in html or "65" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_dashboard_v2_signals.py -v`
Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Add the tab renderer**

In `bullbot/dashboard/tabs.py`, find the existing tab functions (look for `def render_leaderboard_tab` or similar). Append:

```python
def render_v2_signals_tab(conn) -> str:
    """Render the v2 directional-signals tab — one row per ticker, latest signal only."""
    rows = conn.execute("""
        SELECT ds.*
        FROM directional_signals ds
        INNER JOIN (
            SELECT ticker, MAX(asof_ts) AS max_ts
            FROM directional_signals
            GROUP BY ticker
        ) latest ON ds.ticker = latest.ticker AND ds.asof_ts = latest.max_ts
        ORDER BY ds.ticker
    """).fetchall()

    if not rows:
        return "<div class='v2-signals'>No v2 signals yet — run-v2-daily hasn't fired.</div>"

    html = ["<table class='v2-signals'>"]
    html.append("<thead><tr><th>Ticker</th><th>Direction</th><th>Confidence</th><th>Horizon</th><th>Rationale</th><th>As of</th></tr></thead>")
    html.append("<tbody>")
    for r in rows:
        from datetime import datetime
        asof = datetime.utcfromtimestamp(r["asof_ts"]).strftime("%Y-%m-%d")
        direction_cls = f"dir-{r['direction']}"
        html.append(
            f"<tr><td>{r['ticker']}</td>"
            f"<td class='{direction_cls}'>{r['direction']}</td>"
            f"<td>{r['confidence']:.2f}</td>"
            f"<td>{r['horizon_days']}d</td>"
            f"<td>{r['rationale']}</td>"
            f"<td>{asof}</td></tr>"
        )
    html.append("</tbody></table>")
    return "\n".join(html)
```

Hook the new tab into the dashboard's tab list. Find where existing tabs are assembled (e.g., a `TABS = [...]` list or a function that builds the navigation) and add an entry for "V2 Signals" → `render_v2_signals_tab`. The exact pattern depends on the existing structure in tabs.py — match what other tabs do.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_dashboard_v2_signals.py tests/unit/test_dashboard_generator.py tests/unit/test_dashboard_queries.py -v`
Expected: PASS for new test; existing dashboard tests still pass.

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/tabs.py tests/unit/test_dashboard_v2_signals.py
git commit -m "feat(v2): dashboard tab for v2 directional signals

Shows latest signal per ticker — direction, confidence, rationale,
horizon, asof. The first place Dan will see v2 doing anything: open
the dashboard, see a 'V2 Signals' tab with the bot's current read on
each UNIVERSE ticker."
```

---

### Task 7: Ship to pasture + verify live

- [ ] **Step 1: Run full suite locally**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/ -q`
Expected: zero failures.

- [ ] **Step 2: Push branch + fast-forward main**

```bash
git push origin claude/elastic-lederberg-f95882
git push origin claude/elastic-lederberg-f95882:main
```

- [ ] **Step 3: Pull on pasture and run the migration**

```bash
ssh pasture "cd ~/Projects/bull-bot && git pull --ff-only origin main 2>&1 | tail -5"
ssh pasture "cd ~/Projects/bull-bot && .venv/bin/python -c 'import sqlite3; from bullbot.db import migrations; c = sqlite3.connect(\"cache/bullbot.db\"); migrations.apply_schema(c); c.commit(); cols=[r[1] for r in c.execute(\"PRAGMA table_info(directional_signals)\")]; print(\"directional_signals columns:\", cols)' 2>&1"
```

Expected: list including all 9 columns from Task 1.

- [ ] **Step 4: Run the first daily pass on pasture**

```bash
ssh pasture "cd ~/Projects/bull-bot && .venv/bin/python scripts/run_v2_daily.py 2>&1 | tail -30"
```

Expected output: one log line per UNIVERSE ticker (`v2.runner: TICKER -> direction conf=X.XX`), final line `run-v2-daily: wrote N signals` with N == len(UNIVERSE).

- [ ] **Step 5: Verify signals in DB**

```bash
ssh pasture "sqlite3 -header -column /Users/danielrunion/Projects/bull-bot/cache/bullbot.db \"SELECT ticker, direction, ROUND(confidence,2) c, horizon_days h, datetime(asof_ts,'unixepoch','localtime') t, rationale FROM directional_signals WHERE asof_ts > strftime('%s','now','-1 hour') ORDER BY ticker;\""
```

Expected: one row per UNIVERSE ticker with a sensible direction.

- [ ] **Step 6: Schedule the daily run via launchd**

Edit `deploy/com.bullbot.daily.plist` to add `scripts/run_v2_daily.py` to the existing daily program block (after the bar refresh). If a separate plist is preferred, copy the existing one as `com.bullbot.v2-daily.plist` and adjust the script path. Show Dan the diff before installing — this is a live launchd change.

Suggested commit-only step here (no live `launchctl load` yet): commit the plist update, leave the user to `launchctl unload && launchctl load` after reviewing.

```bash
git add deploy/com.bullbot.daily.plist
git commit -m "deploy(v2): add run-v2-daily to daily launchd job

Daily 07:30 EDT job now includes the v2 directional-signal pass after
the bar refresh. Apply with launchctl unload+load on pasture once
reviewed."
```

- [ ] **Step 7: Final sanity check**

Open dashboard, confirm the V2 Signals tab renders and shows non-empty rows.

```bash
open http://localhost:8080  # or whatever the pasture-served dashboard URL is
```

---

## What this gets Dan, in plain language

After 7 tasks:
- The bot wakes up every morning, reads each ticker's recent bars, and writes a one-line opinion to the database: "AAPL is bullish, confidence 65%, expected horizon 30 days because 50-day SMA is above 200-day and price is above 50-day."
- Dan opens the dashboard, sees a "V2 Signals" tab with one row per ticker. Like a weather forecast.
- No trades yet. No options. Just opinions. That's deliberate — Phase A's deliverable is *observable judgment*, not P&L.

**Phase B (next plan) — paper trading on signals.** When a bullish-confidence > 0.5 signal exists for AAPL and we don't already have a position, the bot synthetically buys shares (or skips if budget too low). Closes on opposite signal or stop. Logs the trade to a `v2_paper_trades` table. Dashboard adds a P&L column to V2 Signals.

**Phase C (next plan) — vehicle agent.** Replace "buy shares" with a deterministic table: given budget + direction + regime, pick among long call, call spread, LEAPS, shares, pass.

**Phase D (next plan) — LLM annotation.** Add a cheap Haiku call per ticker per day that *annotates* the rules-based signal: bumps confidence on news catalysts, knocks it down on regime ambiguity, generates a richer rationale.

---

## Self-Review

**Spec coverage:**
- Underlying agent: Tasks 1-4 (table, dataclass, classifier, runner).
- Dashboard visibility: Task 6.
- Live deployment: Task 7.
- Vehicle agent: explicitly deferred to Phase B/C.
- LLM annotation: explicitly deferred to Phase D.

**Placeholder scan:**
- No "TBD", no "TODO", no "implement later". Task 6 step 3's "look for existing pattern" is a real instruction the executor needs to follow since dashboard tabs.py varies by codebase — included a Read instruction. Task 5 step 3 says "look for existing subparsers.add_parser calls" — same justification.

**Type consistency:**
- `DirectionalSignal` fields and types match across Tasks 2, 3, 4, 6.
- `RULES_VERSION = "v1"` used consistently.
- `classify(ticker, bars, asof_ts)` signature unchanged across Tasks 3 and 4.
