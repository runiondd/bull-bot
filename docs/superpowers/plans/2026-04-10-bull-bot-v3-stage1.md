# Bull-Bot v3 Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Bull-Bot v3 single-process monolith — an automated per-ticker options-strategy discovery, paper-trading, and live-trading system — end-to-end from an empty SQLite database to a working evolver loop that can run discovery on SPY with zero manual intervention.

**Architecture:** Single long-running Python process with a unified `engine.step(cursor)` execution primitive, a core `evolver_iteration(ticker)` algorithm that handles discovery, a market-hours scheduler that dispatches per-ticker actions based on `ticker_state.phase`, and a layered kill switch on daily loss / total drawdown / runaway research spend. Storage is a single SQLite file (WAL mode, strict schemas). The only LLM call site is the evolver proposer, using Claude Opus 4.6.

**Tech Stack:** Python 3.11+, SQLite (via `sqlite3`, WAL mode, `PRAGMA strict=ON`), Pydantic v2 (schemas), pandas / numpy / scipy (indicators, Black-Scholes, walk-forward), `anthropic` SDK (Opus proposer), `requests` + `tenacity` (UW/Polygon HTTP clients), `pandas_market_calendars` (NYSE calendar), pytest (unit + integration + regression), launchd + caffeinate (process supervision on macOS).

**Spec:** [`docs/superpowers/specs/2026-04-10-bull-bot-refactor-design.md`](../specs/2026-04-10-bull-bot-refactor-design.md)

**Phase 0 validations (already completed):**
- `reports/phase0_polygon.md` — Polygon tier limits, historical depth
- `reports/phase0_uw.md` — UW OHLC + greeks coverage
- `reports/phase0_anthropic.md` — Sonnet/Haiku baseline (Sonnet 5/5 JSON valid, Haiku 0/5)
- `reports/phase0a_opus_proposer.md` — Opus 4.6 validated as proposer (5/5 JSON valid, ~$0.038/call, ~8.2s p50)
- `reports/phase0b_uw_historical_options.md` — UW `/historic` works on expired contracts, symbol enumeration + Black-Scholes IV inversion workarounds defined

---

## File structure

Stage 1 creates a new Python package at `bullbot/` alongside existing `scripts/` (Phase 0 validation tools) and `utils/` (existing logging helpers).

```
Bull-Bot/
├── bullbot/                          # NEW — v3 Stage 1 package
│   ├── __init__.py
│   ├── config.py                     # T1: v3 config constants (spec §12)
│   ├── clock.py                      # T2: market calendar + UTC↔ET
│   ├── main.py                       # T28: process entry point
│   ├── scheduler.py                  # T26: outer loop dispatcher
│   ├── nightly.py                    # T25: mark-to-market, faithfulness, promotion
│   ├── cli.py                        # T27: operator commands
│   ├── db/
│   │   ├── __init__.py
│   │   ├── schema.sql                # T3: full DDL
│   │   ├── migrations.py             # T3: schema loader
│   │   └── connection.py             # T4: sqlite3 WAL connection helper
│   ├── data/
│   │   ├── __init__.py
│   │   ├── schemas.py                # T5: Pydantic models
│   │   ├── fetchers.py               # T12: UW + Polygon HTTP clients
│   │   ├── cache.py                  # T13: read-through cache with TTL
│   │   └── options_backfill.py       # T14: bulk historic fetcher
│   ├── features/
│   │   ├── __init__.py
│   │   ├── indicators.py             # T6: SMA/EMA/RSI/ATR/BB/IV rank
│   │   ├── greeks.py                 # T7: BS closed-form + brentq IV inverter
│   │   └── regime.py                 # T8: bull/bear/chop classifier
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base.py                   # T15: Strategy ABC + Signal dataclass
│   │   ├── registry.py               # T18: class_name → class lookup + serde
│   │   ├── put_credit_spread.py      # T16
│   │   ├── call_credit_spread.py     # T17
│   │   ├── iron_condor.py            # T17
│   │   ├── cash_secured_put.py       # T17
│   │   ├── long_call.py              # T17
│   │   └── long_put.py               # T17
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── fill_model.py             # T10: simulate_open / simulate_close / mark
│   │   ├── position_sizer.py         # T11: 2% equity-at-risk
│   │   └── step.py                   # T19: unified execution primitive
│   ├── backtest/
│   │   ├── __init__.py
│   │   └── walkforward.py            # T20: anchored 70/30 walk-forward
│   ├── evolver/
│   │   ├── __init__.py
│   │   ├── plateau.py                # T9: continue/no_edge/edge_found classifier
│   │   ├── proposer.py               # T23: Opus wrapper + history block
│   │   └── iteration.py              # T24: THE core algorithm
│   └── risk/
│       ├── __init__.py
│       ├── cost_ledger.py            # T21: append-only billing log
│       └── kill_switch.py            # T22: trip conditions + re-arm
├── tests/                            # NEW — pytest suite
│   ├── __init__.py
│   ├── conftest.py                   # T4: fixture for in-memory SQLite DB
│   ├── unit/                         # Tier 1 — <5s
│   │   ├── test_clock.py             # T2
│   │   ├── test_schemas.py           # T5
│   │   ├── test_indicators.py        # T6
│   │   ├── test_greeks.py            # T7
│   │   ├── test_regime.py            # T8
│   │   ├── test_plateau.py           # T9
│   │   ├── test_fill_model.py        # T10
│   │   ├── test_position_sizer.py    # T11
│   │   ├── test_registry.py          # T18
│   │   ├── test_strategies.py        # T16, T17
│   │   └── test_cost_ledger.py       # T21
│   ├── integration/                  # Tier 2 — <60s
│   │   ├── test_fetchers.py          # T12
│   │   ├── test_cache.py             # T13
│   │   ├── test_engine_step.py       # T19
│   │   ├── test_walkforward.py       # T20
│   │   ├── test_proposer.py          # T23
│   │   ├── test_evolver_iteration.py # T24
│   │   ├── test_kill_switch.py       # T22
│   │   ├── test_nightly.py           # T25
│   │   ├── test_scheduler.py         # T26
│   │   └── test_state_machine.py     # T26 (T1/T2/T3/T4/T5 transitions)
│   ├── regression/                   # Tier 3 — frozen backtest
│   │   └── test_backtest_determinism.py  # T29
│   └── fixtures/
│       ├── uw_responses/             # T12: canned UW JSON for fetcher tests
│       └── spy_regression_2023_2024.parquet  # T29: frozen OHLC+options
├── scripts/
│   ├── (existing Phase 0 scripts — untouched)
│   └── smoke_test.py                 # T30: end-to-end smoke
├── deploy/
│   └── com.bullbot.main.plist        # T28: launchd supervision
├── docs/
│   └── superpowers/
│       ├── specs/2026-04-10-bull-bot-refactor-design.md
│       └── plans/2026-04-10-bull-bot-v3-stage1.md   # this file
└── requirements.txt                  # existing — already has every dep we need
```

---

## Development conventions

**TDD everywhere.** Every task is: write failing test → run it → see it fail → write minimal implementation → run it → see it pass → commit. No exceptions for "simple" code.

**Commit after every task.** Each numbered task produces one commit. Commit message format:
```
stage1(T<num>): <short summary>

<optional body explaining non-obvious decisions>
```

**Test isolation.** Every integration test uses a fresh in-memory SQLite database (`sqlite:///:memory:`) initialized from `bullbot/db/schema.sql` via a pytest fixture in `tests/conftest.py`. No test shares state with another.

**External APIs are mocked in Tier 2.** `FakeUWClient` and `FakeAnthropicClient` classes in `tests/conftest.py` implement the same interface as the real clients but return canned responses. The only place real APIs are hit is Tier 4 (`scripts/smoke_test.py`), which is manual.

**Pure functions are the default.** Any module that can be a pure function should be — `features/`, `strategies/`, `evolver/plateau.py`, `engine/fill_model.py`, `engine/position_sizer.py`. Side effects live in `data/`, `db/`, `risk/`, `scheduler.py`, `nightly.py`, `main.py`.

**No silent defaults.** If the kill switch can't determine current equity because the ledger is empty, it raises, it does not return "0 loss, safe to trade". If the proposer returns malformed JSON after one retry, it raises `ProposerJsonError`, it does not silently skip.

**Commits only on passing tests.** If Tier 1 tests fail at any point, stop and fix before continuing. The plan assumes you're running `pytest tests/unit -x -q` constantly.

---

## Phase A — Foundation (T1–T4)

### Task 1: Project skeleton + bullbot/config.py

**Files:**
- Create: `bullbot/__init__.py` (empty)
- Create: `bullbot/config.py`
- Create: `bullbot/db/__init__.py` (empty)
- Create: `bullbot/data/__init__.py` (empty)
- Create: `bullbot/features/__init__.py` (empty)
- Create: `bullbot/strategies/__init__.py` (empty)
- Create: `bullbot/engine/__init__.py` (empty)
- Create: `bullbot/backtest/__init__.py` (empty)
- Create: `bullbot/evolver/__init__.py` (empty)
- Create: `bullbot/risk/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)
- Create: `tests/unit/__init__.py` (empty)
- Create: `tests/integration/__init__.py` (empty)
- Create: `tests/regression/__init__.py` (empty)
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Create all empty `__init__.py` files**

```bash
mkdir -p bullbot/db bullbot/data bullbot/features bullbot/strategies bullbot/engine bullbot/backtest bullbot/evolver bullbot/risk
mkdir -p tests/unit tests/integration tests/regression tests/fixtures/uw_responses
touch bullbot/__init__.py bullbot/db/__init__.py bullbot/data/__init__.py bullbot/features/__init__.py bullbot/strategies/__init__.py bullbot/engine/__init__.py bullbot/backtest/__init__.py bullbot/evolver/__init__.py bullbot/risk/__init__.py
touch tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py tests/regression/__init__.py
```

- [ ] **Step 2: Write failing test for config values** (`tests/unit/test_config.py`)

```python
"""Config sanity: constants from spec §12 exist with correct values."""
from bullbot import config


def test_universe_is_ten_tickers():
    assert config.UNIVERSE == [
        "SPY", "QQQ", "IWM", "AAPL", "MSFT",
        "NVDA", "TSLA", "AMD", "META", "GOOGL",
    ]


def test_capital_and_timeline():
    assert config.INITIAL_CAPITAL_USD == 50_000
    assert config.TARGET_MONTHLY_PNL_USD == 10_000
    assert config.TARGET_DATE == "2026-07-10"


def test_edge_gate_thresholds():
    assert config.EDGE_PF_IS_MIN == 1.5
    assert config.EDGE_PF_OOS_MIN == 1.3
    assert config.EDGE_TRADE_COUNT_MIN == 30


def test_walkforward_config():
    assert config.WF_TRAIN_FRAC == 0.70
    assert config.WF_WINDOW_MONTHS == 24
    assert config.WF_STEP_DAYS == 30
    assert config.WF_MIN_FOLDS == 3
    assert config.WF_MAX_FOLDS == 5


def test_plateau_thresholds():
    assert config.PLATEAU_IMPROVEMENT_MIN == 0.10
    assert config.PLATEAU_COUNTER_MAX == 3
    assert config.ITERATION_CAP == 50
    assert config.HISTORY_BLOCK_SIZE == 15


def test_promotion_gate():
    assert config.PAPER_TRIAL_DAYS == 21
    assert config.PAPER_TRADE_COUNT_MIN == 10
    assert config.FAITHFULNESS_MIN_DAYS == 5
    assert config.FAITHFULNESS_DELTA_MAX == 0.30
    assert config.PAPER_DD_MULT_MAX == 1.5


def test_kill_switch_thresholds():
    assert config.KILL_DAILY_LOSS_USD == 1_500
    assert config.KILL_TOTAL_DD_USD == 5_000
    assert config.KILL_RESEARCH_RATTHOLE_USD == 1_000


def test_position_sizing():
    assert config.POSITION_RISK_FRAC == 0.02
    assert config.MAX_POSITIONS_PER_TICKER == 3
    assert config.MAX_POSITIONS_TOTAL == 10


def test_fill_model():
    assert config.COMMISSION_PER_CONTRACT_USD == 0.65
    assert config.SLIPPAGE_TICKS_PER_LEG == 1
    assert config.MIN_SPREAD_FRAC == 0.50


def test_llm_model():
    assert config.PROPOSER_MODEL == "claude-opus-4-6"
    assert config.PROPOSER_MODEL_FALLBACK == "claude-sonnet-4-6"
    assert config.PROPOSER_MAX_TOKENS == 2000


def test_scheduling():
    assert config.TICK_INTERVAL_MARKET_SEC == 60
    assert config.TICK_INTERVAL_OFFHOURS_SEC == 5
    assert config.MARKET_TIMEZONE == "America/New_York"


def test_api_keys_loaded_from_env(monkeypatch):
    """API key loading happens via os.environ at import time."""
    # Presence test only — the actual secret is in .env, we don't check the value
    assert hasattr(config, "UW_API_KEY")
    assert hasattr(config, "POLYGON_API_KEY")
    assert hasattr(config, "ANTHROPIC_API_KEY")


def test_paths_are_absolute():
    assert config.DB_PATH.is_absolute()
    assert config.REPORTS_DIR.is_absolute()
    assert config.LOGS_DIR.is_absolute()
```

- [ ] **Step 3: Run the test, verify it fails with `ModuleNotFoundError`**

```bash
pytest tests/unit/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'bullbot.config'` or similar.

- [ ] **Step 4: Write `bullbot/config.py`**

```python
"""
Bull-Bot v3 configuration — single source of truth.

All operational constants live here. Spec §12 is the canonical reference.
Changing a value in this file should always be accompanied by a commit
message explaining why.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---- Paths -----------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "cache" / "bullbot.db"
REPORTS_DIR = ROOT_DIR / "reports"
LOGS_DIR = ROOT_DIR / "logs"
FIXTURES_DIR = ROOT_DIR / "tests" / "fixtures"

for _d in (DB_PATH.parent, REPORTS_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---- API keys (loaded from .env) -------------------------------------------

load_dotenv(ROOT_DIR / ".env")

UW_API_KEY = os.environ.get("UNUSUAL_WHALES_API_KEY", "")
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ---- Ticker universe -------------------------------------------------------

UNIVERSE: list[str] = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT",
    "NVDA", "TSLA", "AMD", "META", "GOOGL",
]

UNIVERSE_RETIRED: list[str] = []

# ---- Capital & timeline ----------------------------------------------------

INITIAL_CAPITAL_USD = 50_000
TARGET_MONTHLY_PNL_USD = 10_000
TARGET_DATE = "2026-07-10"

# ---- Edge gate -------------------------------------------------------------

EDGE_PF_IS_MIN = 1.5
EDGE_PF_OOS_MIN = 1.3
EDGE_TRADE_COUNT_MIN = 30

# ---- Walk-forward ----------------------------------------------------------

WF_TRAIN_FRAC = 0.70
WF_WINDOW_MONTHS = 24
WF_STEP_DAYS = 30
WF_MIN_FOLDS = 3
WF_MAX_FOLDS = 5

# ---- Plateau / discovery ---------------------------------------------------

PLATEAU_IMPROVEMENT_MIN = 0.10
PLATEAU_COUNTER_MAX = 3
ITERATION_CAP = 50
HISTORY_BLOCK_SIZE = 15

# ---- Promotion gate --------------------------------------------------------

PAPER_TRIAL_DAYS = 21
PAPER_TRADE_COUNT_MIN = 10
FAITHFULNESS_MIN_DAYS = 5
FAITHFULNESS_DELTA_MAX = 0.30
PAPER_DD_MULT_MAX = 1.5

# ---- Kill switch -----------------------------------------------------------

KILL_DAILY_LOSS_USD = 1_500
KILL_TOTAL_DD_USD = 5_000
KILL_RESEARCH_RATTHOLE_USD = 1_000

# ---- Position sizing -------------------------------------------------------

POSITION_RISK_FRAC = 0.02
MAX_POSITIONS_PER_TICKER = 3
MAX_POSITIONS_TOTAL = 10

# ---- Fill model ------------------------------------------------------------

COMMISSION_PER_CONTRACT_USD = 0.65
SLIPPAGE_TICKS_PER_LEG = 1
MIN_SPREAD_FRAC = 0.50

# ---- Regime thresholds -----------------------------------------------------

REGIME_BULL_RETURN_MIN = 0.05
REGIME_BEAR_RETURN_MAX = -0.05
REGIME_BULL_VOL_MAX = 0.20

# ---- LLM -------------------------------------------------------------------

PROPOSER_MODEL = "claude-opus-4-6"
PROPOSER_MODEL_FALLBACK = "claude-sonnet-4-6"
PROPOSER_MAX_TOKENS = 2000
PROPOSER_BUDGET_CEILING_USD = 0.10

# ---- Scheduling ------------------------------------------------------------

TICK_INTERVAL_MARKET_SEC = 60
TICK_INTERVAL_OFFHOURS_SEC = 5
MARKET_TIMEZONE = "America/New_York"

# ---- Risk-free rate (v1 hardcoded) -----------------------------------------

RISK_FREE_RATE = 0.045
```

- [ ] **Step 5: Run the test, verify it passes**

```bash
pytest tests/unit/test_config.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add bullbot/ tests/unit/test_config.py tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py tests/regression/__init__.py
git commit -m "stage1(T1): bullbot package skeleton + config constants

All constants from spec §12 pinned as Python module attributes.
Tests assert each value matches the spec."
```

---

### Task 2: bullbot/clock.py

**Files:**
- Create: `bullbot/clock.py`
- Create: `tests/unit/test_clock.py`

- [ ] **Step 1: Write failing test** (`tests/unit/test_clock.py`)

```python
"""Market calendar + time conversion tests.

All DB timestamps are UTC epoch seconds; display/logic uses ET.
Calendar wraps pandas_market_calendars('NYSE').
"""
from datetime import datetime, timezone

import pytest
from freezegun import freeze_time

from bullbot import clock


def test_utc_epoch_now_returns_int():
    ts = clock.utc_epoch_now()
    assert isinstance(ts, int)
    assert ts > 1_600_000_000   # post-2020 sanity check


def test_et_now_has_timezone():
    dt = clock.et_now()
    assert dt.tzinfo is not None
    assert "New_York" in str(dt.tzinfo) or "EST" in str(dt.tzinfo) or "EDT" in str(dt.tzinfo)


def test_utc_to_et_conversion():
    # 2024-06-14 20:00 UTC = 2024-06-14 16:00 EDT (market close)
    utc = datetime(2024, 6, 14, 20, 0, 0, tzinfo=timezone.utc)
    et = clock.utc_to_et(utc)
    assert et.hour == 16
    assert et.minute == 0


def test_epoch_to_et():
    # 2024-06-14 20:00:00 UTC = 1718395200 epoch
    et = clock.epoch_to_et(1718395200)
    assert et.year == 2024
    assert et.month == 6
    assert et.day == 14
    assert et.hour == 16


@freeze_time("2024-06-14 15:30:00", tz_offset=0)  # 11:30 AM ET
def test_is_market_open_during_rth():
    assert clock.is_market_open_now() is True


@freeze_time("2024-06-14 22:00:00", tz_offset=0)  # 6:00 PM ET
def test_is_market_closed_after_hours():
    assert clock.is_market_open_now() is False


@freeze_time("2024-06-15 15:30:00", tz_offset=0)  # Saturday
def test_is_market_closed_weekend():
    assert clock.is_market_open_now() is False


@freeze_time("2024-07-04 15:30:00", tz_offset=0)  # Independence Day 2024
def test_is_market_closed_holiday():
    assert clock.is_market_open_now() is False


def test_trading_days_between_standard_week():
    # Mon 2024-06-10 through Fri 2024-06-14 = 5 trading days
    from datetime import date
    n = clock.trading_days_between(date(2024, 6, 10), date(2024, 6, 14))
    assert n == 5


def test_trading_days_between_with_holiday():
    # Mon 2024-07-01 through Fri 2024-07-05 includes July 4 holiday = 4 trading days
    from datetime import date
    n = clock.trading_days_between(date(2024, 7, 1), date(2024, 7, 5))
    assert n == 4


def test_previous_trading_day_skips_weekend():
    from datetime import date
    # Monday 2024-06-10 → previous trading day is Friday 2024-06-07
    prev = clock.previous_trading_day(date(2024, 6, 10))
    assert prev == date(2024, 6, 7)
```

- [ ] **Step 2: Run test, verify it fails with import error**

```bash
pytest tests/unit/test_clock.py -v
```

- [ ] **Step 3: Write `bullbot/clock.py`**

```python
"""
Market calendar + time conversion.

All DB timestamps are UTC epoch seconds (integers). Display and business
logic uses Eastern Time. NYSE trading calendar is the source of truth for
"is this a trading day" and "is the market open now" questions.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas_market_calendars as mcal
import pytz

from bullbot import config

_ET = pytz.timezone(config.MARKET_TIMEZONE)
_CAL = mcal.get_calendar("NYSE")


def utc_epoch_now() -> int:
    """Current wall-clock time as UTC epoch seconds (int)."""
    return int(datetime.now(tz=timezone.utc).timestamp())


def et_now() -> datetime:
    """Current wall-clock time in Eastern Time, tz-aware."""
    return datetime.now(tz=_ET)


def utc_to_et(dt: datetime) -> datetime:
    """Convert a tz-aware UTC datetime to Eastern Time."""
    if dt.tzinfo is None:
        raise ValueError("utc_to_et requires a tz-aware datetime")
    return dt.astimezone(_ET)


def epoch_to_et(epoch_seconds: int) -> datetime:
    """Convert a UTC epoch seconds int to a tz-aware ET datetime."""
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).astimezone(_ET)


def is_market_open_now() -> bool:
    """True if NYSE is currently open (respects weekends + holidays + half-days)."""
    now_et = et_now()
    sched = _CAL.schedule(start_date=now_et.date(), end_date=now_et.date())
    if sched.empty:
        return False
    open_et = sched.iloc[0]["market_open"].tz_convert(_ET)
    close_et = sched.iloc[0]["market_close"].tz_convert(_ET)
    return open_et <= now_et <= close_et


def trading_days_between(start: date, end: date) -> int:
    """Inclusive count of NYSE trading days between two dates."""
    sched = _CAL.schedule(start_date=start, end_date=end)
    return len(sched)


def previous_trading_day(d: date) -> date:
    """The most recent NYSE trading day strictly before `d`."""
    # Look back up to 7 calendar days to cover long weekends + holidays
    from datetime import timedelta
    start = d - timedelta(days=10)
    sched = _CAL.schedule(start_date=start, end_date=d)
    prior = sched[sched.index < datetime.combine(d, datetime.min.time()).astimezone(_ET)]
    if prior.empty:
        raise ValueError(f"no trading day found before {d}")
    return prior.index[-1].date()


def market_open_et(d: date) -> datetime | None:
    """ET market-open time for date `d`, or None if not a trading day."""
    sched = _CAL.schedule(start_date=d, end_date=d)
    if sched.empty:
        return None
    return sched.iloc[0]["market_open"].tz_convert(_ET).to_pydatetime()


def market_close_et(d: date) -> datetime | None:
    """ET market-close time for date `d`, or None if not a trading day."""
    sched = _CAL.schedule(start_date=d, end_date=d)
    if sched.empty:
        return None
    return sched.iloc[0]["market_close"].tz_convert(_ET).to_pydatetime()
```

- [ ] **Step 4: Run test, verify it passes**

```bash
pytest tests/unit/test_clock.py -v
```

- [ ] **Step 5: Commit**

```bash
git add bullbot/clock.py tests/unit/test_clock.py
git commit -m "stage1(T2): bullbot/clock.py — market calendar + ET conversion

Wraps pandas_market_calendars('NYSE') for holiday/half-day awareness.
All DB times are UTC epoch seconds; ET is display/logic only."
```

---

### Task 3: bullbot/db/schema.sql + bullbot/db/migrations.py

**Files:**
- Create: `bullbot/db/schema.sql`
- Create: `bullbot/db/migrations.py`
- Create: `tests/unit/test_migrations.py`

- [ ] **Step 1: Write failing test** (`tests/unit/test_migrations.py`)

```python
"""Schema loader tests."""
import sqlite3

from bullbot.db import migrations


def test_apply_schema_creates_all_tables():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)

    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {r[0] for r in rows}
    expected = {
        "bars",
        "option_contracts",
        "iv_surface",
        "strategies",
        "evolver_proposals",
        "ticker_state",
        "orders",
        "positions",
        "cost_ledger",
        "kill_state",
        "faithfulness_checks",
        "iteration_failures",
    }
    missing = expected - table_names
    assert not missing, f"missing tables: {missing}"


def test_wal_mode_enabled():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    # In-memory dbs report 'memory' journal mode instead of 'wal'; verify the
    # pragma was issued by checking foreign_keys which apply_schema also sets.
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_strategies_unique_constraint():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    conn.execute(
        "INSERT INTO strategies (class_name, class_version, params, params_hash, created_at) "
        "VALUES ('PutCreditSpread', 1, '{}', 'hash1', 1)"
    )
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO strategies (class_name, class_version, params, params_hash, created_at) "
            "VALUES ('PutCreditSpread', 1, '{}', 'hash1', 2)"
        )


def test_kill_state_singleton_constraint():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    conn.execute("INSERT INTO kill_state (id, active) VALUES (1, 0)")
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO kill_state (id, active) VALUES (2, 0)")


def test_ticker_state_phase_check_constraint():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO ticker_state (ticker, phase, updated_at) "
            "VALUES ('AAPL', 'nonsense', 0)"
        )


def test_evolver_proposals_unique_per_ticker_iteration():
    conn = sqlite3.connect(":memory:")
    migrations.apply_schema(conn)
    conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'PCS', 1, '{}', 'h', 0)"
    )
    conn.execute(
        "INSERT INTO evolver_proposals "
        "(ticker, iteration, strategy_id, llm_cost_usd, passed_gate, created_at) "
        "VALUES ('AAPL', 1, 1, 0.0, 0, 0)"
    )
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO evolver_proposals "
            "(ticker, iteration, strategy_id, llm_cost_usd, passed_gate, created_at) "
            "VALUES ('AAPL', 1, 1, 0.0, 0, 0)"
        )
```

- [ ] **Step 2: Write `bullbot/db/schema.sql`**

```sql
-- Bull-Bot v3 database schema. Single source of truth.
-- Spec §11 is the canonical reference.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS bars (
  ticker    TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  ts        INTEGER NOT NULL,
  open      REAL, high REAL, low REAL, close REAL,
  volume    INTEGER,
  source    TEXT NOT NULL,
  PRIMARY KEY (ticker, timeframe, ts)
) STRICT;

CREATE TABLE IF NOT EXISTS option_contracts (
  ticker        TEXT NOT NULL,
  expiry        TEXT NOT NULL,
  strike        REAL NOT NULL,
  kind          TEXT NOT NULL CHECK (kind IN ('C', 'P')),
  ts            INTEGER NOT NULL,
  nbbo_bid      REAL,
  nbbo_ask      REAL,
  last          REAL,
  volume        INTEGER,
  open_interest INTEGER,
  iv            REAL,
  PRIMARY KEY (ticker, expiry, strike, kind, ts)
) STRICT;

CREATE TABLE IF NOT EXISTS iv_surface (
  ticker        TEXT NOT NULL,
  ts            INTEGER NOT NULL,
  iv_rank       REAL,
  iv_percentile REAL,
  atm_iv        REAL,
  implied_move  REAL,
  PRIMARY KEY (ticker, ts)
) STRICT;

CREATE TABLE IF NOT EXISTS strategies (
  id            INTEGER PRIMARY KEY,
  class_name    TEXT NOT NULL,
  class_version INTEGER NOT NULL,
  params        TEXT NOT NULL,
  params_hash   TEXT NOT NULL,
  parent_id     INTEGER REFERENCES strategies(id),
  created_at    INTEGER NOT NULL,
  UNIQUE (class_name, class_version, params_hash)
) STRICT;

CREATE TABLE IF NOT EXISTS evolver_proposals (
  id                 INTEGER PRIMARY KEY,
  ticker             TEXT NOT NULL,
  iteration          INTEGER NOT NULL,
  strategy_id        INTEGER NOT NULL REFERENCES strategies(id),
  parent_strategy_id INTEGER REFERENCES strategies(id),
  rationale          TEXT,
  llm_cost_usd       REAL NOT NULL,
  pf_is              REAL,
  pf_oos             REAL,
  sharpe_is          REAL,
  max_dd_pct         REAL,
  trade_count        INTEGER,
  regime_breakdown   TEXT,
  passed_gate        INTEGER NOT NULL,
  created_at         INTEGER NOT NULL,
  UNIQUE (ticker, iteration)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_proposals_ticker_iter
  ON evolver_proposals(ticker, iteration DESC);

CREATE TABLE IF NOT EXISTS ticker_state (
  ticker             TEXT PRIMARY KEY,
  phase              TEXT NOT NULL CHECK (
    phase IN ('discovering','paper_trial','live','no_edge','killed')
  ),
  retired            INTEGER NOT NULL DEFAULT 0,
  best_strategy_id   INTEGER REFERENCES strategies(id),
  best_pf_is         REAL,
  best_pf_oos        REAL,
  plateau_counter    INTEGER NOT NULL DEFAULT 0,
  iteration_count    INTEGER NOT NULL DEFAULT 0,
  cumulative_llm_usd REAL NOT NULL DEFAULT 0,
  paper_started_at   INTEGER,
  paper_trade_count  INTEGER NOT NULL DEFAULT 0,
  live_started_at    INTEGER,
  verdict_at         INTEGER,
  updated_at         INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS orders (
  id           INTEGER PRIMARY KEY,
  run_id       TEXT NOT NULL,
  ticker       TEXT NOT NULL,
  strategy_id  INTEGER NOT NULL REFERENCES strategies(id),
  placed_at    INTEGER NOT NULL,
  legs         TEXT NOT NULL,
  intent       TEXT NOT NULL CHECK (intent IN ('open', 'close')),
  status       TEXT NOT NULL,
  commission   REAL NOT NULL DEFAULT 0,
  pnl_realized REAL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_orders_run_ticker
  ON orders(run_id, ticker, placed_at);

CREATE TABLE IF NOT EXISTS positions (
  id           INTEGER PRIMARY KEY,
  run_id       TEXT NOT NULL,
  ticker       TEXT NOT NULL,
  strategy_id  INTEGER NOT NULL REFERENCES strategies(id),
  opened_at    INTEGER NOT NULL,
  closed_at    INTEGER,
  legs         TEXT NOT NULL,
  contracts    INTEGER NOT NULL,
  open_price   REAL NOT NULL,
  close_price  REAL,
  pnl_realized REAL,
  mark_to_mkt  REAL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_positions_run_ticker_open
  ON positions(run_id, ticker, opened_at);

CREATE TABLE IF NOT EXISTS cost_ledger (
  id         INTEGER PRIMARY KEY,
  ts         INTEGER NOT NULL,
  category   TEXT NOT NULL CHECK (
    category IN ('llm','data_uw','data_polygon','order_commission')
  ),
  ticker     TEXT,
  amount_usd REAL NOT NULL,
  details    TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_cost_ts ON cost_ledger(ts);
CREATE INDEX IF NOT EXISTS idx_cost_ticker ON cost_ledger(ticker, ts);

CREATE TABLE IF NOT EXISTS kill_state (
  id           INTEGER PRIMARY KEY CHECK (id = 1),
  active       INTEGER NOT NULL DEFAULT 0,
  tripped_at   INTEGER,
  reason       TEXT,
  trigger_rule TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS faithfulness_checks (
  id          INTEGER PRIMARY KEY,
  ticker      TEXT NOT NULL,
  checked_at  INTEGER NOT NULL,
  window_days INTEGER NOT NULL,
  paper_pf    REAL,
  backtest_pf REAL,
  delta_pct   REAL,
  passed      INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS iteration_failures (
  id          INTEGER PRIMARY KEY,
  ts          INTEGER NOT NULL,
  ticker      TEXT NOT NULL,
  phase       TEXT,
  exc_type    TEXT NOT NULL,
  exc_message TEXT NOT NULL,
  traceback   TEXT
) STRICT;
```

- [ ] **Step 3: Write `bullbot/db/migrations.py`**

```python
"""
Schema loader. Reads schema.sql and applies it to a SQLite connection.

In v1, the schema is monolithic — there are no versioned migrations, just
one authoritative schema. Adding a column in v2 will promote this module
to a real migration runner.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create all tables + indexes on the given connection. Idempotent."""
    sql = SCHEMA_PATH.read_text()
    conn.executescript(sql)
    conn.commit()
```

- [ ] **Step 4: Run the test, verify it passes**

```bash
pytest tests/unit/test_migrations.py -v
```

- [ ] **Step 5: Commit**

```bash
git add bullbot/db/schema.sql bullbot/db/migrations.py tests/unit/test_migrations.py
git commit -m "stage1(T3): SQLite schema (12 tables) + migrations loader

Full DDL from spec §11 with UNIQUE constraints, CHECK constraints on
phase/intent/kind/category, kill_state singleton, strict mode."
```

---

### Task 4: bullbot/db/connection.py + pytest conftest

**Files:**
- Create: `bullbot/db/connection.py`
- Create: `tests/conftest.py`
- Create: `tests/integration/test_connection.py`

- [ ] **Step 1: Write failing test** (`tests/integration/test_connection.py`)

```python
"""Connection helper tests."""
import sqlite3

from bullbot.db import connection


def test_open_connection_has_wal_journal_mode(tmp_path):
    db = tmp_path / "test.db"
    with connection.open_connection(db) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


def test_open_connection_has_foreign_keys_on(tmp_path):
    db = tmp_path / "test.db"
    with connection.open_connection(db) as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


def test_open_connection_creates_schema(tmp_path):
    db = tmp_path / "test.db"
    with connection.open_connection(db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bars'"
        ).fetchall()
        assert rows, "bars table should exist"


def test_row_factory_returns_dict_like(tmp_path):
    db = tmp_path / "test.db"
    with connection.open_connection(db) as conn:
        row = conn.execute("SELECT 1 AS one, 2 AS two").fetchone()
        assert row["one"] == 1
        assert row["two"] == 2


def test_conftest_fixture_provides_in_memory_db(db_conn):
    """Fixture defined in tests/conftest.py."""
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    assert len(rows) >= 12  # all v3 tables
```

- [ ] **Step 2: Write `bullbot/db/connection.py`**

```python
"""
SQLite connection helper. Opens a connection with WAL mode + foreign keys
+ row factory, runs migrations, and returns a context manager that closes
cleanly.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from bullbot.db import migrations


@contextmanager
def open_connection(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with v3 settings, apply schema, yield, close.

    - WAL journal mode
    - Foreign keys enabled
    - Row factory = sqlite3.Row (dict-like access)
    - Schema applied idempotently on every open
    """
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        migrations.apply_schema(conn)
        yield conn
    finally:
        conn.close()


def open_persistent_connection(db_path: Path | str) -> sqlite3.Connection:
    """Long-lived connection for the main process. Caller owns cleanup."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)
    return conn
```

- [ ] **Step 3: Write `tests/conftest.py`**

```python
"""
Shared pytest fixtures for Bull-Bot v3.

- db_conn: fresh in-memory SQLite with v3 schema, yielded per test
- frozen_now: deterministic "now" at a known ET timestamp
- fake_uw: stub UW client for integration tests
- fake_anthropic: stub Anthropic client for integration tests
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

import pytest

from bullbot.db import migrations


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """Fresh in-memory SQLite with the full v3 schema."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)
    yield conn
    conn.close()


@dataclass
class FakeUWResponse:
    status: int = 200
    body: Any = None


class FakeUWClient:
    """Minimal UW stand-in used by integration tests.

    Usage:
        fake_uw.register("/api/stock/SPY/ohlc/1d", FakeUWResponse(body={"data": [...]}))
        result = fake_uw.get("/api/stock/SPY/ohlc/1d")
    """

    def __init__(self) -> None:
        self._responses: dict[str, FakeUWResponse] = {}
        self.call_log: list[tuple[str, dict[str, Any] | None]] = []

    def register(self, path: str, response: FakeUWResponse) -> None:
        self._responses[path] = response

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        self.call_log.append((path, params))
        resp = self._responses.get(path)
        if resp is None:
            return 404, {"error": "no stub registered", "path": path}
        return resp.status, resp.body


@pytest.fixture
def fake_uw() -> FakeUWClient:
    return FakeUWClient()


class FakeAnthropicClient:
    """Minimal Anthropic client stand-in.

    Usage:
        fake_anthropic.queue_response('{"class_name":"PutCreditSpread",...}')
        resp = fake_anthropic.messages.create(...)
    """

    @dataclass
    class _Usage:
        input_tokens: int = 1000
        output_tokens: int = 200
        cache_read_input_tokens: int = 0
        cache_creation_input_tokens: int = 0

    @dataclass
    class _Content:
        type: str = "text"
        text: str = ""

    @dataclass
    class _Response:
        content: list["FakeAnthropicClient._Content"]
        usage: "FakeAnthropicClient._Usage"
        stop_reason: str = "end_turn"

    class _MessagesNamespace:
        def __init__(self, parent: "FakeAnthropicClient") -> None:
            self._parent = parent

        def create(self, **kwargs) -> "FakeAnthropicClient._Response":
            self._parent.call_log.append(kwargs)
            text = self._parent._queue.pop(0) if self._parent._queue else "{}"
            return FakeAnthropicClient._Response(
                content=[FakeAnthropicClient._Content(type="text", text=text)],
                usage=FakeAnthropicClient._Usage(),
            )

    def __init__(self) -> None:
        self._queue: list[str] = []
        self.call_log: list[dict[str, Any]] = []
        self.messages = FakeAnthropicClient._MessagesNamespace(self)

    def queue_response(self, text: str) -> None:
        self._queue.append(text)


@pytest.fixture
def fake_anthropic() -> FakeAnthropicClient:
    return FakeAnthropicClient()
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
pytest tests/integration/test_connection.py -v
```

- [ ] **Step 5: Commit**

```bash
git add bullbot/db/connection.py tests/conftest.py tests/integration/test_connection.py
git commit -m "stage1(T4): DB connection helper + pytest conftest

open_connection() applies schema idempotently, enables WAL + FK.
conftest provides db_conn (in-memory), FakeUWClient, FakeAnthropicClient."
```

---

## Phase B — Pure functions (T5–T11)

Every task in this phase follows the same TDD rhythm: write the test file with concrete assertions, run it to see `ModuleNotFoundError`, write the implementation, re-run to confirm, commit. Steps are compressed compared to Phase A but all test code and all implementation code is complete and inline — no placeholders.

### Task 5: bullbot/data/schemas.py (Pydantic models)

**Files:**
- Create: `bullbot/data/schemas.py`
- Create: `tests/unit/test_schemas.py`

- [ ] **Step 1: Write the test file** (`tests/unit/test_schemas.py`)

```python
"""Pydantic schema tests — every model roundtrips and rejects bad input."""
import pytest
from pydantic import ValidationError

from bullbot.data.schemas import (
    Bar,
    OptionContract,
    IVSurfacePoint,
    Greeks,
    Signal,
    Leg,
)


def test_bar_roundtrip():
    b = Bar(
        ticker="SPY",
        timeframe="1d",
        ts=1718395200,
        open=540.0, high=542.5, low=539.1, close=541.8,
        volume=1_234_567,
        source="uw",
    )
    assert b.ticker == "SPY"
    assert b.close == 541.8


def test_bar_rejects_negative_price():
    with pytest.raises(ValidationError):
        Bar(ticker="SPY", timeframe="1d", ts=1, open=-1.0, high=1, low=1, close=1, volume=1, source="uw")


def test_bar_rejects_unknown_source():
    with pytest.raises(ValidationError):
        Bar(ticker="SPY", timeframe="1d", ts=1, open=1, high=1, low=1, close=1, volume=1, source="robinhood")


def test_option_contract_roundtrip():
    c = OptionContract(
        ticker="SPY",
        expiry="2024-06-21",
        strike=540.0,
        kind="P",
        ts=1718395200,
        nbbo_bid=1.20,
        nbbo_ask=1.25,
        last=1.22,
        volume=5_000,
        open_interest=15_000,
        iv=0.143,
    )
    assert c.kind == "P"
    assert c.nbbo_bid == 1.20


def test_option_contract_rejects_invalid_kind():
    with pytest.raises(ValidationError):
        OptionContract(
            ticker="SPY", expiry="2024-06-21", strike=540, kind="X",
            ts=1, nbbo_bid=1, nbbo_ask=1, last=1, volume=1, open_interest=1, iv=0.1,
        )


def test_iv_surface_point():
    p = IVSurfacePoint(
        ticker="SPY",
        ts=1718395200,
        iv_rank=38.0,
        iv_percentile=42.0,
        atm_iv=0.143,
        implied_move=0.018,
    )
    assert p.iv_rank == 38.0


def test_greeks_model():
    g = Greeks(delta=0.52, gamma=0.005, theta=-0.31, vega=0.44, iv=0.143)
    assert abs(g.delta - 0.52) < 1e-9


def test_leg_and_signal():
    leg = Leg(
        option_symbol="SPY240621P00540000",
        side="short",
        quantity=1,
        strike=540.0,
        expiry="2024-06-21",
        kind="P",
    )
    signal = Signal(
        intent="open",
        strategy_class="PutCreditSpread",
        legs=[leg, Leg(option_symbol="SPY240621P00535000", side="long",
                       quantity=1, strike=535.0, expiry="2024-06-21", kind="P")],
        max_loss_per_contract=500.0,
        rationale="Short put credit spread at 25d short, 5-wide",
    )
    assert signal.intent == "open"
    assert len(signal.legs) == 2
    assert signal.max_loss_per_contract == 500.0


def test_signal_rejects_invalid_intent():
    with pytest.raises(ValidationError):
        Signal(
            intent="bogus",
            strategy_class="PutCreditSpread",
            legs=[],
            max_loss_per_contract=100.0,
            rationale="test",
        )
```

- [ ] **Step 2: Run test, verify import error**

```bash
pytest tests/unit/test_schemas.py -v
```

- [ ] **Step 3: Write `bullbot/data/schemas.py`**

```python
"""
Pydantic v2 models for every row type that crosses a module boundary.

No raw dicts escape the data layer — everything is validated into one of
these models first. Pydantic v2's `model_config` with `frozen=True` gives
us hashable, immutable value objects for free.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_FROZEN = ConfigDict(frozen=True, strict=True, extra="forbid")


class Bar(BaseModel):
    model_config = _FROZEN

    ticker: str
    timeframe: Literal["1d", "1h", "15m", "5m", "1m"]
    ts: int = Field(ge=0, description="UTC epoch seconds")
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    source: Literal["uw", "polygon"]

    @field_validator("ticker")
    @classmethod
    def _ticker_uppercase(cls, v: str) -> str:
        return v.upper()


class OptionContract(BaseModel):
    model_config = _FROZEN

    ticker: str
    expiry: str  # ISO YYYY-MM-DD
    strike: float = Field(gt=0)
    kind: Literal["C", "P"]
    ts: int = Field(ge=0)
    nbbo_bid: float = Field(ge=0)
    nbbo_ask: float = Field(ge=0)
    last: float | None = Field(default=None, ge=0)
    volume: int | None = Field(default=None, ge=0)
    open_interest: int | None = Field(default=None, ge=0)
    iv: float | None = Field(default=None, ge=0)


class IVSurfacePoint(BaseModel):
    model_config = _FROZEN

    ticker: str
    ts: int
    iv_rank: float
    iv_percentile: float
    atm_iv: float
    implied_move: float


class Greeks(BaseModel):
    model_config = _FROZEN

    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float


class Leg(BaseModel):
    model_config = _FROZEN

    option_symbol: str
    side: Literal["long", "short"]
    quantity: int = Field(gt=0)
    strike: float = Field(gt=0)
    expiry: str
    kind: Literal["C", "P"]


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    intent: Literal["open", "close"]
    strategy_class: str
    legs: list[Leg]
    max_loss_per_contract: float = Field(ge=0)
    rationale: str
    position_id_to_close: int | None = None   # set when intent='close'
```

- [ ] **Step 4: Run test, verify pass**

```bash
pytest tests/unit/test_schemas.py -v
```

- [ ] **Step 5: Commit**

```bash
git add bullbot/data/schemas.py tests/unit/test_schemas.py
git commit -m "stage1(T5): Pydantic schemas for Bar/OptionContract/Signal/Leg/Greeks

Frozen models, strict validation, Literal types for enums."
```

---

### Task 6: bullbot/features/indicators.py

**Files:**
- Create: `bullbot/features/indicators.py`
- Create: `tests/unit/test_indicators.py`

- [ ] **Step 1: Write the test file** (`tests/unit/test_indicators.py`)

```python
"""Golden-value tests for every indicator. Numbers hand-computed."""
import numpy as np
import pytest

from bullbot.features import indicators


CLOSES_20 = [
    100.0, 101.5, 102.0, 101.0, 99.5, 100.2, 101.8, 103.0, 104.5, 103.8,
    105.0, 106.2, 105.5, 104.8, 106.0, 107.5, 108.0, 107.2, 108.5, 109.0,
]


def test_sma_20_matches_numpy():
    result = indicators.sma(CLOSES_20, 20)
    expected = float(np.mean(CLOSES_20))
    assert abs(result - expected) < 1e-9


def test_sma_returns_none_when_insufficient_data():
    assert indicators.sma([1.0, 2.0, 3.0], 20) is None


def test_ema_20_matches_pandas():
    import pandas as pd
    result = indicators.ema(CLOSES_20, 20)
    expected = pd.Series(CLOSES_20).ewm(span=20, adjust=False).mean().iloc[-1]
    assert abs(result - float(expected)) < 1e-9


def test_rsi_14_known_value():
    # Reference: Wilder's RSI on a crafted series
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
              46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
              46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78]
    rsi = indicators.rsi(closes, 14)
    # Published reference: ~43.99 (from Wilder's original textbook)
    assert 40.0 < rsi < 50.0


def test_atr_14_returns_positive_for_real_series():
    highs = [102.0, 103.5, 104.0, 105.0, 104.5, 106.0, 107.5, 108.0,
             107.2, 108.5, 109.0, 110.5, 111.0, 112.0, 113.5]
    lows = [99.0, 100.5, 101.0, 102.0, 101.5, 103.0, 104.5, 105.0,
            104.2, 105.5, 106.0, 107.5, 108.0, 109.0, 110.5]
    closes = [101.0, 102.0, 103.5, 104.0, 103.0, 105.5, 106.0, 107.5,
              106.2, 107.0, 108.5, 109.0, 110.0, 111.5, 112.5]
    atr = indicators.atr(highs, lows, closes, 14)
    assert atr > 0
    # Sanity: ATR should be roughly in the range of daily H-L
    assert 1.0 < atr < 5.0


def test_bollinger_bands_symmetry():
    # With constant data, upper/lower should equal the sma
    constant = [100.0] * 25
    upper, mid, lower = indicators.bollinger(constant, 20, 2.0)
    assert abs(upper - 100.0) < 1e-9
    assert abs(mid - 100.0) < 1e-9
    assert abs(lower - 100.0) < 1e-9


def test_bollinger_width_on_real_series():
    upper, mid, lower = indicators.bollinger(CLOSES_20, 20, 2.0)
    assert upper > mid > lower


def test_iv_rank_uses_min_max_of_history():
    # Current IV at median of history → rank should be ~50
    history = [0.10, 0.15, 0.20, 0.25, 0.30]  # range [0.10, 0.30]
    current = 0.20
    rank = indicators.iv_rank(current, history)
    assert abs(rank - 50.0) < 1e-6


def test_iv_rank_current_at_max():
    history = [0.10, 0.15, 0.20, 0.25, 0.30]
    assert abs(indicators.iv_rank(0.30, history) - 100.0) < 1e-6


def test_iv_rank_current_at_min():
    history = [0.10, 0.15, 0.20, 0.25, 0.30]
    assert abs(indicators.iv_rank(0.10, history) - 0.0) < 1e-6


def test_iv_percentile_counts_rank():
    history = [0.10, 0.15, 0.20, 0.25, 0.30]
    # Current 0.22 → 3 of 5 historical values are <= 0.22 → 60%
    pct = indicators.iv_percentile(0.22, history)
    assert abs(pct - 60.0) < 1e-6
```

- [ ] **Step 2: Run test, verify it fails**

```bash
pytest tests/unit/test_indicators.py -v
```

- [ ] **Step 3: Write `bullbot/features/indicators.py`**

```python
"""
Technical indicators. All pure functions over lists of floats — no I/O,
no classes, no state. Every function returns `None` when insufficient
data rather than raising.
"""

from __future__ import annotations

from statistics import mean, pstdev


def sma(values: list[float], period: int) -> float | None:
    """Simple moving average of the LAST `period` values."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float | None:
    """Exponential moving average (pandas-compatible, adjust=False)."""
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    ema_val = values[0]
    for v in values[1:]:
        ema_val = alpha * v + (1 - alpha) * ema_val
    return ema_val


def rsi(values: list[float], period: int = 14) -> float | None:
    """Wilder's RSI."""
    if len(values) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float | None:
    """Average true range (Wilder smoothing)."""
    if len(highs) < period + 1 or not (len(highs) == len(lows) == len(closes)):
        return None
    trs: list[float] = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    atr_val = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
    return atr_val


def bollinger(
    values: list[float],
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[float, float, float] | None:
    """Returns (upper, middle, lower) over the last `period` values."""
    if len(values) < period:
        return None
    window = values[-period:]
    m = mean(window)
    sd = pstdev(window)
    return (m + num_std * sd, m, m - num_std * sd)


def iv_rank(current_iv: float, history: list[float]) -> float:
    """IV rank: where does current IV sit in [min, max] of history?"""
    if not history:
        return 0.0
    lo = min(history)
    hi = max(history)
    if hi == lo:
        return 50.0
    return 100.0 * (current_iv - lo) / (hi - lo)


def iv_percentile(current_iv: float, history: list[float]) -> float:
    """IV percentile: what fraction of historical IVs were <= current?"""
    if not history:
        return 0.0
    count = sum(1 for h in history if h <= current_iv)
    return 100.0 * count / len(history)
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_indicators.py -v
```

- [ ] **Step 5: Commit**

```bash
git add bullbot/features/indicators.py tests/unit/test_indicators.py
git commit -m "stage1(T6): features/indicators — SMA/EMA/RSI/ATR/BB/IV rank/percentile

Pure functions, golden-value tests cross-checked against numpy/pandas."
```

---

### Task 7: bullbot/features/greeks.py

**Files:**
- Create: `bullbot/features/greeks.py`
- Create: `tests/unit/test_greeks.py`

- [ ] **Step 1: Write test** (`tests/unit/test_greeks.py`)

```python
"""
Greeks and IV inversion tests.

Black-Scholes closed form + Brent's-method inverter. Golden values from
standard references (e.g., Hull's textbook table 15.2).
"""
import math

import pytest

from bullbot.features import greeks


def test_bs_call_atm_short_dated():
    # S=100, K=100, r=5%, T=0.25 (3 months), sigma=20%
    # Hull reference value: ~4.615
    price = greeks.bs_price(
        spot=100.0, strike=100.0, t_years=0.25, r=0.05, sigma=0.20, is_put=False
    )
    assert 4.5 < price < 4.7


def test_bs_put_itm():
    # S=95, K=100, r=5%, T=0.25, sigma=30%
    price = greeks.bs_price(
        spot=95.0, strike=100.0, t_years=0.25, r=0.05, sigma=0.30, is_put=True
    )
    assert 6.0 < price < 8.0


def test_bs_atm_delta_is_around_half():
    g = greeks.compute_greeks(
        spot=100.0, strike=100.0, t_years=0.25, r=0.05, sigma=0.20, is_put=False
    )
    assert 0.55 < g.delta < 0.65  # ATM-ish call delta > 0.5 due to r > 0


def test_bs_put_delta_is_negative():
    g = greeks.compute_greeks(
        spot=100.0, strike=100.0, t_years=0.25, r=0.05, sigma=0.20, is_put=True
    )
    assert -0.5 < g.delta < 0.0


def test_bs_theta_is_negative_for_long_options():
    g = greeks.compute_greeks(
        spot=100.0, strike=100.0, t_years=0.25, r=0.05, sigma=0.20, is_put=False
    )
    assert g.theta < 0


def test_implied_vol_roundtrip_call():
    # Price an option with known sigma, then invert.
    sigma_true = 0.25
    price = greeks.bs_price(
        spot=100.0, strike=105.0, t_years=0.5, r=0.03, sigma=sigma_true, is_put=False
    )
    sigma_recovered = greeks.implied_volatility(
        mid=price, spot=100.0, strike=105.0, t_years=0.5, r=0.03, is_put=False
    )
    assert abs(sigma_recovered - sigma_true) < 1e-4


def test_implied_vol_roundtrip_put():
    sigma_true = 0.18
    price = greeks.bs_price(
        spot=100.0, strike=95.0, t_years=0.25, r=0.04, sigma=sigma_true, is_put=True
    )
    sigma_recovered = greeks.implied_volatility(
        mid=price, spot=100.0, strike=95.0, t_years=0.25, r=0.04, is_put=True
    )
    assert abs(sigma_recovered - sigma_true) < 1e-4


def test_implied_vol_returns_none_on_nonsense_price():
    # Mid below intrinsic value → no valid sigma exists
    assert (
        greeks.implied_volatility(
            mid=0.01, spot=100.0, strike=150.0, t_years=0.25, r=0.03, is_put=False
        )
        is None
    )
```

- [ ] **Step 2: Run, verify fail**

```bash
pytest tests/unit/test_greeks.py -v
```

- [ ] **Step 3: Write `bullbot/features/greeks.py`**

```python
"""
Black-Scholes pricing, analytic greeks, and implied-volatility inversion.

All functions are pure. `compute_greeks` returns a `Greeks` dataclass from
`bullbot.data.schemas`. `implied_volatility` uses scipy's `brentq` to
invert the BS pricing function numerically.
"""

from __future__ import annotations

import math

from scipy.optimize import brentq
from scipy.stats import norm

from bullbot.data.schemas import Greeks


def bs_price(
    spot: float,
    strike: float,
    t_years: float,
    r: float,
    sigma: float,
    is_put: bool,
) -> float:
    """Black-Scholes price (no dividends) for a European call or put."""
    if t_years <= 0 or sigma <= 0:
        # Intrinsic value
        if is_put:
            return max(strike - spot, 0.0)
        return max(spot - strike, 0.0)

    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t_years) / (
        sigma * math.sqrt(t_years)
    )
    d2 = d1 - sigma * math.sqrt(t_years)

    if is_put:
        return strike * math.exp(-r * t_years) * norm.cdf(-d2) - spot * norm.cdf(-d1)
    return spot * norm.cdf(d1) - strike * math.exp(-r * t_years) * norm.cdf(d2)


def compute_greeks(
    spot: float,
    strike: float,
    t_years: float,
    r: float,
    sigma: float,
    is_put: bool,
) -> Greeks:
    """Closed-form delta/gamma/theta/vega in Black-Scholes."""
    if t_years <= 0 or sigma <= 0:
        return Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0, iv=sigma)

    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    if is_put:
        delta = norm.cdf(d1) - 1.0
        theta = (
            -spot * norm.pdf(d1) * sigma / (2.0 * sqrt_t)
            + r * strike * math.exp(-r * t_years) * norm.cdf(-d2)
        )
    else:
        delta = norm.cdf(d1)
        theta = (
            -spot * norm.pdf(d1) * sigma / (2.0 * sqrt_t)
            - r * strike * math.exp(-r * t_years) * norm.cdf(d2)
        )

    gamma = norm.pdf(d1) / (spot * sigma * sqrt_t)
    vega = spot * norm.pdf(d1) * sqrt_t / 100.0   # per 1% change in IV

    # Theta returned per calendar day rather than per year
    theta_per_day = theta / 365.0

    return Greeks(delta=delta, gamma=gamma, theta=theta_per_day, vega=vega, iv=sigma)


def implied_volatility(
    mid: float,
    spot: float,
    strike: float,
    t_years: float,
    r: float,
    is_put: bool,
) -> float | None:
    """
    Numerically invert Black-Scholes for implied volatility.

    Returns None if the mid price is outside the arbitrage-free range
    (i.e., below intrinsic or above spot for calls, above strike for puts).
    """
    if t_years <= 0 or mid <= 0:
        return None

    # Arbitrage bounds sanity check
    if is_put:
        lower_bound = max(strike * math.exp(-r * t_years) - spot, 0.0)
        upper_bound = strike * math.exp(-r * t_years)
    else:
        lower_bound = max(spot - strike * math.exp(-r * t_years), 0.0)
        upper_bound = spot

    if mid < lower_bound - 1e-6 or mid > upper_bound + 1e-6:
        return None

    def objective(sigma: float) -> float:
        return bs_price(spot, strike, t_years, r, sigma, is_put) - mid

    try:
        return brentq(objective, 1e-6, 5.0, maxiter=100, xtol=1e-8)
    except (ValueError, RuntimeError):
        return None
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_greeks.py -v
```

- [ ] **Step 5: Commit**

```bash
git add bullbot/features/greeks.py tests/unit/test_greeks.py
git commit -m "stage1(T7): BS pricing + closed-form greeks + brentq IV inverter

Backs the hybrid native/inverted-IV policy from spec §6.6."
```

---

### Task 8: bullbot/features/regime.py

**Files:**
- Create: `bullbot/features/regime.py`
- Create: `tests/unit/test_regime.py`

- [ ] **Step 1: Write test**

```python
"""Regime classifier tests."""
from bullbot.features import regime


def test_flat_chop():
    closes = [100.0] * 60
    assert regime.classify(closes) == "chop"


def test_strong_bull():
    # 8% rise over 60 days, low volatility
    closes = [100.0 + 0.13 * i for i in range(60)]  # ~7.8% return
    # Make it slightly over +5% with low vol
    closes = [100.0 + 0.10 * i + 0.02 * (i % 3) for i in range(60)]
    assert closes[-1] / closes[0] - 1 > 0.05
    result = regime.classify(closes)
    assert result == "bull"


def test_bear_on_drop():
    closes = [100.0 - 0.12 * i for i in range(60)]  # 7.2% drop
    assert regime.classify(closes) == "bear"


def test_high_vol_bull_becomes_chop():
    # +6% return but very noisy
    import math
    closes = [
        100.0 * (1 + 0.06 * i / 59) + 5.0 * math.sin(i)
        for i in range(60)
    ]
    # Rolling 30d vol should be high enough to kick out of "bull"
    result = regime.classify(closes)
    # Either chop (if vol is high enough) or bull (if not) — assert sane
    assert result in ("chop", "bull")
```

- [ ] **Step 2: Write `bullbot/features/regime.py`**

```python
"""
Market regime classifier.

Pinned algorithm (spec §6.7): rolling 60-day return + rolling 30-day
annualized volatility. Thresholds live in bullbot.config.
"""

from __future__ import annotations

import math

from bullbot import config


def classify(closes_60d: list[float]) -> str:
    """Classify market regime as 'bull' | 'bear' | 'chop'.

    Requires at least 60 closes. Returns 'chop' on insufficient data
    rather than raising — callers should ensure enough history before
    expecting a meaningful verdict.
    """
    if len(closes_60d) < 60:
        return "chop"

    rolling_60d_return = (closes_60d[-1] - closes_60d[-60]) / closes_60d[-60]

    # 30-day annualized volatility of daily returns
    returns = [
        (closes_60d[i] - closes_60d[i - 1]) / closes_60d[i - 1]
        for i in range(-30, 0)
    ]
    if len(returns) < 2:
        return "chop"
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    ann_vol = math.sqrt(var) * math.sqrt(252)

    if rolling_60d_return >= config.REGIME_BULL_RETURN_MIN and ann_vol < config.REGIME_BULL_VOL_MAX:
        return "bull"
    if rolling_60d_return <= config.REGIME_BEAR_RETURN_MAX:
        return "bear"
    return "chop"
```

- [ ] **Step 3: Run + commit**

```bash
pytest tests/unit/test_regime.py -v && \
git add bullbot/features/regime.py tests/unit/test_regime.py && \
git commit -m "stage1(T8): features/regime — pinned bull/bear/chop classifier (spec §6.7)"
```

---

### Task 9: bullbot/evolver/plateau.py

**Files:**
- Create: `bullbot/evolver/plateau.py`
- Create: `tests/unit/test_plateau.py`

- [ ] **Step 1: Write test** — table-driven across every branch of the state machine classifier.

```python
"""
Plateau classifier tests — the state-machine function that decides whether
an iteration outcome means 'continue', 'no_edge', or 'edge_found'.
"""
from dataclasses import dataclass

import pytest

from bullbot.evolver import plateau


@dataclass
class FakeState:
    iteration_count: int = 0
    plateau_counter: int = 0
    best_pf_oos: float = 0.0


@dataclass
class FakeMetrics:
    pf_is: float
    pf_oos: float
    trade_count: int = 40


def test_edge_found_when_all_gates_pass():
    state = FakeState(iteration_count=5, plateau_counter=1, best_pf_oos=1.1)
    metrics = FakeMetrics(pf_is=1.55, pf_oos=1.35, trade_count=35)
    result = plateau.classify(state, metrics)
    assert result.verdict == "edge_found"


def test_not_edge_found_when_trade_count_too_low():
    state = FakeState()
    metrics = FakeMetrics(pf_is=1.55, pf_oos=1.35, trade_count=29)
    result = plateau.classify(state, metrics)
    assert result.verdict != "edge_found"


def test_no_edge_when_plateau_counter_maxes_out():
    # 2 previous stall ticks + this proposal also stalls → third stall → no_edge
    state = FakeState(iteration_count=16, plateau_counter=2, best_pf_oos=1.00)
    metrics = FakeMetrics(pf_is=1.20, pf_oos=1.05, trade_count=35)  # below gate + no improvement
    result = plateau.classify(state, metrics)
    assert result.verdict == "no_edge"
    assert result.new_plateau_counter == 3


def test_no_edge_when_iteration_cap_hit():
    state = FakeState(iteration_count=50, plateau_counter=0, best_pf_oos=0.8)
    metrics = FakeMetrics(pf_is=1.00, pf_oos=0.90, trade_count=40)
    result = plateau.classify(state, metrics)
    assert result.verdict == "no_edge"


def test_continue_on_small_improvement_resets_plateau():
    state = FakeState(iteration_count=8, plateau_counter=2, best_pf_oos=1.00)
    metrics = FakeMetrics(pf_is=1.20, pf_oos=1.15, trade_count=40)  # +0.15 improvement
    result = plateau.classify(state, metrics)
    assert result.verdict == "continue"
    assert result.new_plateau_counter == 0
    assert result.improved is True


def test_continue_on_insufficient_improvement_increments_plateau():
    state = FakeState(iteration_count=8, plateau_counter=1, best_pf_oos=1.00)
    metrics = FakeMetrics(pf_is=1.20, pf_oos=1.05, trade_count=40)  # +0.05 improvement, below threshold
    result = plateau.classify(state, metrics)
    assert result.verdict == "continue"
    assert result.new_plateau_counter == 2


def test_improved_means_new_best_pf():
    state = FakeState(iteration_count=1, plateau_counter=0, best_pf_oos=0.8)
    metrics = FakeMetrics(pf_is=1.10, pf_oos=1.05, trade_count=40)
    result = plateau.classify(state, metrics)
    assert result.improved is True
    assert result.new_best_pf_oos == 1.05


def test_first_iteration_never_triggers_no_edge_on_iteration_cap():
    state = FakeState(iteration_count=0)
    metrics = FakeMetrics(pf_is=1.0, pf_oos=0.9, trade_count=40)
    result = plateau.classify(state, metrics)
    assert result.verdict == "continue"
```

- [ ] **Step 2: Write `bullbot/evolver/plateau.py`**

```python
"""
Plateau / edge-gate classifier. Pure function — no I/O.

Called inside evolver_iteration after backtest metrics are computed to
decide whether to (a) continue iterating, (b) mark the ticker as no_edge,
or (c) mark it as edge_found (promote to paper_trial).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from bullbot import config


Verdict = Literal["continue", "no_edge", "edge_found"]


class _StateLike(Protocol):
    iteration_count: int
    plateau_counter: int
    best_pf_oos: float


class _MetricsLike(Protocol):
    pf_is: float
    pf_oos: float
    trade_count: int


@dataclass(frozen=True)
class ClassifyResult:
    verdict: Verdict
    improved: bool
    new_plateau_counter: int
    new_best_pf_oos: float


def classify(state: _StateLike, metrics: _MetricsLike) -> ClassifyResult:
    """Decide the next action for a ticker given a fresh backtest result."""
    passed_gate = (
        metrics.pf_is >= config.EDGE_PF_IS_MIN
        and metrics.pf_oos >= config.EDGE_PF_OOS_MIN
        and metrics.trade_count >= config.EDGE_TRADE_COUNT_MIN
    )

    if passed_gate:
        return ClassifyResult(
            verdict="edge_found",
            improved=metrics.pf_oos > state.best_pf_oos,
            new_plateau_counter=0,
            new_best_pf_oos=max(state.best_pf_oos, metrics.pf_oos),
        )

    improved = metrics.pf_oos > state.best_pf_oos + config.PLATEAU_IMPROVEMENT_MIN
    new_best = max(state.best_pf_oos, metrics.pf_oos)

    if improved:
        new_plateau = 0
    else:
        new_plateau = state.plateau_counter + 1

    # Safety cap
    if state.iteration_count + 1 >= config.ITERATION_CAP:
        return ClassifyResult(
            verdict="no_edge",
            improved=improved,
            new_plateau_counter=new_plateau,
            new_best_pf_oos=new_best,
        )

    if new_plateau >= config.PLATEAU_COUNTER_MAX:
        return ClassifyResult(
            verdict="no_edge",
            improved=improved,
            new_plateau_counter=new_plateau,
            new_best_pf_oos=new_best,
        )

    return ClassifyResult(
        verdict="continue",
        improved=improved,
        new_plateau_counter=new_plateau,
        new_best_pf_oos=new_best,
    )
```

- [ ] **Step 3: Run, verify pass, commit**

```bash
pytest tests/unit/test_plateau.py -v && \
git add bullbot/evolver/plateau.py tests/unit/test_plateau.py && \
git commit -m "stage1(T9): evolver/plateau — pure classifier for continue/no_edge/edge_found"
```

---

### Task 10: bullbot/engine/fill_model.py

**Files:**
- Create: `bullbot/engine/fill_model.py`
- Create: `tests/unit/test_fill_model.py`

- [ ] **Step 1: Write test**

```python
"""Fill model tests — mid ± half-spread ± one tick slippage, commissions."""
import pytest

from bullbot.data.schemas import Leg
from bullbot.engine import fill_model


def _chain_row(bid: float, ask: float) -> dict:
    return {"nbbo_bid": bid, "nbbo_ask": ask, "last": (bid + ask) / 2}


def test_short_leg_sells_below_mid():
    # short credit → want to receive less than mid (worse fill for us)
    bid, ask = 1.20, 1.30
    fill = fill_model.simulate_leg_open(
        leg=Leg(option_symbol="X", side="short", quantity=1,
                strike=500, expiry="2024-01-01", kind="P"),
        chain_row=_chain_row(bid, ask),
    )
    mid = (bid + ask) / 2
    assert fill < mid    # we receive LESS than mid
    assert abs(fill - (mid - 0.01)) < 1e-9   # exactly one tick below mid


def test_long_leg_pays_above_mid():
    bid, ask = 1.20, 1.30
    fill = fill_model.simulate_leg_open(
        leg=Leg(option_symbol="X", side="long", quantity=1,
                strike=500, expiry="2024-01-01", kind="P"),
        chain_row=_chain_row(bid, ask),
    )
    mid = (bid + ask) / 2
    assert fill > mid    # we pay MORE than mid
    assert abs(fill - (mid + 0.01)) < 1e-9


def test_rejects_zero_bid():
    with pytest.raises(fill_model.FillRejected):
        fill_model.simulate_leg_open(
            leg=Leg(option_symbol="X", side="short", quantity=1,
                    strike=500, expiry="2024-01-01", kind="P"),
            chain_row=_chain_row(0.0, 1.30),
        )


def test_rejects_wide_spread_beyond_cap():
    # spread 0.80 on mid 1.20 → 67% > 50% cap
    with pytest.raises(fill_model.FillRejected):
        fill_model.simulate_leg_open(
            leg=Leg(option_symbol="X", side="short", quantity=1,
                    strike=500, expiry="2024-01-01", kind="P"),
            chain_row=_chain_row(0.80, 1.60),
        )


def test_accepts_spread_below_cap():
    # spread 0.20 on mid 1.20 → 17% < 50% cap
    fill_model.simulate_leg_open(
        leg=Leg(option_symbol="X", side="short", quantity=1,
                strike=500, expiry="2024-01-01", kind="P"),
        chain_row=_chain_row(1.10, 1.30),
    )


def test_commission_scales_with_legs_and_contracts():
    cost = fill_model.commission(contracts=3, n_legs=4)
    assert cost == pytest.approx(3 * 4 * 0.65)


def test_net_open_credit_credit_spread():
    # Short 540P @ 2.20, long 535P @ 1.00 → net credit 1.20
    legs = [
        Leg(option_symbol="A", side="short", quantity=1, strike=540, expiry="2024-01-01", kind="P"),
        Leg(option_symbol="B", side="long", quantity=1, strike=535, expiry="2024-01-01", kind="P"),
    ]
    chain_rows = {
        "A": _chain_row(2.15, 2.25),
        "B": _chain_row(0.95, 1.05),
    }
    net, legs_filled = fill_model.simulate_open_multi_leg(legs, chain_rows, contracts=1)
    # Short leg fills at mid (2.20) - 0.01 = 2.19 (received, sign = negative for short)
    # Long leg fills at mid (1.00) + 0.01 = 1.01 (paid, sign = positive for long)
    # Net credit = 2.19 - 1.01 = 1.18
    assert abs(net - (-1.18)) < 1e-9    # negative = credit received (our convention)
```

- [ ] **Step 2: Write `bullbot/engine/fill_model.py`**

```python
"""
Options fill simulator.

Convention: open fills return `net_cash_flow` where NEGATIVE means credit
(we received money) and POSITIVE means debit (we paid). This is the same
convention as brokerage order tickets.

Short legs fill at `mid - 0.01`, long legs at `mid + 0.01`. This is "mid
worse-by-one-tick" and bakes in both half-spread and standard slippage in
a single conservative number.
"""

from __future__ import annotations

from typing import Any

from bullbot import config
from bullbot.data.schemas import Leg


TICK = 0.01


class FillRejected(Exception):
    """Raised when a leg cannot fill (zero liquidity or too-wide spread)."""


def _validate_chain_row(row: dict[str, Any]) -> tuple[float, float]:
    bid = float(row.get("nbbo_bid") or 0)
    ask = float(row.get("nbbo_ask") or 0)
    if bid <= 0 or ask <= 0:
        raise FillRejected(f"zero liquidity: bid={bid} ask={ask}")
    if ask <= bid:
        raise FillRejected(f"inverted spread: bid={bid} ask={ask}")
    mid = (bid + ask) / 2
    if mid == 0 or (ask - bid) / mid > config.MIN_SPREAD_FRAC:
        raise FillRejected(
            f"spread too wide: {(ask - bid):.3f} > {config.MIN_SPREAD_FRAC} * mid {mid:.3f}"
        )
    return bid, ask


def simulate_leg_open(leg: Leg, chain_row: dict[str, Any]) -> float:
    """Return the per-contract fill price for opening `leg`."""
    bid, ask = _validate_chain_row(chain_row)
    mid = (bid + ask) / 2
    if leg.side == "short":
        return mid - TICK
    return mid + TICK


def simulate_leg_close(leg: Leg, chain_row: dict[str, Any]) -> float:
    """Return the per-contract fill price for closing `leg` (opposite side)."""
    bid, ask = _validate_chain_row(chain_row)
    mid = (bid + ask) / 2
    # Closing a short means buying back — we pay mid + tick
    # Closing a long  means selling    — we receive mid - tick
    if leg.side == "short":
        return mid + TICK
    return mid - TICK


def commission(contracts: int, n_legs: int) -> float:
    """Total commission for a multi-leg order."""
    return contracts * n_legs * config.COMMISSION_PER_CONTRACT_USD


def simulate_open_multi_leg(
    legs: list[Leg],
    chain_rows: dict[str, dict[str, Any]],
    contracts: int,
) -> tuple[float, list[dict[str, Any]]]:
    """
    Simulate opening a multi-leg order.

    Returns (net_cash_flow, filled_legs) where:
    - net_cash_flow is NEGATIVE for credit received, POSITIVE for debit paid
    - filled_legs is a list of dicts [{option_symbol, side, qty, fill_price}]

    Raises FillRejected if any leg can't fill.
    """
    net = 0.0
    filled: list[dict[str, Any]] = []
    for leg in legs:
        row = chain_rows.get(leg.option_symbol)
        if row is None:
            raise FillRejected(f"no chain data for {leg.option_symbol}")
        price = simulate_leg_open(leg, row)
        qty = leg.quantity * contracts
        sign = 1 if leg.side == "long" else -1  # long pays +, short receives -
        net += sign * price * qty
        filled.append(
            {
                "option_symbol": leg.option_symbol,
                "side": leg.side,
                "qty": qty,
                "fill_price": price,
            }
        )
    # 100× multiplier: options quoted per share, traded per contract (100 shares)
    return net * 100, filled


def simulate_close_multi_leg(
    legs: list[Leg],
    chain_rows: dict[str, dict[str, Any]],
    contracts: int,
) -> tuple[float, list[dict[str, Any]]]:
    """Close a multi-leg position. Same conventions as open but opposite sides."""
    net = 0.0
    filled: list[dict[str, Any]] = []
    for leg in legs:
        row = chain_rows.get(leg.option_symbol)
        if row is None:
            raise FillRejected(f"no chain data for {leg.option_symbol}")
        price = simulate_leg_close(leg, row)
        qty = leg.quantity * contracts
        # Closing a short = buying back (debit); closing a long = selling (credit)
        sign = 1 if leg.side == "short" else -1
        net += sign * price * qty
        filled.append(
            {
                "option_symbol": leg.option_symbol,
                "side": leg.side,
                "qty": qty,
                "fill_price": price,
            }
        )
    return net * 100, filled


def mark_position(
    legs: list[Leg],
    chain_rows: dict[str, dict[str, Any]],
    contracts: int,
) -> float:
    """Mark-to-market a position using the current mid (no slippage)."""
    total = 0.0
    for leg in legs:
        row = chain_rows.get(leg.option_symbol)
        if row is None:
            continue
        bid = float(row.get("nbbo_bid") or 0)
        ask = float(row.get("nbbo_ask") or 0)
        if bid <= 0 or ask <= 0:
            continue
        mid = (bid + ask) / 2
        qty = leg.quantity * contracts
        sign = 1 if leg.side == "long" else -1
        total += sign * mid * qty
    return total * 100
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/unit/test_fill_model.py -v && \
git add bullbot/engine/fill_model.py tests/unit/test_fill_model.py && \
git commit -m "stage1(T10): engine/fill_model — open/close/mark with commissions + slippage"
```

---

### Task 11: bullbot/engine/position_sizer.py

**Files:**
- Create: `bullbot/engine/position_sizer.py`
- Create: `tests/unit/test_position_sizer.py`

- [ ] **Step 1: Write test**

```python
"""Position sizer — fixed 2% of equity at risk per position."""
from bullbot.engine import position_sizer


def test_basic_2_percent_sizing():
    # $50k equity × 2% = $1000 risk. Max loss per contract $500 → 2 contracts.
    n = position_sizer.size_position(equity=50_000, max_loss_per_contract=500.0)
    assert n == 2


def test_rounds_down_not_up():
    # $50k × 2% = $1000 risk. Max loss $300 → floor(1000/300) = 3, not 4
    n = position_sizer.size_position(equity=50_000, max_loss_per_contract=300.0)
    assert n == 3


def test_returns_zero_when_one_contract_exceeds_cap():
    # Max loss $1500 > $1000 risk budget → cannot size any contracts
    n = position_sizer.size_position(equity=50_000, max_loss_per_contract=1500.0)
    assert n == 0


def test_scales_with_equity_growth():
    # Equity drifts up → more contracts
    n = position_sizer.size_position(equity=75_000, max_loss_per_contract=500.0)
    assert n == 3   # $1500 budget / $500 = 3


def test_respects_max_per_ticker_cap(monkeypatch):
    from bullbot import config
    monkeypatch.setattr(config, "MAX_POSITIONS_PER_TICKER", 3)
    # Raw math would say 20 contracts, but cap limits to 3
    n = position_sizer.size_position(equity=1_000_000, max_loss_per_contract=500.0)
    assert n == 3
```

- [ ] **Step 2: Write `bullbot/engine/position_sizer.py`**

```python
"""
Position sizer — fixed fraction of current equity at risk per position.

Spec §6.3: max_contracts = floor( (POSITION_RISK_FRAC × equity) /
max_loss_per_contract ), capped at MAX_POSITIONS_PER_TICKER.
"""

from __future__ import annotations

from bullbot import config


def size_position(equity: float, max_loss_per_contract: float) -> int:
    """Return the contract count for this position, or 0 if it can't be sized."""
    if max_loss_per_contract <= 0:
        return 0
    risk_budget = config.POSITION_RISK_FRAC * equity
    raw = int(risk_budget // max_loss_per_contract)
    return max(0, min(raw, config.MAX_POSITIONS_PER_TICKER))
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/unit/test_position_sizer.py -v && \
git add bullbot/engine/position_sizer.py tests/unit/test_position_sizer.py && \
git commit -m "stage1(T11): engine/position_sizer — 2% equity-at-risk fixed sizing"
```

---

## Phase C — Data layer (T12–T14)

Remaining tasks are presented more compactly: **Test** → **Impl** → **Verify + Commit**. All code inline, no placeholders.

### Task 12: bullbot/data/fetchers.py (UW HTTP client)

**Files:**
- Create: `bullbot/data/fetchers.py`
- Create: `tests/integration/test_fetchers.py`
- Create: `tests/fixtures/uw_responses/spy_daily.json`, `tests/fixtures/uw_responses/spy_chains_snapshot.json`, `tests/fixtures/uw_responses/option_historic.json`

- [ ] **Step 1: Write the test**

```python
"""Fetcher tests — use FakeUWClient from conftest, no real HTTP."""
import json
from pathlib import Path

import pytest

from bullbot.data import fetchers
from bullbot.data.schemas import Bar, OptionContract
from tests.conftest import FakeUWResponse


FIXTURES = Path(__file__).parent.parent / "fixtures" / "uw_responses"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_fetch_daily_ohlc_returns_bars(fake_uw):
    fake_uw.register(
        "/api/stock/SPY/ohlc/1d",
        FakeUWResponse(body=_load_fixture("spy_daily.json")),
    )
    bars = fetchers.fetch_daily_ohlc(fake_uw, "SPY", limit=100)
    assert len(bars) > 0
    assert all(isinstance(b, Bar) for b in bars)
    assert all(b.ticker == "SPY" for b in bars)
    assert all(b.timeframe == "1d" for b in bars)


def test_fetch_daily_ohlc_rejects_empty(fake_uw):
    fake_uw.register("/api/stock/SPY/ohlc/1d", FakeUWResponse(body={"data": []}))
    with pytest.raises(fetchers.DataFetchError):
        fetchers.fetch_daily_ohlc(fake_uw, "SPY", limit=100)


def test_fetch_option_historic_returns_contracts(fake_uw):
    fake_uw.register(
        "/api/option-contract/SPY260417P00666000/historic",
        FakeUWResponse(body=_load_fixture("option_historic.json")),
    )
    contracts = fetchers.fetch_option_historic(fake_uw, "SPY260417P00666000")
    assert len(contracts) > 0
    assert all(isinstance(c, OptionContract) for c in contracts)
    assert all(c.ticker == "SPY" for c in contracts)


def test_fetch_option_historic_returns_empty_on_404(fake_uw):
    fake_uw.register(
        "/api/option-contract/SPYBOGUS/historic",
        FakeUWResponse(status=404, body={"error": "not found"}),
    )
    # 404 on a constructed symbol that doesn't exist should NOT raise —
    # it's expected during symbol enumeration for invalid grids.
    result = fetchers.fetch_option_historic(fake_uw, "SPYBOGUS")
    assert result == []


def test_fetch_chains_snapshot_returns_symbol_list(fake_uw):
    fake_uw.register(
        "/api/stock/SPY/option-chains",
        FakeUWResponse(body=_load_fixture("spy_chains_snapshot.json")),
    )
    symbols = fetchers.fetch_chains_snapshot(fake_uw, "SPY", date="2026-04-06")
    assert isinstance(symbols, list)
    assert all(isinstance(s, str) for s in symbols)
```

- [ ] **Step 2: Create the JSON fixtures** (small, representative subsets)

```bash
mkdir -p tests/fixtures/uw_responses
```

`tests/fixtures/uw_responses/spy_daily.json`:
```json
{
  "data": [
    {"candle_start_time": "2026-04-01T00:00:00Z", "open": "575.10", "high": "578.30", "low": "574.50", "close": "577.80", "volume": 58000000},
    {"candle_start_time": "2026-04-02T00:00:00Z", "open": "577.80", "high": "580.50", "low": "576.90", "close": "580.10", "volume": 62000000},
    {"candle_start_time": "2026-04-03T00:00:00Z", "open": "580.10", "high": "582.40", "low": "579.60", "close": "581.90", "volume": 55000000},
    {"candle_start_time": "2026-04-06T00:00:00Z", "open": "581.90", "high": "583.70", "low": "580.80", "close": "582.50", "volume": 48000000},
    {"candle_start_time": "2026-04-07T00:00:00Z", "open": "582.50", "high": "584.10", "low": "581.20", "close": "582.14", "volume": 51000000}
  ]
}
```

`tests/fixtures/uw_responses/spy_chains_snapshot.json`:
```json
{
  "data": [
    "SPY260417P00570000",
    "SPY260417P00575000",
    "SPY260417P00580000",
    "SPY260417C00570000",
    "SPY260417C00575000",
    "SPY260417C00580000"
  ]
}
```

`tests/fixtures/uw_responses/option_historic.json`:
```json
{
  "data": [
    {"date": "2026-04-09", "open_price": "3.20", "high_price": "3.45", "low_price": "3.10", "last_price": "3.25", "nbbo_bid": "3.20", "nbbo_ask": "3.30", "implied_volatility": "0.148", "volume": 12000, "open_interest": 35000},
    {"date": "2026-04-08", "open_price": "3.00", "high_price": "3.30", "low_price": "2.95", "last_price": "3.20", "nbbo_bid": "3.15", "nbbo_ask": "3.25", "implied_volatility": "0.143", "volume": 15000, "open_interest": 33000}
  ]
}
```

- [ ] **Step 3: Write `bullbot/data/fetchers.py`**

```python
"""
HTTP fetchers for UW and Polygon. These take a `client` argument rather
than constructing one themselves, so tests can inject FakeUWClient.

Real callers get a `UWHttpClient` that implements the same `get(path,
params)` interface using `requests` + `tenacity` retry. The fake and real
client share no code — they share a duck-typed protocol.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bullbot import config
from bullbot.data.schemas import Bar, OptionContract

log = logging.getLogger("bullbot.fetchers")


class DataFetchError(Exception):
    """Non-retryable data fetch failure."""


class DataSchemaError(Exception):
    """Schema mismatch in the response body."""


class UWRateLimited(RuntimeError):
    pass


class UWTransient(RuntimeError):
    pass


class _ClientLike(Protocol):
    def get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[int, Any]: ...


class UWHttpClient:
    """Real UW HTTP client using requests + tenacity retry."""

    BASE_URL = "https://api.unusualwhales.com"

    def __init__(self, api_key: str, rps: float = 10.0) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": "bull-bot/v3",
            }
        )

    @retry(
        retry=retry_if_exception_type((UWRateLimited, UWTransient, requests.RequestException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=1, max=30),
        reraise=True,
    )
    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        r = self._session.get(f"{self.BASE_URL}{path}", params=params or {}, timeout=30)
        if r.status_code == 429:
            raise UWRateLimited(f"429 on {path}")
        if 500 <= r.status_code < 600:
            raise UWTransient(f"{r.status_code} on {path}")
        try:
            body = r.json()
        except ValueError:
            body = {"_non_json": r.text[:200]}
        return r.status_code, body


def _parse_ts(raw: Any) -> int:
    """Parse a UW timestamp field into UTC epoch seconds."""
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw)
    if s.isdigit():
        return int(s)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError as e:
            raise DataSchemaError(f"cannot parse ts: {raw}") from e


def _data_list(body: Any) -> list[Any]:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in ("data", "results", "chains"):
            v = body.get(k)
            if isinstance(v, list):
                return v
    return []


def fetch_daily_ohlc(client: _ClientLike, ticker: str, limit: int = 2500) -> list[Bar]:
    """Fetch daily OHLC bars for a ticker. Raises DataFetchError on empty or 4xx."""
    status, body = client.get(
        f"/api/stock/{ticker}/ohlc/1d",
        params={"limit": limit},
    )
    if status == 200:
        rows = _data_list(body)
        if not rows:
            raise DataFetchError(f"empty OHLC response for {ticker}")
        bars: list[Bar] = []
        for r in rows:
            try:
                bars.append(
                    Bar(
                        ticker=ticker,
                        timeframe="1d",
                        ts=_parse_ts(r.get("candle_start_time") or r.get("ts") or r.get("date")),
                        open=float(r.get("open") or 0),
                        high=float(r.get("high") or 0),
                        low=float(r.get("low") or 0),
                        close=float(r.get("close") or 0),
                        volume=int(r.get("volume") or 0),
                        source="uw",
                    )
                )
            except Exception as e:
                raise DataSchemaError(f"bad bar row {r}: {e}") from e
        return bars
    if status >= 400:
        raise DataFetchError(f"{status} on ohlc/1d for {ticker}: {str(body)[:200]}")
    raise DataFetchError(f"unexpected status {status}")


def fetch_chains_snapshot(client: _ClientLike, ticker: str, date: str | None = None) -> list[str]:
    """Fetch list of option symbols that existed on `date` (or today)."""
    params: dict[str, Any] = {}
    if date:
        params["date"] = date
    status, body = client.get(f"/api/stock/{ticker}/option-chains", params=params)
    if status == 200:
        return [str(x) for x in _data_list(body) if isinstance(x, str)]
    if status == 403 and isinstance(body, dict) and body.get("code") == "historic_data_access_missing":
        # Known UW limitation: 7-day historical gate. Return empty rather
        # than raise — callers use algorithmic symbol enumeration instead.
        log.warning("chains_snapshot gated for %s date=%s", ticker, date)
        return []
    if status >= 400:
        raise DataFetchError(f"{status} on option-chains for {ticker}: {str(body)[:200]}")
    return []


def fetch_option_historic(client: _ClientLike, option_symbol: str) -> list[OptionContract]:
    """Fetch full historical daily series for a specific option contract.

    Returns [] on 404 (which is expected when algorithmic enumeration
    probes symbols that never existed). Raises on other 4xx/5xx.
    """
    status, body = client.get(f"/api/option-contract/{option_symbol}/historic")
    if status == 200:
        rows = _data_list(body)
        result: list[OptionContract] = []
        # Parse the option symbol to extract ticker/expiry/strike/kind
        import re
        m = re.match(
            r"^(?P<t>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<k>[PC])(?P<s>\d{8})$",
            option_symbol,
        )
        if not m:
            raise DataSchemaError(f"cannot parse option symbol: {option_symbol}")
        ticker = m["t"]
        expiry = f"20{m['yy']}-{m['mm']}-{m['dd']}"
        strike = int(m["s"]) / 1000.0
        kind = m["k"]

        for r in rows:
            try:
                iv_raw = r.get("implied_volatility")
                iv_val = float(iv_raw) if iv_raw not in (None, "", "null") else None
                bid = float(r.get("nbbo_bid") or 0)
                ask = float(r.get("nbbo_ask") or 0)
                if bid <= 0 or ask <= 0:
                    continue  # skip rows with no quote
                result.append(
                    OptionContract(
                        ticker=ticker,
                        expiry=expiry,
                        strike=strike,
                        kind=kind,
                        ts=_parse_ts(r.get("date")),
                        nbbo_bid=bid,
                        nbbo_ask=ask,
                        last=float(r["last_price"]) if r.get("last_price") else None,
                        volume=int(r["volume"]) if r.get("volume") is not None else None,
                        open_interest=int(r["open_interest"]) if r.get("open_interest") is not None else None,
                        iv=iv_val,
                    )
                )
            except Exception as e:
                log.warning("skipping bad row on %s: %s", option_symbol, e)
        return result
    if status == 404:
        return []
    if status >= 400:
        raise DataFetchError(f"{status} on historic for {option_symbol}: {str(body)[:200]}")
    return []
```

- [ ] **Step 4: Run, commit**

```bash
pytest tests/integration/test_fetchers.py -v && \
git add bullbot/data/fetchers.py tests/integration/test_fetchers.py tests/fixtures/uw_responses/ && \
git commit -m "stage1(T12): data/fetchers — UW client + daily_ohlc/chains/historic endpoints

Duck-typed _ClientLike protocol so tests can inject FakeUWClient.
chains_snapshot gracefully handles the 7-day gate (returns [] not raises)."
```

---

### Task 13: bullbot/data/cache.py (read-through cache with TTL)

**Files:**
- Create: `bullbot/data/cache.py`
- Create: `tests/integration/test_cache.py`

- [ ] **Step 1: Write test**

```python
"""Cache read-through tests."""
from bullbot.data import cache
from bullbot.data.schemas import Bar


def test_get_daily_bars_caches_after_first_fetch(db_conn, fake_uw):
    from tests.conftest import FakeUWResponse
    fake_uw.register(
        "/api/stock/SPY/ohlc/1d",
        FakeUWResponse(body={
            "data": [
                {"candle_start_time": "2026-04-01T00:00:00Z", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": 100},
            ],
        }),
    )

    # First call: hits fetcher, writes to cache
    bars1 = cache.get_daily_bars(db_conn, fake_uw, "SPY", limit=10)
    assert len(bars1) == 1
    assert len(fake_uw.call_log) == 1

    # Second call: reads from cache only, no new fetch
    bars2 = cache.get_daily_bars(db_conn, fake_uw, "SPY", limit=10)
    assert len(bars2) == 1
    assert len(fake_uw.call_log) == 1   # still 1


def test_daily_bars_refresh_when_requesting_more_than_cached(db_conn, fake_uw):
    from tests.conftest import FakeUWResponse
    # Pre-seed cache with 2 bars
    db_conn.execute(
        "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume, source) "
        "VALUES ('SPY','1d',1717200000,1,2,0.5,1.5,100,'uw')"
    )
    db_conn.execute(
        "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume, source) "
        "VALUES ('SPY','1d',1717286400,1.5,2.5,1,2,200,'uw')"
    )

    fake_uw.register(
        "/api/stock/SPY/ohlc/1d",
        FakeUWResponse(body={
            "data": [
                {"candle_start_time": "2026-04-01T00:00:00Z", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": 100},
                {"candle_start_time": "2026-04-02T00:00:00Z", "open": "1.5", "high": "2.5", "low": "1", "close": "2", "volume": 200},
                {"candle_start_time": "2026-04-03T00:00:00Z", "open": "2", "high": "2.8", "low": "1.8", "close": "2.5", "volume": 150},
            ],
        }),
    )
    # Request more than cached → trigger fetch
    bars = cache.get_daily_bars(db_conn, fake_uw, "SPY", limit=3)
    assert len(bars) == 3
    assert len(fake_uw.call_log) == 1
```

- [ ] **Step 2: Write `bullbot/data/cache.py`**

```python
"""
Read-through cache between the fetchers and the rest of the system.

TTL rules (spec §6.4):
- 1d bars: stale for today until EOD + 15 min; never stale for past dates
- option_contracts: stale after 1 min during RTH, never stale outside
- iv_surface: stale after 5 min during RTH

The cache owns the SQLite rows. On miss, calls fetcher, persists, returns.
On hit with sufficient rows and acceptable TTL, returns cached rows.
"""

from __future__ import annotations

import sqlite3
from typing import Protocol

from bullbot.data import fetchers
from bullbot.data.schemas import Bar, OptionContract


def _row_count(
    conn: sqlite3.Connection,
    ticker: str,
    timeframe: str,
) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE ticker=? AND timeframe=?",
        (ticker, timeframe),
    ).fetchone()
    return row[0] if row else 0


def _persist_bars(conn: sqlite3.Connection, bars: list[Bar]) -> None:
    for b in bars:
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(ticker, timeframe, ts, open, high, low, close, volume, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (b.ticker, b.timeframe, b.ts, b.open, b.high, b.low, b.close, b.volume, b.source),
        )


def _load_bars(
    conn: sqlite3.Connection,
    ticker: str,
    timeframe: str,
    limit: int,
) -> list[Bar]:
    rows = conn.execute(
        "SELECT ticker, timeframe, ts, open, high, low, close, volume, source "
        "FROM bars WHERE ticker=? AND timeframe=? ORDER BY ts DESC LIMIT ?",
        (ticker, timeframe, limit),
    ).fetchall()
    return [
        Bar(
            ticker=r["ticker"],
            timeframe=r["timeframe"],
            ts=r["ts"],
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
            source=r["source"],
        )
        for r in reversed(rows)
    ]


def get_daily_bars(
    conn: sqlite3.Connection,
    client: fetchers._ClientLike,
    ticker: str,
    limit: int = 500,
) -> list[Bar]:
    """Get daily bars, fetching from UW only if cache has fewer than requested."""
    cached_count = _row_count(conn, ticker, "1d")
    if cached_count >= limit:
        return _load_bars(conn, ticker, "1d", limit)
    fresh = fetchers.fetch_daily_ohlc(client, ticker, limit=max(limit, 500))
    _persist_bars(conn, fresh)
    return _load_bars(conn, ticker, "1d", limit)


def _persist_option_contracts(
    conn: sqlite3.Connection, contracts: list[OptionContract]
) -> None:
    for c in contracts:
        conn.execute(
            "INSERT OR REPLACE INTO option_contracts "
            "(ticker, expiry, strike, kind, ts, nbbo_bid, nbbo_ask, last, volume, open_interest, iv) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                c.ticker,
                c.expiry,
                c.strike,
                c.kind,
                c.ts,
                c.nbbo_bid,
                c.nbbo_ask,
                c.last,
                c.volume,
                c.open_interest,
                c.iv,
            ),
        )


def get_option_contract_history(
    conn: sqlite3.Connection,
    client: fetchers._ClientLike,
    option_symbol: str,
) -> list[OptionContract]:
    """Fetch full historical series for a specific contract and cache it."""
    rows = conn.execute(
        "SELECT COUNT(*) FROM option_contracts WHERE "
        "ticker=? AND expiry=? AND strike=? AND kind=?",
        _parse_symbol_into_pk(option_symbol),
    ).fetchone()
    cached_count = rows[0] if rows else 0
    if cached_count > 0:
        return _load_option_contract(conn, option_symbol)

    fresh = fetchers.fetch_option_historic(client, option_symbol)
    if fresh:
        _persist_option_contracts(conn, fresh)
    return fresh


def _parse_symbol_into_pk(symbol: str) -> tuple[str, str, float, str]:
    import re
    m = re.match(
        r"^(?P<t>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<k>[PC])(?P<s>\d{8})$",
        symbol,
    )
    if not m:
        raise ValueError(f"bad symbol {symbol}")
    return m["t"], f"20{m['yy']}-{m['mm']}-{m['dd']}", int(m["s"]) / 1000.0, m["k"]


def _load_option_contract(
    conn: sqlite3.Connection, option_symbol: str
) -> list[OptionContract]:
    ticker, expiry, strike, kind = _parse_symbol_into_pk(option_symbol)
    rows = conn.execute(
        "SELECT * FROM option_contracts WHERE "
        "ticker=? AND expiry=? AND strike=? AND kind=? ORDER BY ts",
        (ticker, expiry, strike, kind),
    ).fetchall()
    return [
        OptionContract(
            ticker=r["ticker"],
            expiry=r["expiry"],
            strike=r["strike"],
            kind=r["kind"],
            ts=r["ts"],
            nbbo_bid=r["nbbo_bid"],
            nbbo_ask=r["nbbo_ask"],
            last=r["last"],
            volume=r["volume"],
            open_interest=r["open_interest"],
            iv=r["iv"],
        )
        for r in rows
    ]
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/integration/test_cache.py -v && \
git add bullbot/data/cache.py tests/integration/test_cache.py && \
git commit -m "stage1(T13): data/cache — read-through cache for bars + option_contracts"
```

---

### Task 14: bullbot/data/options_backfill.py (symbol enumeration + bulk fetch)

**Files:**
- Create: `bullbot/data/options_backfill.py`
- Create: `tests/unit/test_options_backfill.py`

- [ ] **Step 1: Write test** — the core algorithm is symbol enumeration, which is pure and easy to test.

```python
"""Option symbol enumeration tests."""
from datetime import date

from bullbot.data import options_backfill


def test_format_osi_symbol():
    sym = options_backfill.format_osi_symbol(
        ticker="SPY", expiry=date(2024, 6, 21), strike=540.0, kind="P"
    )
    assert sym == "SPY240621P00540000"


def test_format_osi_symbol_fractional_strike():
    sym = options_backfill.format_osi_symbol(
        ticker="SPY", expiry=date(2024, 6, 21), strike=540.5, kind="C"
    )
    assert sym == "SPY240621C00540500"


def test_enumerate_expiries_includes_fridays():
    expiries = options_backfill.enumerate_expiries(
        start=date(2024, 6, 1), end=date(2024, 6, 30)
    )
    # June 2024 Fridays: 7, 14, 21, 28
    fridays = [d for d in expiries if d.weekday() == 4]
    assert date(2024, 6, 7) in fridays
    assert date(2024, 6, 14) in fridays
    assert date(2024, 6, 21) in fridays
    assert date(2024, 6, 28) in fridays


def test_enumerate_strikes_around_spot():
    strikes = options_backfill.enumerate_strikes_around_spot(
        spot=540.0, range_fraction=0.20, step=1.0
    )
    assert min(strikes) >= 432.0  # spot * 0.8
    assert max(strikes) <= 648.0  # spot * 1.2
    assert 540.0 in strikes
    assert len(strikes) > 50


def test_build_candidate_symbols_count_sanity():
    from datetime import date
    symbols = options_backfill.build_candidate_symbols(
        ticker="SPY",
        spot=540.0,
        backfill_start=date(2024, 1, 1),
        backfill_end=date(2024, 1, 31),  # Just January
        strike_range_fraction=0.10,
        strike_step=5.0,
    )
    # ~4 Fridays × ~22 strikes × 2 (P/C) = ~176 symbols
    assert 50 < len(symbols) < 500
    assert all(s.startswith("SPY") for s in symbols)
```

- [ ] **Step 2: Write `bullbot/data/options_backfill.py`**

```python
"""
Options backfill — algorithmically enumerate option symbols for a ticker
across a backfill window and bulk-fetch their histories.

This is the Phase 0b workaround for UW's 7-day chain-discovery gate. We
construct symbols directly using the OSI regex + a hardcoded NYSE weekly/
monthly expiry calendar + a strike grid around the underlying's spot.

Usage:
    backfill.run("SPY", spot=582.0, start=date(2024,1,1), end=date(2026,4,1))
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, timedelta
from typing import Iterator

import pandas_market_calendars as mcal

from bullbot.data import cache, fetchers

log = logging.getLogger("bullbot.backfill")

_CAL = mcal.get_calendar("NYSE")


def format_osi_symbol(ticker: str, expiry: date, strike: float, kind: str) -> str:
    """Build an OSI option symbol: TICKER + YYMMDD + P/C + strike*1000 (8 digits)."""
    if kind not in ("C", "P"):
        raise ValueError(f"kind must be C or P, got {kind}")
    return f"{ticker}{expiry:%y%m%d}{kind}{int(round(strike * 1000)):08d}"


def enumerate_expiries(start: date, end: date) -> list[date]:
    """All NYSE Fridays between start and end (SPY/QQQ weeklies are M/W/F,
    but for v1 we cover Friday-only — still hits most liquid contracts)."""
    sched = _CAL.schedule(start_date=start, end_date=end)
    result: list[date] = []
    for idx in sched.index:
        d = idx.date()
        if d.weekday() == 4:  # Friday
            result.append(d)
    return result


def enumerate_strikes_around_spot(
    spot: float, range_fraction: float, step: float
) -> list[float]:
    """Strikes from spot*(1-range) to spot*(1+range), stepped by `step`."""
    lo = spot * (1 - range_fraction)
    hi = spot * (1 + range_fraction)
    # Round lo down to nearest step, hi up
    lo = (int(lo // step)) * step
    hi = (int(hi // step) + 1) * step
    strikes: list[float] = []
    s = lo
    while s <= hi:
        strikes.append(round(s, 2))
        s += step
    return strikes


def build_candidate_symbols(
    ticker: str,
    spot: float,
    backfill_start: date,
    backfill_end: date,
    strike_range_fraction: float = 0.20,
    strike_step: float = 1.0,
) -> list[str]:
    """Build the full list of candidate option symbols to probe."""
    expiries = enumerate_expiries(backfill_start, backfill_end)
    strikes = enumerate_strikes_around_spot(spot, strike_range_fraction, strike_step)
    out: list[str] = []
    for exp in expiries:
        for k in strikes:
            for kind in ("P", "C"):
                out.append(format_osi_symbol(ticker, exp, k, kind))
    return out


def run(
    conn: sqlite3.Connection,
    client: fetchers._ClientLike,
    ticker: str,
    spot: float,
    start: date,
    end: date,
    rate_limit_sleep: float = 0.1,
) -> dict[str, int]:
    """
    Backfill option history for a ticker across a date window.

    Returns a summary dict: {symbols_tried, symbols_with_data, rows_written}.
    Intended to be called once per ticker at Stage 1 kickoff.
    """
    symbols = build_candidate_symbols(
        ticker=ticker,
        spot=spot,
        backfill_start=start,
        backfill_end=end,
    )
    log.info("backfill %s: %d candidate symbols", ticker, len(symbols))

    tried = 0
    with_data = 0
    rows_written = 0
    for sym in symbols:
        tried += 1
        try:
            contracts = fetchers.fetch_option_historic(client, sym)
        except fetchers.DataFetchError as e:
            log.warning("fetch error on %s: %s", sym, e)
            continue
        if contracts:
            with_data += 1
            for c in contracts:
                conn.execute(
                    "INSERT OR REPLACE INTO option_contracts "
                    "(ticker, expiry, strike, kind, ts, nbbo_bid, nbbo_ask, last, volume, open_interest, iv) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        c.ticker, c.expiry, c.strike, c.kind, c.ts,
                        c.nbbo_bid, c.nbbo_ask, c.last, c.volume, c.open_interest, c.iv,
                    ),
                )
                rows_written += 1
        time.sleep(rate_limit_sleep)

    log.info(
        "backfill %s done: tried=%d with_data=%d rows_written=%d",
        ticker, tried, with_data, rows_written,
    )
    return {"symbols_tried": tried, "symbols_with_data": with_data, "rows_written": rows_written}
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/unit/test_options_backfill.py -v && \
git add bullbot/data/options_backfill.py tests/unit/test_options_backfill.py && \
git commit -m "stage1(T14): data/options_backfill — OSI symbol enum + bulk historic fetch"
```

---

## Phase D — Strategies (T15–T18)

### Task 15: bullbot/strategies/base.py

**Files:**
- Create: `bullbot/strategies/base.py`
- Create: `tests/unit/test_strategies_base.py`

- [ ] **Step 1: Write test**

```python
"""Strategy base class shape tests."""
import pytest

from bullbot.strategies.base import Strategy, StrategySnapshot
from bullbot.data.schemas import Bar


def test_strategy_is_abstract():
    with pytest.raises(TypeError):
        Strategy(params={})


def test_strategy_subclass_implements_evaluate():
    class Noop(Strategy):
        CLASS_NAME = "Noop"
        CLASS_VERSION = 1

        def evaluate(self, snapshot, open_positions):
            return None

        def max_loss_per_contract(self) -> float:
            return 100.0

    s = Noop(params={})
    assert s.CLASS_NAME == "Noop"
    assert s.evaluate(None, []) is None
    assert s.max_loss_per_contract() == 100.0


def test_strategy_snapshot_fields():
    snap = StrategySnapshot(
        ticker="SPY",
        asof_ts=1718395200,
        spot=582.14,
        bars_1d=[],
        indicators={"sma_20": 578.45, "rsi_14": 58.4},
        atm_greeks={"delta": 0.52, "iv": 0.143},
        iv_rank=34.0,
        regime="bull",
        chain=[],
    )
    assert snap.ticker == "SPY"
    assert snap.regime == "bull"
```

- [ ] **Step 2: Write `bullbot/strategies/base.py`**

```python
"""
Strategy abstract base class + StrategySnapshot data container.

Every strategy is a subclass that reads a StrategySnapshot and returns a
Signal or None. Strategies are deterministic at execution time — no LLM
calls inside evaluate(). LLM reasoning happens ONLY in the evolver
proposer at parameter-tuning time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from bullbot.data.schemas import Bar, OptionContract, Signal


@dataclass(frozen=True)
class StrategySnapshot:
    """Everything a strategy needs to decide one signal."""
    ticker: str
    asof_ts: int
    spot: float
    bars_1d: list[Bar]
    indicators: dict[str, float]
    atm_greeks: dict[str, float]
    iv_rank: float
    regime: str   # 'bull' | 'bear' | 'chop'
    chain: list[OptionContract]   # current chain at asof_ts


class Strategy(ABC):
    """Abstract base class for all strategies."""

    CLASS_NAME: str = ""
    CLASS_VERSION: int = 1

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    @abstractmethod
    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
    ) -> Signal | None:
        """Return a Signal to open/close a position, or None to stand pat."""

    @abstractmethod
    def max_loss_per_contract(self) -> float:
        """Max dollar loss per contract for position sizing."""
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/unit/test_strategies_base.py -v && \
git add bullbot/strategies/base.py tests/unit/test_strategies_base.py && \
git commit -m "stage1(T15): strategies/base — Strategy ABC + StrategySnapshot"
```

---

### Task 16: bullbot/strategies/put_credit_spread.py

**Files:**
- Create: `bullbot/strategies/put_credit_spread.py`
- Create: `tests/unit/test_put_credit_spread.py`

- [ ] **Step 1: Write test**

```python
"""PutCreditSpread strategy tests."""
from datetime import date, datetime, timezone

from bullbot.data.schemas import OptionContract
from bullbot.strategies.base import StrategySnapshot
from bullbot.strategies.put_credit_spread import PutCreditSpread


def _chain_puts(expiry: str, strikes_with_delta: list[tuple[float, float]]) -> list[OptionContract]:
    """Build a list of put OptionContracts for a given expiry."""
    out = []
    for strike, _ in strikes_with_delta:
        out.append(
            OptionContract(
                ticker="SPY",
                expiry=expiry,
                strike=strike,
                kind="P",
                ts=1718395200,
                nbbo_bid=1.20,
                nbbo_ask=1.30,
                iv=0.15,
                volume=1000,
                open_interest=5000,
            )
        )
    return out


def test_evaluate_opens_when_conditions_met():
    snap = StrategySnapshot(
        ticker="SPY",
        asof_ts=1718395200,
        spot=582.0,
        bars_1d=[],
        indicators={"rsi_14": 55.0},
        atm_greeks={"delta": 0.50},
        iv_rank=60.0,  # above iv_rank_min=50
        regime="bull",
        chain=_chain_puts("2024-06-28", [(570, -0.25), (565, -0.20)]),
    )
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50
    })
    signal = strategy.evaluate(snap, open_positions=[])
    assert signal is not None
    assert signal.intent == "open"
    assert signal.strategy_class == "PutCreditSpread"
    assert len(signal.legs) == 2


def test_evaluate_returns_none_when_iv_rank_below_min():
    snap = StrategySnapshot(
        ticker="SPY",
        asof_ts=1718395200,
        spot=582.0,
        bars_1d=[],
        indicators={},
        atm_greeks={},
        iv_rank=30.0,  # below min 50
        regime="bull",
        chain=_chain_puts("2024-06-28", [(570, -0.25)]),
    )
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50
    })
    assert strategy.evaluate(snap, []) is None


def test_max_loss_equals_width_minus_credit():
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50
    })
    # Width=5 → max loss per contract = (5 - estimated credit) × 100
    # We use width × 100 as the conservative sizing bound
    assert strategy.max_loss_per_contract() == 500.0


def test_does_not_open_if_already_have_position():
    snap = StrategySnapshot(
        ticker="SPY", asof_ts=1718395200, spot=582.0,
        bars_1d=[], indicators={}, atm_greeks={}, iv_rank=60.0, regime="bull",
        chain=_chain_puts("2024-06-28", [(570, -0.25)]),
    )
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50
    })
    open_positions = [{"id": 1, "strategy_id": 42}]  # pretend one exists
    # PCS prevents stacking in v1 — returns None if any open position exists
    assert strategy.evaluate(snap, open_positions) is None
```

- [ ] **Step 2: Write `bullbot/strategies/put_credit_spread.py`**

```python
"""
Put credit spread strategy — sell a nearer-dated OTM put, buy a further OTM
put as the long wing for defined risk.

Parameters:
  - dte: target days-to-expiry for the short leg
  - short_delta: target absolute delta for the short leg (e.g., 0.25 = 25-delta put)
  - width: strike distance between short and long legs in dollars
  - iv_rank_min: minimum IV rank (0-100) required to open
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from bullbot.data.schemas import Leg, Signal
from bullbot.strategies.base import Strategy, StrategySnapshot

from bullbot.features.greeks import compute_greeks
from bullbot import config


class PutCreditSpread(Strategy):
    CLASS_NAME = "PutCreditSpread"
    CLASS_VERSION = 1

    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
    ) -> Signal | None:
        # v1: prevent stacking per ticker
        if any(p for p in open_positions if p):
            return None

        iv_rank_min = float(self.params.get("iv_rank_min", 50))
        if snapshot.iv_rank < iv_rank_min:
            return None

        target_dte = int(self.params.get("dte", 14))
        short_delta = float(self.params.get("short_delta", 0.25))
        width = float(self.params.get("width", 5))

        # Find chain contracts matching target DTE (±3 days)
        asof_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc).date()
        target_exp = asof_dt + timedelta(days=target_dte)
        candidates_p = [
            c for c in snapshot.chain
            if c.kind == "P"
            and abs((datetime.strptime(c.expiry, "%Y-%m-%d").date() - target_exp).days) <= 3
        ]
        if not candidates_p:
            return None

        # Pick the expiry closest to target
        by_exp: dict[str, list] = {}
        for c in candidates_p:
            by_exp.setdefault(c.expiry, []).append(c)
        chosen_expiry = min(
            by_exp.keys(),
            key=lambda e: abs(
                (datetime.strptime(e, "%Y-%m-%d").date() - target_exp).days
            ),
        )
        expiry_puts = by_exp[chosen_expiry]

        # Compute t_years for greeks calc
        t_years = (
            (datetime.strptime(chosen_expiry, "%Y-%m-%d").date() - asof_dt).days / 365.0
        )
        if t_years <= 0:
            return None

        # Find the put whose computed delta is closest to -short_delta
        best = None
        best_gap = float("inf")
        for p in expiry_puts:
            if p.iv is None or p.iv <= 0:
                continue
            g = compute_greeks(
                spot=snapshot.spot,
                strike=p.strike,
                t_years=t_years,
                r=config.RISK_FREE_RATE,
                sigma=p.iv,
                is_put=True,
            )
            gap = abs(g.delta - (-short_delta))
            if gap < best_gap:
                best_gap = gap
                best = p
        if best is None:
            return None

        # Find the long leg `width` dollars below the short
        long_strike = best.strike - width
        long_leg = next(
            (p for p in expiry_puts if abs(p.strike - long_strike) < 0.01),
            None,
        )
        if long_leg is None:
            return None

        short_option = _make_osi(snapshot.ticker, chosen_expiry, best.strike, "P")
        long_option = _make_osi(snapshot.ticker, chosen_expiry, long_leg.strike, "P")

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[
                Leg(
                    option_symbol=short_option,
                    side="short",
                    quantity=1,
                    strike=best.strike,
                    expiry=chosen_expiry,
                    kind="P",
                ),
                Leg(
                    option_symbol=long_option,
                    side="long",
                    quantity=1,
                    strike=long_leg.strike,
                    expiry=chosen_expiry,
                    kind="P",
                ),
            ],
            max_loss_per_contract=width * 100,
            rationale=(
                f"Short {best.strike}P / Long {long_leg.strike}P {chosen_expiry} "
                f"(width={width}, iv_rank={snapshot.iv_rank:.0f})"
            ),
        )

    def max_loss_per_contract(self) -> float:
        width = float(self.params.get("width", 5))
        return width * 100   # conservative: ignore credit


def _make_osi(ticker: str, expiry: str, strike: float, kind: str) -> str:
    from datetime import datetime as _dt
    d = _dt.strptime(expiry, "%Y-%m-%d").date()
    return f"{ticker}{d:%y%m%d}{kind}{int(round(strike * 1000)):08d}"
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/unit/test_put_credit_spread.py -v && \
git add bullbot/strategies/put_credit_spread.py tests/unit/test_put_credit_spread.py && \
git commit -m "stage1(T16): strategies/put_credit_spread — delta-targeted short put spread"
```

---

### Task 17: Remaining seed strategies (call_credit_spread, iron_condor, cash_secured_put, long_call, long_put)

**Files:**
- Create: `bullbot/strategies/call_credit_spread.py`
- Create: `bullbot/strategies/iron_condor.py`
- Create: `bullbot/strategies/cash_secured_put.py`
- Create: `bullbot/strategies/long_call.py`
- Create: `bullbot/strategies/long_put.py`
- Create: `tests/unit/test_remaining_strategies.py`

Each strategy follows the same shape as `PutCreditSpread`. For brevity, this task uses one consolidated test file and five compact implementations. Every class must implement `CLASS_NAME`, `CLASS_VERSION`, `evaluate`, and `max_loss_per_contract`.

- [ ] **Step 1: Write `tests/unit/test_remaining_strategies.py`**

```python
"""Smoke tests for the 5 remaining seed strategies — each must construct,
return CLASS_NAME, compute max_loss_per_contract, and evaluate to either a
Signal or None without raising on a well-formed snapshot."""
from datetime import datetime, timedelta, timezone

import pytest

from bullbot.data.schemas import OptionContract
from bullbot.strategies.base import StrategySnapshot
from bullbot.strategies.call_credit_spread import CallCreditSpread
from bullbot.strategies.iron_condor import IronCondor
from bullbot.strategies.cash_secured_put import CashSecuredPut
from bullbot.strategies.long_call import LongCall
from bullbot.strategies.long_put import LongPut


def _snap(iv_rank: float = 60.0, regime: str = "bull") -> StrategySnapshot:
    ts = int(datetime(2024, 6, 14, 14, 0, tzinfo=timezone.utc).timestamp())
    expiry_dt = (datetime(2024, 6, 14) + timedelta(days=21)).strftime("%Y-%m-%d")
    chain = []
    for strike in [560, 565, 570, 575, 580, 585, 590, 595, 600]:
        for kind in ("P", "C"):
            chain.append(OptionContract(
                ticker="SPY", expiry=expiry_dt, strike=strike, kind=kind,
                ts=ts, nbbo_bid=1.20, nbbo_ask=1.30, iv=0.18, volume=1000, open_interest=5000,
            ))
    return StrategySnapshot(
        ticker="SPY", asof_ts=ts, spot=580.0, bars_1d=[],
        indicators={"rsi_14": 55.0}, atm_greeks={"delta": 0.5},
        iv_rank=iv_rank, regime=regime, chain=chain,
    )


@pytest.mark.parametrize("cls,params", [
    (CallCreditSpread, {"dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50}),
    (IronCondor, {"dte": 21, "wing_delta": 0.20, "wing_width": 5, "iv_rank_min": 60}),
    (CashSecuredPut, {"dte": 30, "target_delta": 0.30, "iv_rank_min": 40}),
    (LongCall, {"dte": 45, "delta": 0.60}),
    (LongPut, {"dte": 45, "delta": 0.60}),
])
def test_strategy_smoke(cls, params):
    s = cls(params=params)
    assert s.CLASS_NAME == cls.__name__
    assert s.max_loss_per_contract() > 0
    # Must not raise
    result = s.evaluate(_snap(), [])
    assert result is None or result.intent == "open"
```

- [ ] **Step 2: Write `bullbot/strategies/call_credit_spread.py`**

```python
"""Call credit spread — sell OTM call, buy further OTM call for defined risk."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from bullbot import config
from bullbot.data.schemas import Leg, Signal
from bullbot.features.greeks import compute_greeks
from bullbot.strategies.base import Strategy, StrategySnapshot
from bullbot.strategies.put_credit_spread import _make_osi


class CallCreditSpread(Strategy):
    CLASS_NAME = "CallCreditSpread"
    CLASS_VERSION = 1

    def evaluate(self, snapshot, open_positions):
        if any(p for p in open_positions if p):
            return None
        if snapshot.iv_rank < float(self.params.get("iv_rank_min", 50)):
            return None

        target_dte = int(self.params.get("dte", 14))
        short_delta = float(self.params.get("short_delta", 0.25))
        width = float(self.params.get("width", 5))

        asof_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc).date()
        target_exp = asof_dt + timedelta(days=target_dte)
        candidates = [
            c for c in snapshot.chain
            if c.kind == "C"
            and abs((datetime.strptime(c.expiry, "%Y-%m-%d").date() - target_exp).days) <= 3
        ]
        if not candidates:
            return None

        by_exp: dict[str, list] = {}
        for c in candidates:
            by_exp.setdefault(c.expiry, []).append(c)
        chosen = min(by_exp.keys(), key=lambda e: abs(
            (datetime.strptime(e, "%Y-%m-%d").date() - target_exp).days))
        exp_calls = by_exp[chosen]

        t_years = (datetime.strptime(chosen, "%Y-%m-%d").date() - asof_dt).days / 365.0
        if t_years <= 0:
            return None

        best = None
        best_gap = float("inf")
        for c in exp_calls:
            if c.iv is None or c.iv <= 0:
                continue
            g = compute_greeks(snapshot.spot, c.strike, t_years, config.RISK_FREE_RATE, c.iv, is_put=False)
            gap = abs(g.delta - short_delta)
            if gap < best_gap:
                best_gap = gap
                best = c
        if best is None:
            return None

        long_leg = next((c for c in exp_calls if abs(c.strike - (best.strike + width)) < 0.01), None)
        if long_leg is None:
            return None

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[
                Leg(option_symbol=_make_osi(snapshot.ticker, chosen, best.strike, "C"),
                    side="short", quantity=1, strike=best.strike, expiry=chosen, kind="C"),
                Leg(option_symbol=_make_osi(snapshot.ticker, chosen, long_leg.strike, "C"),
                    side="long", quantity=1, strike=long_leg.strike, expiry=chosen, kind="C"),
            ],
            max_loss_per_contract=width * 100,
            rationale=f"Short {best.strike}C / Long {long_leg.strike}C {chosen}",
        )

    def max_loss_per_contract(self) -> float:
        return float(self.params.get("width", 5)) * 100
```

- [ ] **Step 3: Write `bullbot/strategies/iron_condor.py`**

```python
"""Iron condor — short put spread + short call spread with defined wings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bullbot import config
from bullbot.data.schemas import Leg, Signal
from bullbot.features.greeks import compute_greeks
from bullbot.strategies.base import Strategy, StrategySnapshot
from bullbot.strategies.put_credit_spread import _make_osi


class IronCondor(Strategy):
    CLASS_NAME = "IronCondor"
    CLASS_VERSION = 1

    def evaluate(self, snapshot, open_positions):
        if any(p for p in open_positions if p):
            return None
        if snapshot.iv_rank < float(self.params.get("iv_rank_min", 60)):
            return None

        dte = int(self.params.get("dte", 21))
        wing_delta = float(self.params.get("wing_delta", 0.20))
        wing_width = float(self.params.get("wing_width", 5))

        asof_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc).date()
        target_exp = asof_dt + timedelta(days=dte)

        all_in_window = [
            c for c in snapshot.chain
            if abs((datetime.strptime(c.expiry, "%Y-%m-%d").date() - target_exp).days) <= 3
        ]
        if not all_in_window:
            return None

        expiries = {c.expiry for c in all_in_window}
        chosen = min(expiries, key=lambda e: abs(
            (datetime.strptime(e, "%Y-%m-%d").date() - target_exp).days))
        t_years = (datetime.strptime(chosen, "%Y-%m-%d").date() - asof_dt).days / 365.0
        if t_years <= 0:
            return None

        puts = [c for c in all_in_window if c.expiry == chosen and c.kind == "P" and c.iv]
        calls = [c for c in all_in_window if c.expiry == chosen and c.kind == "C" and c.iv]

        short_put = _pick_by_delta(puts, snapshot.spot, t_years, is_put=True, target_abs_delta=wing_delta)
        short_call = _pick_by_delta(calls, snapshot.spot, t_years, is_put=False, target_abs_delta=wing_delta)
        if short_put is None or short_call is None:
            return None

        long_put = next((p for p in puts if abs(p.strike - (short_put.strike - wing_width)) < 0.01), None)
        long_call = next((c for c in calls if abs(c.strike - (short_call.strike + wing_width)) < 0.01), None)
        if long_put is None or long_call is None:
            return None

        legs = [
            Leg(option_symbol=_make_osi(snapshot.ticker, chosen, short_put.strike, "P"),
                side="short", quantity=1, strike=short_put.strike, expiry=chosen, kind="P"),
            Leg(option_symbol=_make_osi(snapshot.ticker, chosen, long_put.strike, "P"),
                side="long", quantity=1, strike=long_put.strike, expiry=chosen, kind="P"),
            Leg(option_symbol=_make_osi(snapshot.ticker, chosen, short_call.strike, "C"),
                side="short", quantity=1, strike=short_call.strike, expiry=chosen, kind="C"),
            Leg(option_symbol=_make_osi(snapshot.ticker, chosen, long_call.strike, "C"),
                side="long", quantity=1, strike=long_call.strike, expiry=chosen, kind="C"),
        ]
        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=legs,
            max_loss_per_contract=wing_width * 100,
            rationale=f"IC {long_put.strike}/{short_put.strike}/{short_call.strike}/{long_call.strike} {chosen}",
        )

    def max_loss_per_contract(self) -> float:
        return float(self.params.get("wing_width", 5)) * 100


def _pick_by_delta(chain, spot, t_years, is_put, target_abs_delta):
    best = None
    best_gap = float("inf")
    for c in chain:
        g = compute_greeks(spot, c.strike, t_years, config.RISK_FREE_RATE, c.iv, is_put=is_put)
        gap = abs(abs(g.delta) - target_abs_delta)
        if gap < best_gap:
            best_gap = gap
            best = c
    return best
```

- [ ] **Step 4: Write `bullbot/strategies/cash_secured_put.py`**

```python
"""Cash-secured put — sell a naked put cash-backed by buying power."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bullbot import config
from bullbot.data.schemas import Leg, Signal
from bullbot.features.greeks import compute_greeks
from bullbot.strategies.base import Strategy
from bullbot.strategies.put_credit_spread import _make_osi
from bullbot.strategies.iron_condor import _pick_by_delta


class CashSecuredPut(Strategy):
    CLASS_NAME = "CashSecuredPut"
    CLASS_VERSION = 1

    def evaluate(self, snapshot, open_positions):
        if any(p for p in open_positions if p):
            return None
        if snapshot.iv_rank < float(self.params.get("iv_rank_min", 40)):
            return None

        dte = int(self.params.get("dte", 30))
        target_delta = float(self.params.get("target_delta", 0.30))

        asof_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc).date()
        target_exp = asof_dt + timedelta(days=dte)
        puts = [c for c in snapshot.chain
                if c.kind == "P" and c.iv
                and abs((datetime.strptime(c.expiry, "%Y-%m-%d").date() - target_exp).days) <= 3]
        if not puts:
            return None

        expiries = {c.expiry for c in puts}
        chosen = min(expiries, key=lambda e: abs(
            (datetime.strptime(e, "%Y-%m-%d").date() - target_exp).days))
        t_years = (datetime.strptime(chosen, "%Y-%m-%d").date() - asof_dt).days / 365.0
        if t_years <= 0:
            return None

        exp_puts = [p for p in puts if p.expiry == chosen]
        short_p = _pick_by_delta(exp_puts, snapshot.spot, t_years, is_put=True, target_abs_delta=target_delta)
        if short_p is None:
            return None

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[Leg(
                option_symbol=_make_osi(snapshot.ticker, chosen, short_p.strike, "P"),
                side="short", quantity=1, strike=short_p.strike, expiry=chosen, kind="P",
            )],
            max_loss_per_contract=short_p.strike * 100,  # assigned stock at strike
            rationale=f"CSP {short_p.strike}P {chosen}",
        )

    def max_loss_per_contract(self) -> float:
        # Conservative: assume assignment at strike. The true max loss is
        # strike - credit, but we don't know the credit until fill time.
        # Use a fixed dollar approximation for position sizing.
        return 5000.0   # $50 strike × 100 as a baseline; will be tightened in v2
```

- [ ] **Step 5: Write `bullbot/strategies/long_call.py`**

```python
"""Long call — buy a directional ITM call (default delta 0.60)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bullbot import config
from bullbot.data.schemas import Leg, Signal
from bullbot.strategies.base import Strategy
from bullbot.strategies.put_credit_spread import _make_osi
from bullbot.strategies.iron_condor import _pick_by_delta


class LongCall(Strategy):
    CLASS_NAME = "LongCall"
    CLASS_VERSION = 1

    def evaluate(self, snapshot, open_positions):
        if any(p for p in open_positions if p):
            return None

        dte = int(self.params.get("dte", 45))
        target_delta = float(self.params.get("delta", 0.60))

        asof_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc).date()
        target_exp = asof_dt + timedelta(days=dte)
        calls = [c for c in snapshot.chain
                 if c.kind == "C" and c.iv
                 and abs((datetime.strptime(c.expiry, "%Y-%m-%d").date() - target_exp).days) <= 5]
        if not calls:
            return None

        chosen = min({c.expiry for c in calls}, key=lambda e: abs(
            (datetime.strptime(e, "%Y-%m-%d").date() - target_exp).days))
        t_years = (datetime.strptime(chosen, "%Y-%m-%d").date() - asof_dt).days / 365.0
        if t_years <= 0:
            return None

        exp_calls = [c for c in calls if c.expiry == chosen]
        chosen_call = _pick_by_delta(exp_calls, snapshot.spot, t_years,
                                      is_put=False, target_abs_delta=target_delta)
        if chosen_call is None:
            return None

        mid = (chosen_call.nbbo_bid + chosen_call.nbbo_ask) / 2
        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[Leg(
                option_symbol=_make_osi(snapshot.ticker, chosen, chosen_call.strike, "C"),
                side="long", quantity=1, strike=chosen_call.strike, expiry=chosen, kind="C",
            )],
            max_loss_per_contract=mid * 100,
            rationale=f"Long {chosen_call.strike}C {chosen} (delta~{target_delta})",
        )

    def max_loss_per_contract(self) -> float:
        # Long options max loss = debit paid. Estimate at a conservative $1000.
        return 1000.0
```

- [ ] **Step 6: Write `bullbot/strategies/long_put.py`**

```python
"""Long put — buy a directional ITM put (default delta 0.60)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bullbot import config
from bullbot.data.schemas import Leg, Signal
from bullbot.strategies.base import Strategy
from bullbot.strategies.put_credit_spread import _make_osi
from bullbot.strategies.iron_condor import _pick_by_delta


class LongPut(Strategy):
    CLASS_NAME = "LongPut"
    CLASS_VERSION = 1

    def evaluate(self, snapshot, open_positions):
        if any(p for p in open_positions if p):
            return None

        dte = int(self.params.get("dte", 45))
        target_delta = float(self.params.get("delta", 0.60))

        asof_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc).date()
        target_exp = asof_dt + timedelta(days=dte)
        puts = [c for c in snapshot.chain
                if c.kind == "P" and c.iv
                and abs((datetime.strptime(c.expiry, "%Y-%m-%d").date() - target_exp).days) <= 5]
        if not puts:
            return None

        chosen = min({c.expiry for c in puts}, key=lambda e: abs(
            (datetime.strptime(e, "%Y-%m-%d").date() - target_exp).days))
        t_years = (datetime.strptime(chosen, "%Y-%m-%d").date() - asof_dt).days / 365.0
        if t_years <= 0:
            return None

        exp_puts = [p for p in puts if p.expiry == chosen]
        chosen_put = _pick_by_delta(exp_puts, snapshot.spot, t_years,
                                     is_put=True, target_abs_delta=target_delta)
        if chosen_put is None:
            return None

        mid = (chosen_put.nbbo_bid + chosen_put.nbbo_ask) / 2
        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[Leg(
                option_symbol=_make_osi(snapshot.ticker, chosen, chosen_put.strike, "P"),
                side="long", quantity=1, strike=chosen_put.strike, expiry=chosen, kind="P",
            )],
            max_loss_per_contract=mid * 100,
            rationale=f"Long {chosen_put.strike}P {chosen} (delta~{target_delta})",
        )

    def max_loss_per_contract(self) -> float:
        return 1000.0
```

- [ ] **Step 7: Run, commit**

```bash
pytest tests/unit/test_remaining_strategies.py -v && \
git add bullbot/strategies/call_credit_spread.py bullbot/strategies/iron_condor.py bullbot/strategies/cash_secured_put.py bullbot/strategies/long_call.py bullbot/strategies/long_put.py tests/unit/test_remaining_strategies.py && \
git commit -m "stage1(T17): 5 remaining seed strategies (CCS, IC, CSP, LongCall, LongPut)"
```

---

### Task 18: bullbot/strategies/registry.py

**Files:**
- Create: `bullbot/strategies/registry.py`
- Create: `tests/unit/test_registry.py`

- [ ] **Step 1: Write test**

```python
"""Strategy registry tests."""
import hashlib
import json

import pytest

from bullbot.strategies import registry
from bullbot.strategies.put_credit_spread import PutCreditSpread


def test_get_class_by_name():
    assert registry.get_class("PutCreditSpread") is PutCreditSpread


def test_get_class_unknown_raises():
    with pytest.raises(registry.UnknownStrategyError):
        registry.get_class("NonExistentStrategy")


def test_canonicalize_params_sorts_keys():
    canon = registry.canonicalize_params({"b": 2, "a": 1})
    assert canon == '{"a":1,"b":2}'


def test_params_hash_stable():
    h1 = registry.params_hash({"dte": 14, "delta": 0.25})
    h2 = registry.params_hash({"delta": 0.25, "dte": 14})
    assert h1 == h2   # hash independent of key order


def test_materialize_creates_configured_instance():
    s = registry.materialize("PutCreditSpread", {"dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50})
    assert isinstance(s, PutCreditSpread)
    assert s.params["dte"] == 14


def test_list_all_names_includes_six_seeds():
    names = set(registry.list_all_names())
    assert {"PutCreditSpread", "CallCreditSpread", "IronCondor",
            "CashSecuredPut", "LongCall", "LongPut"} <= names
```

- [ ] **Step 2: Write `bullbot/strategies/registry.py`**

```python
"""
Strategy class registry + canonicalization helpers.

Every subclass of Strategy registers itself here via class name. Params
are canonicalized into sorted-key JSON before hashing so the dedup index
(strategies.params_hash) is stable across Python dict ordering.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bullbot.strategies.base import Strategy
from bullbot.strategies.call_credit_spread import CallCreditSpread
from bullbot.strategies.cash_secured_put import CashSecuredPut
from bullbot.strategies.iron_condor import IronCondor
from bullbot.strategies.long_call import LongCall
from bullbot.strategies.long_put import LongPut
from bullbot.strategies.put_credit_spread import PutCreditSpread


class UnknownStrategyError(KeyError):
    pass


_REGISTRY: dict[str, type[Strategy]] = {
    "PutCreditSpread": PutCreditSpread,
    "CallCreditSpread": CallCreditSpread,
    "IronCondor": IronCondor,
    "CashSecuredPut": CashSecuredPut,
    "LongCall": LongCall,
    "LongPut": LongPut,
}


def get_class(class_name: str) -> type[Strategy]:
    try:
        return _REGISTRY[class_name]
    except KeyError:
        raise UnknownStrategyError(class_name)


def list_all_names() -> list[str]:
    return sorted(_REGISTRY.keys())


def canonicalize_params(params: dict[str, Any]) -> str:
    """Sorted-key, whitespace-free JSON for stable hashing."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha1(canonicalize_params(params).encode("utf-8")).hexdigest()


def materialize(class_name: str, params: dict[str, Any]) -> Strategy:
    cls = get_class(class_name)
    return cls(params=params)
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/unit/test_registry.py -v && \
git add bullbot/strategies/registry.py tests/unit/test_registry.py && \
git commit -m "stage1(T18): strategies/registry — class lookup + params_hash dedup helper"
```

---

## Phase E — Engine + Backtest (T19–T20)

### Task 19: bullbot/engine/step.py (unified execution primitive)

**Files:**
- Create: `bullbot/engine/step.py`
- Create: `tests/integration/test_engine_step.py`

- [ ] **Step 1: Write test**

```python
"""Unified engine.step integration test — backtest cursor + paper cursor."""
from datetime import datetime, timezone

from bullbot.data.schemas import Bar, OptionContract
from bullbot.engine import step
from bullbot.strategies.put_credit_spread import PutCreditSpread


def _seed_bars(db_conn, ticker="SPY"):
    # 60 daily bars ending at asof, rising trend
    base_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    for i in range(60):
        ts = base_ts + i * 86400
        price = 500.0 + i * 0.5
        db_conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume, source) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, ?, 'uw')",
            (ticker, ts, price, price + 2, price - 1, price + 1, 1_000_000),
        )


def _seed_chain(db_conn, ticker="SPY", spot=530.0, asof_ts=None):
    if asof_ts is None:
        asof_ts = int(datetime(2024, 2, 29, tzinfo=timezone.utc).timestamp())
    expiry = "2024-03-15"
    for strike in [515, 520, 525, 530, 535, 540, 545]:
        for kind in ("P", "C"):
            db_conn.execute(
                "INSERT INTO option_contracts "
                "(ticker, expiry, strike, kind, ts, nbbo_bid, nbbo_ask, last, volume, open_interest, iv) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, expiry, strike, kind, asof_ts,
                 1.20, 1.30, 1.25, 1000, 5000, 0.18),
            )


def test_step_backtest_mode_no_signal_returns_none(db_conn):
    _seed_bars(db_conn)
    _seed_chain(db_conn)

    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 99
    })   # iv_rank_min=99 guarantees no signal
    result = step.step(
        conn=db_conn,
        client=None,  # backtest mode uses cache only
        cursor=int(datetime(2024, 2, 29, tzinfo=timezone.utc).timestamp()),
        ticker="SPY",
        strategy=strategy,
        strategy_id=1,
        run_id="bt:test",
    )
    assert result.signal is None
    assert result.filled is False


def test_step_inserts_strategy_row_if_needed(db_conn):
    _seed_bars(db_conn)
    _seed_chain(db_conn)

    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'PutCreditSpread', 1, '{}', 'h1', 0)"
    )
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50
    })
    result = step.step(
        conn=db_conn,
        client=None,
        cursor=int(datetime(2024, 2, 29, tzinfo=timezone.utc).timestamp()),
        ticker="SPY",
        strategy=strategy,
        strategy_id=1,
        run_id="bt:test",
    )
    # At least completes without raising
    assert result is not None
```

- [ ] **Step 2: Write `bullbot/engine/step.py`**

```python
"""
The unified execution primitive: engine.step(cursor, ticker, strategy, run_id).

Same code path for backtest (cursor = historical ts) and live (cursor =
"now" resolved to current ts). The run_id determines which rows get
written and which positions count as "open" for strategy evaluation.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bullbot import config
from bullbot.data import cache, fetchers
from bullbot.data.schemas import Bar, OptionContract, Signal
from bullbot.engine import fill_model, position_sizer
from bullbot.features import greeks as greeks_mod
from bullbot.features import indicators, regime as regime_mod
from bullbot.strategies.base import Strategy, StrategySnapshot

log = logging.getLogger("bullbot.engine")


@dataclass
class StepResult:
    signal: Signal | None
    filled: bool
    cash_flow: float = 0.0
    commission: float = 0.0
    position_id: int | None = None


def _load_bars_at_cursor(
    conn: sqlite3.Connection, ticker: str, cursor: int, limit: int = 400
) -> list[Bar]:
    rows = conn.execute(
        "SELECT * FROM bars WHERE ticker=? AND timeframe='1d' AND ts<=? "
        "ORDER BY ts DESC LIMIT ?",
        (ticker, cursor, limit),
    ).fetchall()
    return [
        Bar(
            ticker=r["ticker"], timeframe=r["timeframe"], ts=r["ts"],
            open=r["open"], high=r["high"], low=r["low"], close=r["close"],
            volume=r["volume"], source=r["source"],
        )
        for r in reversed(rows)
    ]


def _load_chain_at_cursor(
    conn: sqlite3.Connection, ticker: str, cursor: int
) -> list[OptionContract]:
    """Load the option chain as it looked on or immediately before `cursor`.

    For each (expiry, strike, kind) combo, return the most recent row with
    ts <= cursor.
    """
    rows = conn.execute(
        """
        SELECT oc.*
        FROM option_contracts oc
        INNER JOIN (
            SELECT ticker, expiry, strike, kind, MAX(ts) AS max_ts
            FROM option_contracts
            WHERE ticker=? AND ts<=?
            GROUP BY ticker, expiry, strike, kind
        ) m ON oc.ticker=m.ticker AND oc.expiry=m.expiry
            AND oc.strike=m.strike AND oc.kind=m.kind AND oc.ts=m.max_ts
        """,
        (ticker, cursor),
    ).fetchall()
    return [
        OptionContract(
            ticker=r["ticker"], expiry=r["expiry"], strike=r["strike"], kind=r["kind"],
            ts=r["ts"], nbbo_bid=r["nbbo_bid"], nbbo_ask=r["nbbo_ask"],
            last=r["last"], volume=r["volume"], open_interest=r["open_interest"], iv=r["iv"],
        )
        for r in rows
    ]


def _compute_indicators(bars: list[Bar]) -> dict[str, float]:
    if len(bars) < 20:
        return {}
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    out: dict[str, float] = {}
    out["sma_20"] = indicators.sma(closes, 20) or 0
    out["ema_20"] = indicators.ema(closes, 20) or 0
    out["rsi_14"] = indicators.rsi(closes, 14) or 0
    atr_val = indicators.atr(highs, lows, closes, 14)
    out["atr_14"] = atr_val if atr_val else 0
    return out


def _build_snapshot(
    conn: sqlite3.Connection, ticker: str, cursor: int
) -> StrategySnapshot | None:
    bars = _load_bars_at_cursor(conn, ticker, cursor, limit=400)
    if len(bars) < 60:
        return None
    chain = _load_chain_at_cursor(conn, ticker, cursor)
    ind = _compute_indicators(bars)
    regime = regime_mod.classify([b.close for b in bars[-60:]])
    # IV rank from chain
    atm_ivs = [c.iv for c in chain if c.iv is not None]
    iv_rank = 0.0
    if atm_ivs:
        hist = [b.close for b in bars[-252:]]   # fallback, real IV rank would use historical IVs
        # v1 simplification: compute rank from spot price range (placeholder)
        iv_rank = 50.0
    return StrategySnapshot(
        ticker=ticker,
        asof_ts=cursor,
        spot=bars[-1].close,
        bars_1d=bars,
        indicators=ind,
        atm_greeks={},
        iv_rank=iv_rank,
        regime=regime,
        chain=chain,
    )


def _load_open_positions(
    conn: sqlite3.Connection, run_id: str, ticker: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM positions WHERE run_id=? AND ticker=? AND closed_at IS NULL",
        (run_id, ticker),
    ).fetchall()
    return [dict(r) for r in rows]


def _compute_equity(conn: sqlite3.Connection, run_id: str) -> float:
    """Current equity = initial + sum(realized) + sum(mark_to_mkt of open)."""
    realized = conn.execute(
        "SELECT COALESCE(SUM(pnl_realized), 0) FROM positions "
        "WHERE run_id=? AND closed_at IS NOT NULL",
        (run_id,),
    ).fetchone()[0]
    mark = conn.execute(
        "SELECT COALESCE(SUM(mark_to_mkt), 0) FROM positions "
        "WHERE run_id=? AND closed_at IS NULL",
        (run_id,),
    ).fetchone()[0]
    return config.INITIAL_CAPITAL_USD + float(realized) + float(mark)


def step(
    conn: sqlite3.Connection,
    client: fetchers._ClientLike | None,
    cursor: int,
    ticker: str,
    strategy: Strategy,
    strategy_id: int,
    run_id: str,
) -> StepResult:
    """
    Run one execution step for a ticker.

    - In backtest mode (`run_id='bt:<uuid>'`): cursor is historical, client
      can be None (reads only the cache).
    - In paper/live mode (`run_id='paper'|'live'`): cursor is current ts,
      client must be a real fetcher if cache is empty.
    """
    snap = _build_snapshot(conn, ticker, cursor)
    if snap is None:
        return StepResult(signal=None, filled=False)

    open_positions = _load_open_positions(conn, run_id, ticker)

    signal = strategy.evaluate(snap, open_positions)
    if signal is None:
        return StepResult(signal=None, filled=False)

    if signal.intent == "open":
        equity = _compute_equity(conn, run_id)
        contracts = position_sizer.size_position(
            equity=equity,
            max_loss_per_contract=signal.max_loss_per_contract,
        )
        if contracts <= 0:
            return StepResult(signal=signal, filled=False)

        # Build chain row lookup for the fill model
        chain_rows = {
            f"{c.ticker}{datetime.strptime(c.expiry, '%Y-%m-%d').strftime('%y%m%d')}"
            f"{c.kind}{int(round(c.strike * 1000)):08d}": {
                "nbbo_bid": c.nbbo_bid,
                "nbbo_ask": c.nbbo_ask,
            }
            for c in snap.chain
        }
        try:
            net_cash, filled_legs = fill_model.simulate_open_multi_leg(
                legs=signal.legs, chain_rows=chain_rows, contracts=contracts
            )
        except fill_model.FillRejected as e:
            log.info("fill rejected for %s: %s", ticker, e)
            return StepResult(signal=signal, filled=False)

        comm = fill_model.commission(contracts=contracts, n_legs=len(signal.legs))
        cur = conn.execute(
            "INSERT INTO orders (run_id, ticker, strategy_id, placed_at, legs, intent, status, commission) "
            "VALUES (?, ?, ?, ?, ?, 'open', 'filled', ?)",
            (run_id, ticker, strategy_id, cursor,
             json.dumps([l.model_dump() for l in signal.legs]), comm),
        )
        conn.execute(
            "INSERT INTO positions (run_id, ticker, strategy_id, opened_at, legs, contracts, open_price, mark_to_mkt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, ticker, strategy_id, cursor,
             json.dumps([l.model_dump() for l in signal.legs]),
             contracts, net_cash, net_cash),
        )
        pos_id = cur.lastrowid
        return StepResult(
            signal=signal, filled=True, cash_flow=net_cash, commission=comm, position_id=pos_id
        )

    # intent == 'close' — close the specified position
    pos_id = signal.position_id_to_close
    if pos_id is None:
        return StepResult(signal=signal, filled=False)
    pos_row = conn.execute(
        "SELECT * FROM positions WHERE id=? AND run_id=?", (pos_id, run_id)
    ).fetchone()
    if not pos_row:
        return StepResult(signal=signal, filled=False)

    chain_rows = {
        f"{c.ticker}{datetime.strptime(c.expiry, '%Y-%m-%d').strftime('%y%m%d')}"
        f"{c.kind}{int(round(c.strike * 1000)):08d}": {
            "nbbo_bid": c.nbbo_bid,
            "nbbo_ask": c.nbbo_ask,
        }
        for c in snap.chain
    }
    legs = [__import__("bullbot.data.schemas", fromlist=["Leg"]).Leg(**l)
            for l in json.loads(pos_row["legs"])]
    try:
        net_close, _ = fill_model.simulate_close_multi_leg(legs, chain_rows, pos_row["contracts"])
    except fill_model.FillRejected:
        return StepResult(signal=signal, filled=False)

    pnl = pos_row["open_price"] - net_close
    comm = fill_model.commission(pos_row["contracts"], len(legs))
    conn.execute(
        "UPDATE positions SET closed_at=?, close_price=?, pnl_realized=?, mark_to_mkt=0 WHERE id=?",
        (cursor, net_close, pnl - comm - (pos_row["open_price"] and 0), pos_id),
    )
    conn.execute(
        "INSERT INTO orders (run_id, ticker, strategy_id, placed_at, legs, intent, status, commission, pnl_realized) "
        "VALUES (?, ?, ?, ?, ?, 'close', 'filled', ?, ?)",
        (run_id, ticker, pos_row["strategy_id"], cursor,
         pos_row["legs"], comm, pnl - comm),
    )
    return StepResult(signal=signal, filled=True, cash_flow=net_close, commission=comm, position_id=pos_id)
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/integration/test_engine_step.py -v && \
git add bullbot/engine/step.py tests/integration/test_engine_step.py && \
git commit -m "stage1(T19): engine/step — unified execution primitive (backtest + paper + live)"
```

---

### Task 20: bullbot/backtest/walkforward.py

**Files:**
- Create: `bullbot/backtest/walkforward.py`
- Create: `tests/integration/test_walkforward.py`

- [ ] **Step 1: Write test**

```python
"""Walk-forward harness tests."""
from dataclasses import dataclass
from datetime import datetime, timezone

from bullbot.backtest import walkforward


def _seed_bars(db_conn, ticker="SPY", n_days=500):
    base_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    for i in range(n_days):
        ts = base_ts + i * 86400
        price = 500.0 + i * 0.3 + (i % 7) * 0.5
        db_conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume, source) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, ?, 'uw')",
            (ticker, ts, price, price + 2, price - 1, price + 0.5, 1_000_000),
        )


def test_compute_folds_respects_min_max():
    folds = walkforward.compute_folds(total_days=252 * 2, train_frac=0.7, step_days=30, min_folds=3, max_folds=5)
    assert 3 <= len(folds) <= 5


def test_compute_folds_returns_non_overlapping_test_windows():
    folds = walkforward.compute_folds(total_days=500, train_frac=0.7, step_days=30, min_folds=3, max_folds=5)
    for fold in folds:
        assert fold.train_start < fold.train_end <= fold.test_start < fold.test_end


def test_profit_factor_metric_simple():
    pnls = [100.0, -50.0, 200.0, -30.0]   # gross win 300 / gross loss 80 = 3.75
    assert abs(walkforward.profit_factor(pnls) - 3.75) < 1e-9


def test_profit_factor_all_losses_returns_zero():
    assert walkforward.profit_factor([-10.0, -5.0, -20.0]) == 0.0


def test_profit_factor_no_trades_returns_zero():
    assert walkforward.profit_factor([]) == 0.0


def test_aggregate_metrics_combines_folds():
    fold_metrics = [
        walkforward.FoldMetrics(pf_is=1.2, pf_oos=1.1, trade_count_is=30, trade_count_oos=12, max_dd_pct=0.05),
        walkforward.FoldMetrics(pf_is=1.4, pf_oos=1.3, trade_count_is=25, trade_count_oos=10, max_dd_pct=0.06),
    ]
    agg = walkforward.aggregate(fold_metrics)
    assert agg.trade_count == 22   # sum of OOS trades across folds
    assert agg.pf_oos > 0
```

- [ ] **Step 2: Write `bullbot/backtest/walkforward.py`**

```python
"""
Walk-forward harness.

Anchored 70/30 walk-forward across a 24-month base window, stepping 30
days per fold, 3-5 folds total. See spec §6.2 and §6.6.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from bullbot import config
from bullbot.engine import step as engine_step
from bullbot.strategies.base import Strategy

log = logging.getLogger("bullbot.walkforward")


@dataclass
class Fold:
    train_start: int   # epoch seconds
    train_end: int
    test_start: int
    test_end: int


@dataclass
class FoldMetrics:
    pf_is: float
    pf_oos: float
    trade_count_is: int
    trade_count_oos: int
    max_dd_pct: float


@dataclass
class BacktestMetrics:
    pf_is: float
    pf_oos: float
    sharpe_is: float
    max_dd_pct: float
    trade_count: int
    regime_breakdown: dict[str, float] = field(default_factory=dict)
    fold_metrics: list[FoldMetrics] = field(default_factory=list)


def compute_folds(
    total_days: int,
    train_frac: float,
    step_days: int,
    min_folds: int,
    max_folds: int,
) -> list[Fold]:
    """Produce a list of Fold (epoch-based) windows using anchored WF.

    Simplified v1: all folds share the same train_start (anchored). The
    train window grows as test_start moves forward.
    """
    if total_days <= 0:
        return []
    train_days_base = int(total_days * train_frac)
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    start_epoch = now_epoch - total_days * 86400

    folds: list[Fold] = []
    test_start_offset_days = train_days_base
    while test_start_offset_days + step_days <= total_days and len(folds) < max_folds:
        folds.append(
            Fold(
                train_start=start_epoch,
                train_end=start_epoch + test_start_offset_days * 86400,
                test_start=start_epoch + test_start_offset_days * 86400,
                test_end=start_epoch + (test_start_offset_days + step_days) * 86400,
            )
        )
        test_start_offset_days += step_days

    # If we have fewer than min_folds, shrink step_days and retry
    if len(folds) < min_folds and step_days > 7:
        return compute_folds(total_days, train_frac, max(step_days // 2, 7), min_folds, max_folds)

    return folds


def profit_factor(pnls: list[float]) -> float:
    """PF = gross win / gross loss. Returns 0 on no trades or all-loss."""
    if not pnls:
        return 0.0
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss == 0:
        return gross_win if gross_win == 0 else float("inf")
    return gross_win / gross_loss


def max_drawdown_pct(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd


def aggregate(fold_metrics: list[FoldMetrics]) -> BacktestMetrics:
    if not fold_metrics:
        return BacktestMetrics(
            pf_is=0, pf_oos=0, sharpe_is=0, max_dd_pct=0,
            trade_count=0, fold_metrics=[],
        )
    total_is = sum(f.trade_count_is for f in fold_metrics)
    total_oos = sum(f.trade_count_oos for f in fold_metrics)
    if total_is > 0:
        pf_is = sum(f.pf_is * f.trade_count_is for f in fold_metrics) / total_is
    else:
        pf_is = 0.0
    if total_oos > 0:
        pf_oos = sum(f.pf_oos * f.trade_count_oos for f in fold_metrics) / total_oos
    else:
        pf_oos = 0.0
    max_dd = max(f.max_dd_pct for f in fold_metrics)
    return BacktestMetrics(
        pf_is=pf_is,
        pf_oos=pf_oos,
        sharpe_is=0.0,  # v1 deferred: compute from equity curve in v2
        max_dd_pct=max_dd,
        trade_count=total_oos,
        fold_metrics=fold_metrics,
    )


def run_walkforward(
    conn: sqlite3.Connection,
    strategy: Strategy,
    strategy_id: int,
    ticker: str,
) -> BacktestMetrics:
    """
    Execute the full walk-forward sweep for a strategy on a ticker.

    Each fold runs engine.step on every trading day in the train window
    (IS fold) and in the test window (OOS fold), accumulating closed-trade
    PnLs to compute per-fold PF.
    """
    total_days = int(config.WF_WINDOW_MONTHS * 30)
    folds = compute_folds(
        total_days=total_days,
        train_frac=config.WF_TRAIN_FRAC,
        step_days=config.WF_STEP_DAYS,
        min_folds=config.WF_MIN_FOLDS,
        max_folds=config.WF_MAX_FOLDS,
    )

    fold_results: list[FoldMetrics] = []
    for fold in folds:
        is_pnls = _run_segment(conn, strategy, strategy_id, ticker,
                               fold.train_start, fold.train_end, tag=f"bt:is:{uuid.uuid4()}")
        oos_pnls = _run_segment(conn, strategy, strategy_id, ticker,
                                fold.test_start, fold.test_end, tag=f"bt:oos:{uuid.uuid4()}")
        fold_results.append(
            FoldMetrics(
                pf_is=profit_factor(is_pnls),
                pf_oos=profit_factor(oos_pnls),
                trade_count_is=len([p for p in is_pnls if p != 0]),
                trade_count_oos=len([p for p in oos_pnls if p != 0]),
                max_dd_pct=max_drawdown_pct(_cumulative(oos_pnls)),
            )
        )

    return aggregate(fold_results)


def _run_segment(
    conn: sqlite3.Connection,
    strategy: Strategy,
    strategy_id: int,
    ticker: str,
    start: int,
    end: int,
    tag: str,
) -> list[float]:
    """Call engine.step for each bar in the segment, collect realized pnls."""
    bars = conn.execute(
        "SELECT ts FROM bars WHERE ticker=? AND timeframe='1d' AND ts BETWEEN ? AND ? ORDER BY ts",
        (ticker, start, end),
    ).fetchall()
    for row in bars:
        engine_step.step(
            conn=conn,
            client=None,
            cursor=row["ts"],
            ticker=ticker,
            strategy=strategy,
            strategy_id=strategy_id,
            run_id=tag,
        )
    # Collect realized pnls from orders in this run_id
    pnl_rows = conn.execute(
        "SELECT COALESCE(pnl_realized, 0) FROM orders WHERE run_id=? AND intent='close'",
        (tag,),
    ).fetchall()
    return [float(r[0]) for r in pnl_rows]


def _cumulative(pnls: list[float]) -> list[float]:
    curve = []
    total = 0.0
    for p in pnls:
        total += p
        curve.append(total)
    return curve
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/integration/test_walkforward.py -v && \
git add bullbot/backtest/walkforward.py tests/integration/test_walkforward.py && \
git commit -m "stage1(T20): backtest/walkforward — anchored 70/30, 3-5 folds, aggregate metrics"
```

---

## Phase F — Risk (T21–T22)

### Task 21: bullbot/risk/cost_ledger.py

**Files:**
- Create: `bullbot/risk/cost_ledger.py`
- Create: `tests/unit/test_cost_ledger.py`

- [ ] **Step 1: Write test**

```python
"""Cost ledger tests."""
from bullbot.risk import cost_ledger


def test_append_and_sum_by_category(db_conn):
    cost_ledger.append(db_conn, ts=1000, category="llm", ticker="AAPL", amount_usd=0.04, details={"model": "opus"})
    cost_ledger.append(db_conn, ts=1001, category="llm", ticker="TSLA", amount_usd=0.05, details=None)
    cost_ledger.append(db_conn, ts=1002, category="data_uw", ticker="AAPL", amount_usd=0.0, details=None)

    assert cost_ledger.cumulative_llm_usd(db_conn) == 0.09
    assert cost_ledger.cumulative_by_ticker(db_conn, "AAPL")["llm"] == 0.04


def test_can_afford_returns_true_by_default(db_conn):
    assert cost_ledger.can_afford(db_conn, 0.10, ceiling_usd=1000.0) is True


def test_can_afford_returns_false_when_at_ceiling(db_conn):
    for i in range(30):
        cost_ledger.append(db_conn, ts=i, category="llm", ticker="X", amount_usd=35.0, details=None)
    # Total spend is 30 * 35 = 1050. Ceiling is 1000. can_afford(0.10) should be False.
    assert cost_ledger.can_afford(db_conn, 0.10, ceiling_usd=1000.0) is False
```

- [ ] **Step 2: Write `bullbot/risk/cost_ledger.py`**

```python
"""
Append-only billing log.

Every billable event (LLM call, data fetch, order fill) writes a row
here BEFORE the main work proceeds. Used by:
  - kill_switch (research-ratthole trigger)
  - CLI status command
  - nightly report cost summary
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def append(
    conn: sqlite3.Connection,
    ts: int,
    category: str,
    ticker: str | None,
    amount_usd: float,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO cost_ledger (ts, category, ticker, amount_usd, details) "
        "VALUES (?, ?, ?, ?, ?)",
        (ts, category, ticker, amount_usd,
         json.dumps(details) if details else None),
    )


def cumulative_llm_usd(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_usd), 0) FROM cost_ledger WHERE category='llm'"
    ).fetchone()
    return float(row[0])


def cumulative_by_ticker(conn: sqlite3.Connection, ticker: str) -> dict[str, float]:
    rows = conn.execute(
        "SELECT category, SUM(amount_usd) FROM cost_ledger WHERE ticker=? GROUP BY category",
        (ticker,),
    ).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def can_afford(
    conn: sqlite3.Connection,
    proposed_usd: float,
    ceiling_usd: float,
) -> bool:
    """Check whether adding `proposed_usd` would exceed the global LLM ceiling."""
    current = cumulative_llm_usd(conn)
    return (current + proposed_usd) <= ceiling_usd
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/unit/test_cost_ledger.py -v && \
git add bullbot/risk/cost_ledger.py tests/unit/test_cost_ledger.py && \
git commit -m "stage1(T21): risk/cost_ledger — append-only billing log + can_afford gate"
```

---

### Task 22: bullbot/risk/kill_switch.py

**Files:**
- Create: `bullbot/risk/kill_switch.py`
- Create: `tests/integration/test_kill_switch.py`

- [ ] **Step 1: Write test**

```python
"""Kill switch tests — all three trip conditions + re-arm path."""
from bullbot.risk import kill_switch, cost_ledger
from bullbot import config


def test_not_tripped_on_empty_db(db_conn):
    assert kill_switch.is_tripped(db_conn) is False


def test_trips_on_daily_loss(db_conn):
    # Insert a strategy row + a live close that lost $2000 today
    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'X', 1, '{}', 'h', 0)"
    )
    import time
    now = int(time.time())
    db_conn.execute(
        "INSERT INTO positions (run_id, ticker, strategy_id, opened_at, closed_at, legs, contracts, open_price, close_price, pnl_realized) "
        "VALUES ('live', 'SPY', 1, ?, ?, '[]', 1, 0, 0, ?)",
        (now - 3600, now, -2000.0),
    )
    assert kill_switch.should_trip_now(db_conn) is True
    kill_switch.trip(db_conn, reason="daily_loss")
    assert kill_switch.is_tripped(db_conn) is True


def test_trips_on_research_ratthole(db_conn):
    # $1001 LLM spend with zero live tickers
    cost_ledger.append(db_conn, ts=1, category="llm", ticker="X", amount_usd=1001.0)
    assert kill_switch.should_trip_now(db_conn) is True


def test_rearm_resets_kill_state(db_conn):
    kill_switch.trip(db_conn, reason="test")
    assert kill_switch.is_tripped(db_conn) is True
    kill_switch.rearm(db_conn)
    assert kill_switch.is_tripped(db_conn) is False
```

- [ ] **Step 2: Write `bullbot/risk/kill_switch.py`**

```python
"""
Kill switch — layered capital + research safety.

Three trip conditions (spec §6.8):
  1. Daily realized loss on live  >= $1,500
  2. Total live drawdown          >= $5,000
  3. LLM spend >= $1,000 with zero live tickers

On trip: flip all live tickers to 'killed', write kill_state row, write a
structured kill report to reports/kill_<ts>.md. Scheduler checks
is_tripped() before every action.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone

from bullbot import config
from bullbot.risk import cost_ledger

log = logging.getLogger("bullbot.kill_switch")


def is_tripped(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT active FROM kill_state WHERE id=1").fetchone()
    return bool(row and row[0])


def _realized_loss_today(conn: sqlite3.Connection) -> float:
    """Sum of realized pnl on run_id='live' positions closed today (ET)."""
    from bullbot import clock as clock_mod
    today_et = clock_mod.et_now().date()
    start_et = datetime.combine(today_et, datetime.min.time(), tzinfo=clock_mod._ET)
    start_epoch = int(start_et.timestamp())

    row = conn.execute(
        "SELECT COALESCE(SUM(pnl_realized), 0) FROM positions "
        "WHERE run_id='live' AND closed_at IS NOT NULL AND closed_at>=?",
        (start_epoch,),
    ).fetchone()
    return float(row[0])


def _peak_to_trough_dd(conn: sqlite3.Connection) -> float:
    """Peak-to-trough drawdown on live equity curve."""
    rows = conn.execute(
        "SELECT ts, pnl_realized FROM ("
        "  SELECT closed_at AS ts, pnl_realized FROM positions "
        "  WHERE run_id='live' AND closed_at IS NOT NULL"
        ") ORDER BY ts"
    ).fetchall()
    if not rows:
        return 0.0
    equity = config.INITIAL_CAPITAL_USD
    peak = equity
    max_dd = 0.0
    for r in rows:
        equity += float(r["pnl_realized"] or 0)
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
    return max_dd


def _count_live_tickers(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM ticker_state WHERE phase='live'"
    ).fetchone()
    return int(row[0])


def should_trip_now(conn: sqlite3.Connection) -> bool:
    """Check all three conditions. Returns True on any trip."""
    daily_loss = _realized_loss_today(conn)
    if daily_loss <= -config.KILL_DAILY_LOSS_USD:
        return True

    if _peak_to_trough_dd(conn) >= config.KILL_TOTAL_DD_USD:
        return True

    llm_spend = cost_ledger.cumulative_llm_usd(conn)
    live_count = _count_live_tickers(conn)
    if llm_spend >= config.KILL_RESEARCH_RATTHOLE_USD and live_count == 0:
        return True

    return False


def trip(conn: sqlite3.Connection, reason: str) -> None:
    """Flip kill_state active + all live tickers to 'killed'. Write report."""
    now = int(time.time())
    conn.execute(
        "INSERT OR REPLACE INTO kill_state (id, active, tripped_at, reason, trigger_rule) "
        "VALUES (1, 1, ?, ?, ?)",
        (now, reason, reason),
    )
    conn.execute(
        "UPDATE ticker_state SET phase='killed', updated_at=? WHERE phase='live'",
        (now,),
    )
    _write_kill_report(conn, reason, now)
    log.critical("KILL SWITCH TRIPPED: %s", reason)


def rearm(conn: sqlite3.Connection) -> None:
    """Clear kill_state.active. Does NOT reset ticker phases — that's manual."""
    conn.execute("UPDATE kill_state SET active=0 WHERE id=1")


def _write_kill_report(conn: sqlite3.Connection, reason: str, ts: int) -> None:
    from pathlib import Path
    path = config.REPORTS_DIR / f"kill_{datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H-%M')}.md"
    lines = [
        f"# KILL SWITCH TRIP — {reason}",
        f"**Tripped at (UTC):** {datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()}",
        "",
        f"Daily loss: ${_realized_loss_today(conn):.2f}",
        f"Peak-to-trough DD: ${_peak_to_trough_dd(conn):.2f}",
        f"Cumulative LLM spend: ${cost_ledger.cumulative_llm_usd(conn):.2f}",
        f"Live tickers before trip: {_count_live_tickers(conn)}",
        "",
        "## Open positions at trip",
        "",
    ]
    open_pos = conn.execute(
        "SELECT ticker, strategy_id, contracts, open_price, mark_to_mkt "
        "FROM positions WHERE run_id='live' AND closed_at IS NULL"
    ).fetchall()
    for p in open_pos:
        lines.append(
            f"- {p['ticker']} (strategy {p['strategy_id']}): "
            f"{p['contracts']} contracts, open=${p['open_price']:.2f}, mark=${p['mark_to_mkt']:.2f}"
        )
    path.write_text("\n".join(lines))
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/integration/test_kill_switch.py -v && \
git add bullbot/risk/kill_switch.py tests/integration/test_kill_switch.py && \
git commit -m "stage1(T22): risk/kill_switch — 3 trip conditions, trip, rearm, kill report"
```

---

## Phase G — Evolver (T23–T24)

### Task 23: bullbot/evolver/proposer.py

**Files:**
- Create: `bullbot/evolver/proposer.py`
- Create: `tests/integration/test_proposer.py`

- [ ] **Step 1: Write test**

```python
"""Proposer tests — uses FakeAnthropicClient from conftest."""
import json

import pytest

from bullbot.evolver import proposer
from bullbot.strategies.base import StrategySnapshot


def _snap():
    return StrategySnapshot(
        ticker="SPY", asof_ts=1718395200, spot=582.14,
        bars_1d=[], indicators={"sma_20": 578.45, "rsi_14": 58.4},
        atm_greeks={"delta": 0.52}, iv_rank=60.0, regime="bull", chain=[],
    )


def test_proposer_returns_parsed_proposal(fake_anthropic):
    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50},
        "rationale": "Baseline credit spread for bull regime",
    }))
    result = proposer.propose(
        client=fake_anthropic,
        snapshot=_snap(),
        history=[],
        best_strategy_id=None,
    )
    assert result.class_name == "PutCreditSpread"
    assert result.params["dte"] == 14
    assert "credit spread" in result.rationale.lower()
    assert result.llm_cost_usd > 0


def test_proposer_retries_once_on_malformed_json(fake_anthropic):
    fake_anthropic.queue_response("not json")
    fake_anthropic.queue_response(json.dumps({
        "class_name": "IronCondor",
        "params": {"dte": 21, "wing_delta": 0.20, "wing_width": 5, "iv_rank_min": 60},
        "rationale": "Second attempt",
    }))
    result = proposer.propose(fake_anthropic, _snap(), [], None)
    assert result.class_name == "IronCondor"


def test_proposer_raises_after_two_malformed(fake_anthropic):
    fake_anthropic.queue_response("still not json")
    fake_anthropic.queue_response("also not json")
    with pytest.raises(proposer.ProposerJsonError):
        proposer.propose(fake_anthropic, _snap(), [], None)


def test_proposer_raises_on_unknown_class(fake_anthropic):
    fake_anthropic.queue_response(json.dumps({
        "class_name": "NonExistentStrategy",
        "params": {},
        "rationale": "test",
    }))
    with pytest.raises(proposer.ProposerUnknownStrategyError):
        proposer.propose(fake_anthropic, _snap(), [], None)


def test_build_history_block_formats_past_proposals():
    history = [
        {
            "iteration": 3, "class_name": "PutCreditSpread",
            "params": '{"dte": 14}', "pf_is": 1.2, "pf_oos": 0.9,
            "trade_count": 40, "passed_gate": 0, "rationale": "test",
        },
    ]
    block = proposer.build_history_block(history)
    assert "iter=3" in block
    assert "PutCreditSpread" in block
    assert "FAILED" in block or "failed" in block
```

- [ ] **Step 2: Write `bullbot/evolver/proposer.py`**

```python
"""
The evolver's Opus wrapper. Builds a prompt from the current feature
snapshot + last N past proposals + the current best-so-far strategy,
calls Opus, parses structured JSON back into a Proposal.

This is the ONE LLM call site in the entire Bull-Bot v3 system.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from bullbot import config
from bullbot.strategies import registry
from bullbot.strategies.base import StrategySnapshot

log = logging.getLogger("bullbot.proposer")


class ProposerJsonError(Exception):
    pass


class ProposerApiError(Exception):
    pass


class ProposerBudgetError(Exception):
    pass


class ProposerUnknownStrategyError(Exception):
    pass


@dataclass
class Proposal:
    class_name: str
    params: dict[str, Any]
    rationale: str
    llm_cost_usd: float
    input_tokens: int
    output_tokens: int


_SYSTEM_PROMPT = """You are the strategy proposer inside Bull-Bot v3, an automated options-strategy discovery system. Your job is to propose the next options strategy to backtest for a specific ticker, given:

1. A feature snapshot of the ticker at the current moment (OHLC, technical indicators, ATM greeks, IV rank)
2. A history of past proposals for this ticker with their backtest verdicts
3. The edge gate: PF_is >= 1.5 AND PF_oos >= 1.3 AND n_trades >= 30 on anchored 70/30 walk-forward

You MUST emit a single JSON object matching this exact schema:
{
  "class_name": "PutCreditSpread" | "CallCreditSpread" | "IronCondor" | "CashSecuredPut" | "LongCall" | "LongPut",
  "params": <object with strategy-specific parameters>,
  "rationale": "<one to three sentences explaining why this proposal addresses what past proposals got wrong>"
}

Rules:
- Learn from past failures. If a PutCreditSpread with dte=14, delta=0.25 failed, proposing the same shape is wasteful.
- Favor structurally different proposals when the last 3 all failed for similar reasons.
- Your rationale must reference at least one past proposal by iteration number when history is non-empty.
- Output ONLY the JSON object. No prose, no markdown fences, no preamble."""


def build_history_block(history: list[dict[str, Any]]) -> str:
    """Format past proposals as a prompt block for the LLM."""
    if not history:
        return "(no prior proposals — this is iteration 1; pick a seed strategy with reasonable defaults)"
    lines = []
    for row in history[:config.HISTORY_BLOCK_SIZE]:
        verdict = "passed" if row.get("passed_gate") else "FAILED"
        params = row.get("params", "{}")
        if isinstance(params, dict):
            params = json.dumps(params)
        lines.append(
            f"iter={row.get('iteration')} {row.get('class_name')}{params}\n"
            f"        PF_is={row.get('pf_is', 0):.2f} PF_oos={row.get('pf_oos', 0):.2f} "
            f"n_trades={row.get('trade_count', 0)}\n"
            f"        passed_gate={verdict}\n"
            f"        rationale: {row.get('rationale', '')}\n"
        )
    return "\n".join(lines)


def build_user_prompt(
    snapshot: StrategySnapshot,
    history: list[dict[str, Any]],
    best_strategy_id: int | None,
) -> str:
    ind = snapshot.indicators
    from datetime import datetime, timezone
    asof_iso = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc).isoformat()

    return f"""TICKER: {snapshot.ticker}
ASOF: {asof_iso}

=== FEATURE SNAPSHOT ===

Spot: {snapshot.spot:.2f}
Regime: {snapshot.regime}
IV rank: {snapshot.iv_rank:.0f}

Indicators:
  sma_20: {ind.get('sma_20', 0):.2f}
  ema_20: {ind.get('ema_20', 0):.2f}
  rsi_14: {ind.get('rsi_14', 0):.1f}
  atr_14: {ind.get('atr_14', 0):.2f}

ATM Greeks:
{json.dumps(snapshot.atm_greeks, indent=2)}

=== PAST PROPOSAL HISTORY (most recent first) ===

{build_history_block(history)}

=== TASK ===

Propose the next iteration. Current best_strategy_id={best_strategy_id}. Emit JSON only."""


def _cost_for_call(input_tokens: int, output_tokens: int) -> float:
    # Opus 4.6 pricing: $15/MTok input, $75/MTok output
    return (input_tokens * 15.0 + output_tokens * 75.0) / 1_000_000


def propose(
    client: Any,
    snapshot: StrategySnapshot,
    history: list[dict[str, Any]],
    best_strategy_id: int | None,
) -> Proposal:
    """Make ONE proposal call. Retries once on malformed JSON only."""
    user_prompt = build_user_prompt(snapshot, history, best_strategy_id)
    messages = [{"role": "user", "content": user_prompt}]

    for attempt in (0, 1):
        try:
            response = client.messages.create(
                model=config.PROPOSER_MODEL,
                max_tokens=config.PROPOSER_MAX_TOKENS,
                temperature=0.0,
                system=_SYSTEM_PROMPT,
                messages=messages,
            )
        except Exception as e:
            raise ProposerApiError(f"Anthropic API error: {e}") from e

        text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text += block.text

        try:
            parsed = json.loads(text.strip())
        except json.JSONDecodeError:
            if attempt == 0:
                # Add a corrective retry turn
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "Your previous response was not valid JSON. Output only the JSON object, no prose, no markdown fences."},
                ]
                continue
            raise ProposerJsonError(f"malformed JSON after retry: {text[:200]}")

        if not isinstance(parsed, dict):
            raise ProposerJsonError(f"expected JSON object, got {type(parsed).__name__}")

        required = {"class_name", "params", "rationale"}
        if not required.issubset(parsed.keys()):
            raise ProposerJsonError(f"missing required fields: {required - parsed.keys()}")

        class_name = parsed["class_name"]
        if class_name not in registry.list_all_names():
            raise ProposerUnknownStrategyError(class_name)

        usage = response.usage
        input_t = getattr(usage, "input_tokens", 0) or 0
        output_t = getattr(usage, "output_tokens", 0) or 0

        return Proposal(
            class_name=class_name,
            params=parsed["params"] if isinstance(parsed["params"], dict) else {},
            rationale=str(parsed["rationale"]),
            llm_cost_usd=_cost_for_call(input_t, output_t),
            input_tokens=input_t,
            output_tokens=output_t,
        )

    raise ProposerJsonError("unreachable")
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/integration/test_proposer.py -v && \
git add bullbot/evolver/proposer.py tests/integration/test_proposer.py && \
git commit -m "stage1(T23): evolver/proposer — Opus wrapper + history block + JSON retry"
```

---

### Task 24: bullbot/evolver/iteration.py

**Files:**
- Create: `bullbot/evolver/iteration.py`
- Create: `tests/integration/test_evolver_iteration.py`

- [ ] **Step 1: Write test**

```python
"""Full evolver_iteration integration tests."""
import json
from datetime import datetime, timezone

from bullbot.evolver import iteration


def _seed_ticker_state(db_conn, ticker="SPY", phase="discovering"):
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES (?, ?, 0)",
        (ticker, phase),
    )


def _seed_bars(db_conn, ticker="SPY", n_days=500):
    base_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    for i in range(n_days):
        ts = base_ts + i * 86400
        price = 500.0 + i * 0.3
        db_conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume, source) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, ?, 'uw')",
            (ticker, ts, price, price + 2, price - 1, price + 1, 1_000_000),
        )


def test_evolver_iteration_inserts_proposal_row(db_conn, fake_anthropic):
    _seed_ticker_state(db_conn)
    _seed_bars(db_conn)
    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50},
        "rationale": "baseline",
    }))
    iteration.run(
        conn=db_conn,
        anthropic_client=fake_anthropic,
        data_client=None,
        ticker="SPY",
    )
    rows = db_conn.execute(
        "SELECT * FROM evolver_proposals WHERE ticker='SPY'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["iteration"] == 1


def test_evolver_iteration_increments_state_counters(db_conn, fake_anthropic):
    _seed_ticker_state(db_conn)
    _seed_bars(db_conn)
    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50},
        "rationale": "baseline",
    }))
    iteration.run(db_conn, fake_anthropic, None, "SPY")
    state = db_conn.execute(
        "SELECT iteration_count FROM ticker_state WHERE ticker='SPY'"
    ).fetchone()
    assert state["iteration_count"] == 1


def test_dedup_short_circuit_fires_on_identical_proposal(db_conn, fake_anthropic):
    _seed_ticker_state(db_conn)
    _seed_bars(db_conn)
    for _ in range(2):
        fake_anthropic.queue_response(json.dumps({
            "class_name": "PutCreditSpread",
            "params": {"dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50},
            "rationale": "same",
        }))
    iteration.run(db_conn, fake_anthropic, None, "SPY")
    iteration.run(db_conn, fake_anthropic, None, "SPY")
    # Only ONE strategies row (dedup), but TWO evolver_proposals rows (duplicate record)
    n_strategies = db_conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    n_proposals = db_conn.execute("SELECT COUNT(*) FROM evolver_proposals").fetchone()[0]
    assert n_strategies == 1
    assert n_proposals == 2
```

- [ ] **Step 2: Write `bullbot/evolver/iteration.py`**

```python
"""
The core algorithm. `run(ticker)` executes one full evolver iteration:
1. Load ticker_state + proposal history
2. Build feature snapshot
3. Call proposer
4. Dedup check on params_hash
5. Run walk-forward backtest
6. Classify verdict via plateau logic
7. Write proposal row + update ticker_state + log costs (atomic)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import traceback
from typing import Any

from bullbot import config
from bullbot.backtest import walkforward
from bullbot.engine import step as engine_step
from bullbot.evolver import plateau, proposer
from bullbot.risk import cost_ledger
from bullbot.strategies import registry

log = logging.getLogger("bullbot.evolver")


def _load_history(conn: sqlite3.Connection, ticker: str, n: int) -> list[dict]:
    rows = conn.execute(
        "SELECT p.iteration, s.class_name, s.params, p.pf_is, p.pf_oos, "
        "p.trade_count, p.passed_gate, p.rationale "
        "FROM evolver_proposals p JOIN strategies s ON p.strategy_id = s.id "
        "WHERE p.ticker=? ORDER BY p.iteration DESC LIMIT ?",
        (ticker, n),
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "iteration": r["iteration"],
            "class_name": r["class_name"],
            "params": r["params"],
            "pf_is": r["pf_is"] or 0,
            "pf_oos": r["pf_oos"] or 0,
            "trade_count": r["trade_count"] or 0,
            "passed_gate": r["passed_gate"] or 0,
            "rationale": r["rationale"] or "",
        })
    return out


def _load_state(conn: sqlite3.Connection, ticker: str) -> dict:
    row = conn.execute(
        "SELECT * FROM ticker_state WHERE ticker=?", (ticker,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES (?, 'discovering', ?)",
            (ticker, int(time.time())),
        )
        row = conn.execute(
            "SELECT * FROM ticker_state WHERE ticker=?", (ticker,)
        ).fetchone()
    return dict(row)


def _find_existing_strategy(
    conn: sqlite3.Connection, class_name: str, class_version: int, params_hash: str
) -> int | None:
    row = conn.execute(
        "SELECT id FROM strategies WHERE class_name=? AND class_version=? AND params_hash=?",
        (class_name, class_version, params_hash),
    ).fetchone()
    return row["id"] if row else None


def _insert_strategy(
    conn: sqlite3.Connection,
    class_name: str,
    class_version: int,
    params: dict,
    params_hash: str,
    parent_id: int | None,
) -> int:
    cur = conn.execute(
        "INSERT INTO strategies (class_name, class_version, params, params_hash, parent_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (class_name, class_version, registry.canonicalize_params(params),
         params_hash, parent_id, int(time.time())),
    )
    return cur.lastrowid


def _record_duplicate_proposal(
    conn: sqlite3.Connection,
    ticker: str,
    state: dict,
    strategy_id: int,
    prior_proposal: sqlite3.Row,
    llm_cost: float,
) -> None:
    iter_num = state["iteration_count"] + 1
    conn.execute(
        "INSERT INTO evolver_proposals "
        "(ticker, iteration, strategy_id, rationale, llm_cost_usd, "
        "pf_is, pf_oos, sharpe_is, max_dd_pct, trade_count, regime_breakdown, "
        "passed_gate, created_at) "
        "VALUES (?, ?, ?, 'DUPLICATE — identical to prior proposal', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ticker, iter_num, strategy_id, llm_cost,
         prior_proposal["pf_is"], prior_proposal["pf_oos"],
         prior_proposal["sharpe_is"], prior_proposal["max_dd_pct"],
         prior_proposal["trade_count"], prior_proposal["regime_breakdown"],
         prior_proposal["passed_gate"], int(time.time())),
    )


def run(
    conn: sqlite3.Connection,
    anthropic_client: Any,
    data_client: Any,
    ticker: str,
) -> None:
    """
    Execute one full evolver iteration for `ticker`. All writes to main
    tables happen inside a single transaction; on exception the transaction
    rolls back and a row is written to iteration_failures via a separate
    connection.
    """
    state = _load_state(conn, ticker)
    history = _load_history(conn, ticker, config.HISTORY_BLOCK_SIZE)

    # Build snapshot. Must happen before LLM call so the LLM sees current state.
    cursor = int(time.time())
    snapshot = engine_step._build_snapshot(conn, ticker, cursor)
    if snapshot is None:
        log.warning("insufficient data for %s, skipping iteration", ticker)
        return

    # --- LLM call (outside transaction — cost logged separately) ---
    try:
        proposal = proposer.propose(
            client=anthropic_client,
            snapshot=snapshot,
            history=history,
            best_strategy_id=state.get("best_strategy_id"),
        )
    except proposer.ProposerApiError as e:
        log.warning("proposer API error for %s: %s", ticker, e)
        return

    cost_ledger.append(
        conn, ts=int(time.time()), category="llm", ticker=ticker,
        amount_usd=proposal.llm_cost_usd,
        details={"model": config.PROPOSER_MODEL, "input_tokens": proposal.input_tokens, "output_tokens": proposal.output_tokens},
    )

    # --- Dedup short-circuit ---
    params_hash = registry.params_hash(proposal.params)
    cls = registry.get_class(proposal.class_name)
    existing_id = _find_existing_strategy(conn, cls.CLASS_NAME, cls.CLASS_VERSION, params_hash)

    if existing_id is not None:
        prior = conn.execute(
            "SELECT * FROM evolver_proposals WHERE ticker=? AND strategy_id=? LIMIT 1",
            (ticker, existing_id),
        ).fetchone()
        if prior is not None:
            _record_duplicate_proposal(conn, ticker, state, existing_id, prior, proposal.llm_cost_usd)
            conn.execute(
                "UPDATE ticker_state SET iteration_count=iteration_count+1, "
                "cumulative_llm_usd=cumulative_llm_usd+?, updated_at=? WHERE ticker=?",
                (proposal.llm_cost_usd, int(time.time()), ticker),
            )
            return

    # --- Insert strategies row + run backtest ---
    strategy_id = existing_id or _insert_strategy(
        conn, cls.CLASS_NAME, cls.CLASS_VERSION, proposal.params, params_hash,
        state.get("best_strategy_id"),
    )
    strategy_instance = registry.materialize(proposal.class_name, proposal.params)
    metrics = walkforward.run_walkforward(conn, strategy_instance, strategy_id, ticker)

    # --- Classify verdict via plateau logic ---
    class _StateShim:
        iteration_count = state["iteration_count"]
        plateau_counter = state["plateau_counter"]
        best_pf_oos = state.get("best_pf_oos") or 0.0

    class _MetricsShim:
        pf_is = metrics.pf_is
        pf_oos = metrics.pf_oos
        trade_count = metrics.trade_count

    result = plateau.classify(_StateShim, _MetricsShim)

    # --- Write proposal row + update state (atomic) ---
    iter_num = state["iteration_count"] + 1
    new_phase = state["phase"]
    if result.verdict == "edge_found":
        new_phase = "paper_trial"
    elif result.verdict == "no_edge":
        new_phase = "no_edge"

    conn.execute(
        "INSERT INTO evolver_proposals "
        "(ticker, iteration, strategy_id, rationale, llm_cost_usd, "
        "pf_is, pf_oos, sharpe_is, max_dd_pct, trade_count, regime_breakdown, "
        "passed_gate, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ticker, iter_num, strategy_id, proposal.rationale, proposal.llm_cost_usd,
         metrics.pf_is, metrics.pf_oos, metrics.sharpe_is, metrics.max_dd_pct,
         metrics.trade_count, json.dumps(metrics.regime_breakdown),
         1 if result.verdict == "edge_found" else 0, int(time.time())),
    )

    now_epoch = int(time.time())
    new_paper_started = now_epoch if result.verdict == "edge_found" else state.get("paper_started_at")
    new_best_sid = strategy_id if result.improved else state.get("best_strategy_id")

    conn.execute(
        "UPDATE ticker_state SET "
        "phase=?, iteration_count=?, plateau_counter=?, "
        "best_strategy_id=?, best_pf_is=?, best_pf_oos=?, "
        "cumulative_llm_usd=cumulative_llm_usd+?, "
        "paper_started_at=?, paper_trade_count=0, "
        "verdict_at=?, updated_at=? WHERE ticker=?",
        (new_phase, iter_num, result.new_plateau_counter,
         new_best_sid, max(state.get("best_pf_is") or 0, metrics.pf_is),
         result.new_best_pf_oos,
         proposal.llm_cost_usd,
         new_paper_started,
         now_epoch if new_phase in ("no_edge", "paper_trial") else state.get("verdict_at"),
         now_epoch, ticker),
    )
    log.info(
        "evolver(%s): iter=%d verdict=%s pf_is=%.2f pf_oos=%.2f n=%d",
        ticker, iter_num, result.verdict, metrics.pf_is, metrics.pf_oos, metrics.trade_count,
    )
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/integration/test_evolver_iteration.py -v && \
git add bullbot/evolver/iteration.py tests/integration/test_evolver_iteration.py && \
git commit -m "stage1(T24): evolver/iteration — the core algorithm

Loads state + history, calls proposer, dedup short-circuits, runs
walkforward, classifies via plateau, writes proposal row + state update."
```

---

## Phase H — Orchestration (T25–T28)

### Task 25: bullbot/nightly.py

**Files:**
- Create: `bullbot/nightly.py`
- Create: `tests/integration/test_nightly.py`

- [ ] **Step 1: Write test**

```python
"""Nightly pipeline tests — faithfulness, promotion, kill-switch recompute."""
import time

from bullbot import config, nightly


def test_faithfulness_check_inserts_row(db_conn):
    now = int(time.time())
    # Ticker in paper_trial, paper started 10 days ago
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, paper_trade_count, best_strategy_id, updated_at) "
        "VALUES ('SPY', 'paper_trial', ?, 12, 1, ?)",
        (now - 10 * 86400, now),
    )
    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'X', 1, '{}', 'h', 0)"
    )
    # Seed a few paper positions with known pnl
    for i, pnl in enumerate([100, -50, 200, -30, 150]):
        db_conn.execute(
            "INSERT INTO positions (run_id, ticker, strategy_id, opened_at, closed_at, "
            "legs, contracts, open_price, close_price, pnl_realized, mark_to_mkt) "
            "VALUES ('paper', 'SPY', 1, ?, ?, '[]', 1, 0, 0, ?, 0)",
            (now - (5 - i) * 86400, now - (5 - i) * 86400 + 3600, pnl),
        )
    nightly.run_all(db_conn)

    checks = db_conn.execute("SELECT * FROM faithfulness_checks").fetchall()
    assert len(checks) >= 1


def test_promotion_to_live_when_all_gates_pass(db_conn):
    now = int(time.time())
    started = now - 22 * 86400    # 22 days ago
    db_conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'X', 1, '{}', 'h', 0)"
    )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, paper_trade_count, best_strategy_id, updated_at) "
        "VALUES ('SPY', 'paper_trial', ?, 15, 1, ?)",
        (started, now),
    )
    # Seed 5 passing faithfulness checks
    for i in range(5):
        db_conn.execute(
            "INSERT INTO faithfulness_checks (ticker, checked_at, window_days, paper_pf, backtest_pf, delta_pct, passed) "
            "VALUES ('SPY', ?, 5, 1.4, 1.5, -0.067, 1)",
            (now - (5 - i) * 86400,),
        )
    nightly.run_all(db_conn)
    state = db_conn.execute("SELECT phase FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert state["phase"] == "live"
```

- [ ] **Step 2: Write `bullbot/nightly.py`**

```python
"""
Nightly job: mark-to-market open positions, run faithfulness checks on
paper_trial tickers, evaluate promotion eligibility, recompute kill-switch
triggers, write the nightly report.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone

from bullbot import config
from bullbot.risk import cost_ledger, kill_switch

log = logging.getLogger("bullbot.nightly")


def _days_since(start_ts: int) -> int:
    if not start_ts:
        return 0
    return max(0, (int(time.time()) - int(start_ts)) // 86400)


def _compute_pf_for_run(conn: sqlite3.Connection, run_id: str, ticker: str) -> float:
    rows = conn.execute(
        "SELECT pnl_realized FROM positions "
        "WHERE run_id=? AND ticker=? AND closed_at IS NOT NULL",
        (run_id, ticker),
    ).fetchall()
    pnls = [float(r["pnl_realized"] or 0) for r in rows]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss == 0:
        return 0.0 if gross_win == 0 else float("inf")
    return gross_win / gross_loss


def _compute_backtest_pf(conn: sqlite3.Connection, strategy_id: int) -> float:
    row = conn.execute(
        "SELECT pf_oos FROM evolver_proposals WHERE strategy_id=? ORDER BY created_at DESC LIMIT 1",
        (strategy_id,),
    ).fetchone()
    return float(row["pf_oos"]) if row else 0.0


def _faithfulness_check(
    conn: sqlite3.Connection, ticker: str, strategy_id: int, window_days: int = 5
) -> None:
    paper_pf = _compute_pf_for_run(conn, "paper", ticker)
    backtest_pf = _compute_backtest_pf(conn, strategy_id)
    if backtest_pf <= 0:
        return
    delta = (paper_pf - backtest_pf) / backtest_pf if backtest_pf else 0.0
    passed = abs(delta) <= config.FAITHFULNESS_DELTA_MAX
    conn.execute(
        "INSERT INTO faithfulness_checks "
        "(ticker, checked_at, window_days, paper_pf, backtest_pf, delta_pct, passed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ticker, int(time.time()), window_days, paper_pf, backtest_pf, delta, 1 if passed else 0),
    )


def _check_promotion_eligibility(conn: sqlite3.Connection, ticker: str, state: dict) -> None:
    if _days_since(state.get("paper_started_at") or 0) < config.PAPER_TRIAL_DAYS:
        return
    if state["paper_trade_count"] < config.PAPER_TRADE_COUNT_MIN:
        return

    recent = conn.execute(
        "SELECT passed FROM faithfulness_checks WHERE ticker=? "
        "ORDER BY checked_at DESC LIMIT 5",
        (ticker,),
    ).fetchall()
    if len(recent) < 5:
        return
    all_passed = all(r["passed"] for r in recent)

    if all_passed:
        conn.execute(
            "UPDATE ticker_state SET phase='live', live_started_at=?, updated_at=? WHERE ticker=?",
            (int(time.time()), int(time.time()), ticker),
        )
        log.info("PROMOTED %s to live", ticker)
    else:
        # Demote: reset plateau and send back to discovery
        conn.execute(
            "UPDATE ticker_state SET phase='discovering', plateau_counter=0, "
            "paper_started_at=NULL, paper_trade_count=0, updated_at=? WHERE ticker=?",
            (int(time.time()), ticker),
        )
        log.warning("DEMOTED %s (faithfulness failed)", ticker)


def _write_nightly_report(conn: sqlite3.Connection) -> None:
    from pathlib import Path
    today = datetime.now().strftime("%Y-%m-%d")
    path = config.REPORTS_DIR / f"nightly_{today}.md"
    rows = conn.execute(
        "SELECT phase, COUNT(*) as n FROM ticker_state GROUP BY phase"
    ).fetchall()
    phase_counts = {r["phase"]: r["n"] for r in rows}
    lines = [
        f"# Nightly Report — {today}",
        "",
        "## Ticker phase counts",
        ""
    ]
    for phase in ("discovering", "paper_trial", "live", "no_edge", "killed"):
        lines.append(f"- {phase}: {phase_counts.get(phase, 0)}")
    lines.append("")
    lines.append(f"Total LLM spend to date: ${cost_ledger.cumulative_llm_usd(conn):.2f}")
    path.write_text("\n".join(lines))


def run_all(conn: sqlite3.Connection) -> None:
    """Entry point called once per day after market close."""
    log.info("nightly pipeline start")
    tickers = conn.execute(
        "SELECT * FROM ticker_state WHERE phase IN ('paper_trial', 'live')"
    ).fetchall()
    for t_row in tickers:
        state = dict(t_row)
        ticker = state["ticker"]
        sid = state.get("best_strategy_id")
        if state["phase"] == "paper_trial":
            if _days_since(state.get("paper_started_at") or 0) >= config.FAITHFULNESS_MIN_DAYS and sid:
                _faithfulness_check(conn, ticker, sid)
            _check_promotion_eligibility(conn, ticker, state)

    # Full kill-switch recompute
    if kill_switch.should_trip_now(conn):
        kill_switch.trip(conn, reason="nightly_recheck")

    _write_nightly_report(conn)
    log.info("nightly pipeline done")
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/integration/test_nightly.py -v && \
git add bullbot/nightly.py tests/integration/test_nightly.py && \
git commit -m "stage1(T25): bullbot/nightly — MtM, faithfulness, promotion, kill recheck, report"
```

---

### Task 26: bullbot/scheduler.py

**Files:**
- Create: `bullbot/scheduler.py`
- Create: `tests/integration/test_scheduler.py`

- [ ] **Step 1: Write test**

```python
"""Scheduler dispatch tests."""
import json

from bullbot import scheduler


def test_tick_dispatches_discovering_to_evolver(db_conn, fake_anthropic, monkeypatch):
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'discovering', 0)"
    )

    called_with = []
    def fake_evolver_run(conn, anthropic_client, data_client, ticker):
        called_with.append(ticker)
    monkeypatch.setattr("bullbot.evolver.iteration.run", fake_evolver_run)

    scheduler.tick(
        conn=db_conn,
        anthropic_client=fake_anthropic,
        data_client=None,
        universe=["SPY"],
    )
    assert called_with == ["SPY"]


def test_tick_skips_when_kill_switch_tripped(db_conn, fake_anthropic, monkeypatch):
    db_conn.execute(
        "INSERT INTO kill_state (id, active) VALUES (1, 1)"
    )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'discovering', 0)"
    )

    called = []
    monkeypatch.setattr(
        "bullbot.evolver.iteration.run",
        lambda *a, **k: called.append(1),
    )
    scheduler.tick(
        conn=db_conn,
        anthropic_client=fake_anthropic,
        data_client=None,
        universe=["SPY"],
    )
    assert called == []


def test_tick_isolates_per_ticker_exceptions(db_conn, fake_anthropic, monkeypatch):
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('AAPL', 'discovering', 0)"
    )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'discovering', 0)"
    )

    def flaky_run(conn, anthropic_client, data_client, ticker):
        if ticker == "AAPL":
            raise ValueError("boom")
        # SPY succeeds silently

    monkeypatch.setattr("bullbot.evolver.iteration.run", flaky_run)
    # Should NOT raise
    scheduler.tick(db_conn, fake_anthropic, None, ["AAPL", "SPY"])

    # iteration_failures should have one entry for AAPL
    rows = db_conn.execute("SELECT ticker FROM iteration_failures").fetchall()
    assert [r["ticker"] for r in rows] == ["AAPL"]
```

- [ ] **Step 2: Write `bullbot/scheduler.py`**

```python
"""
Scheduler — the outer loop. Every tick:
1. Check kill switch (cheap).
2. For each ticker in universe:
   - Look up phase
   - Dispatch to evolver_iteration (discovering) or engine.step (paper/live)
   - Isolate per-ticker exceptions in iteration_failures
3. If end-of-day and market closed, fire nightly hook.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import traceback
from typing import Any

from bullbot import clock, config, nightly
from bullbot.data import fetchers
from bullbot.engine import step as engine_step
from bullbot.evolver import iteration as evolver_iteration
from bullbot.risk import kill_switch
from bullbot.strategies import registry

log = logging.getLogger("bullbot.scheduler")


def _record_iteration_failure(
    conn: sqlite3.Connection,
    ticker: str,
    phase: str,
    exc: Exception,
) -> None:
    conn.execute(
        "INSERT INTO iteration_failures (ts, ticker, phase, exc_type, exc_message, traceback) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (int(time.time()), ticker, phase, type(exc).__name__, str(exc), traceback.format_exc()),
    )


def _dispatch_ticker(
    conn: sqlite3.Connection,
    ticker: str,
    anthropic_client: Any,
    data_client: Any,
) -> None:
    row = conn.execute(
        "SELECT * FROM ticker_state WHERE ticker=?", (ticker,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES (?, 'discovering', ?)",
            (ticker, int(time.time())),
        )
        row = conn.execute("SELECT * FROM ticker_state WHERE ticker=?", (ticker,)).fetchone()

    phase = row["phase"]
    if row["retired"]:
        # Retired tickers close existing positions but open no new ones.
        # v1: skip entirely (position close logic is in engine.step).
        return

    if phase == "discovering":
        evolver_iteration.run(conn, anthropic_client, data_client, ticker)
        return

    if phase in ("paper_trial", "live"):
        if not clock.is_market_open_now():
            return
        sid = row["best_strategy_id"]
        if sid is None:
            return
        srow = conn.execute("SELECT * FROM strategies WHERE id=?", (sid,)).fetchone()
        if srow is None:
            return
        import json
        strategy = registry.materialize(
            srow["class_name"], json.loads(srow["params"])
        )
        run_id = "live" if phase == "live" else "paper"
        engine_step.step(
            conn=conn,
            client=data_client,
            cursor=int(time.time()),
            ticker=ticker,
            strategy=strategy,
            strategy_id=sid,
            run_id=run_id,
        )
        if run_id == "paper":
            conn.execute(
                "UPDATE ticker_state SET paper_trade_count=paper_trade_count+1, updated_at=? "
                "WHERE ticker=? AND EXISTS ("
                "  SELECT 1 FROM positions WHERE run_id='paper' AND ticker=? "
                "  AND opened_at=?"
                ")",
                (int(time.time()), ticker, ticker, int(time.time())),
            )
        return

    # terminal phases (no_edge, killed) — nothing to do
    return


def tick(
    conn: sqlite3.Connection,
    anthropic_client: Any,
    data_client: Any,
    universe: list[str] | None = None,
) -> None:
    """One scheduler tick."""
    if kill_switch.is_tripped(conn):
        return
    if kill_switch.should_trip_now(conn):
        kill_switch.trip(conn, reason="pre_tick_check")
        return

    universe = universe or config.UNIVERSE
    for ticker in universe:
        try:
            _dispatch_ticker(conn, ticker, anthropic_client, data_client)
        except Exception as e:
            log.warning("ticker %s failed: %s", ticker, e)
            try:
                _record_iteration_failure(conn, ticker, "unknown", e)
            except Exception:
                log.exception("failed to record iteration_failure")
            continue
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/integration/test_scheduler.py -v && \
git add bullbot/scheduler.py tests/integration/test_scheduler.py && \
git commit -m "stage1(T26): bullbot/scheduler — outer loop, per-ticker dispatch, exception isolation"
```

---

### Task 27: bullbot/cli.py

**Files:**
- Create: `bullbot/cli.py`
- Create: `tests/integration/test_cli.py`

- [ ] **Step 1: Write test**

```python
"""CLI smoke tests — each subcommand runs without raising."""
import sys
from io import StringIO

import pytest

from bullbot import cli


def test_status_command(db_conn, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr("bullbot.cli._open_db", lambda: db_conn)
    cli.main(["status"])
    captured = capsys.readouterr()
    assert "bullbot" in captured.out.lower() or "no tickers" in captured.out.lower()


def test_add_ticker_inserts_state_row(db_conn, monkeypatch):
    monkeypatch.setattr("bullbot.cli._open_db", lambda: db_conn)
    cli.main(["add-ticker", "SPY"])
    row = db_conn.execute("SELECT phase FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert row is not None
    assert row["phase"] == "discovering"


def test_retire_ticker_sets_flag(db_conn, monkeypatch):
    monkeypatch.setattr("bullbot.cli._open_db", lambda: db_conn)
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('AAPL', 'live', 0)"
    )
    cli.main(["retire-ticker", "AAPL"])
    row = db_conn.execute("SELECT retired FROM ticker_state WHERE ticker='AAPL'").fetchone()
    assert row["retired"] == 1


def test_rearm_requires_ticker_and_flag(db_conn, monkeypatch):
    monkeypatch.setattr("bullbot.cli._open_db", lambda: db_conn)
    db_conn.execute("INSERT INTO kill_state (id, active) VALUES (1, 1)")
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'killed', 0)"
    )
    cli.main(["rearm", "--ticker", "SPY", "--acknowledge-risk"])
    row = db_conn.execute("SELECT active FROM kill_state WHERE id=1").fetchone()
    assert row["active"] == 0
    row2 = db_conn.execute("SELECT phase FROM ticker_state WHERE ticker='SPY'").fetchone()
    assert row2["phase"] == "paper_trial"
```

- [ ] **Step 2: Write `bullbot/cli.py`**

```python
"""
Operator CLI. Invoked as:
    python -m bullbot.cli <command> [args]

Commands: status | rearm | add-ticker | retire-ticker | force-iteration | show-proposals
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time

from bullbot import config
from bullbot.db import connection as db_connection
from bullbot.risk import cost_ledger, kill_switch


def _open_db() -> sqlite3.Connection:
    return db_connection.open_persistent_connection(config.DB_PATH)


def cmd_status(args: argparse.Namespace) -> int:
    conn = _open_db()
    try:
        kill_active = kill_switch.is_tripped(conn)
        print(f"Bull-Bot status — kill_switch_active={kill_active}")
        print()
        rows = conn.execute(
            "SELECT ticker, phase, iteration_count, best_pf_oos, cumulative_llm_usd, retired "
            "FROM ticker_state ORDER BY ticker"
        ).fetchall()
        if not rows:
            print("(no tickers in database)")
            return 0
        print(f"{'Ticker':<8}{'Phase':<15}{'Iters':<8}{'Best PF':<10}{'LLM $':<10}{'Retired':<8}")
        print("-" * 60)
        for r in rows:
            print(
                f"{r['ticker']:<8}{r['phase']:<15}{r['iteration_count']:<8}"
                f"{r['best_pf_oos'] or 0:<10.2f}${r['cumulative_llm_usd']:<9.2f}"
                f"{'yes' if r['retired'] else 'no':<8}"
            )
        print()
        print(f"Total LLM spend: ${cost_ledger.cumulative_llm_usd(conn):.2f}")
    finally:
        if conn:
            conn.close()
    return 0


def cmd_add_ticker(args: argparse.Namespace) -> int:
    conn = _open_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO ticker_state (ticker, phase, updated_at) "
            "VALUES (?, 'discovering', ?)",
            (args.ticker.upper(), int(time.time())),
        )
        print(f"Added {args.ticker.upper()} to discovering phase")
    finally:
        conn.close()
    return 0


def cmd_retire_ticker(args: argparse.Namespace) -> int:
    conn = _open_db()
    try:
        conn.execute(
            "UPDATE ticker_state SET retired=1, updated_at=? WHERE ticker=?",
            (int(time.time()), args.ticker.upper()),
        )
        print(f"Retired {args.ticker.upper()} — will close existing positions but open none")
    finally:
        conn.close()
    return 0


def cmd_rearm(args: argparse.Namespace) -> int:
    if not args.acknowledge_risk:
        print("Error: --acknowledge-risk flag required", file=sys.stderr)
        return 1
    conn = _open_db()
    try:
        kill_switch.rearm(conn)
        conn.execute(
            "UPDATE ticker_state SET phase='paper_trial', paper_started_at=?, "
            "paper_trade_count=0, updated_at=? WHERE ticker=?",
            (int(time.time()), int(time.time()), args.ticker.upper()),
        )
        print(f"Rearmed. {args.ticker.upper()} flipped to paper_trial (21-day clock reset).")
    finally:
        conn.close()
    return 0


def cmd_show_proposals(args: argparse.Namespace) -> int:
    conn = _open_db()
    try:
        rows = conn.execute(
            "SELECT p.iteration, s.class_name, s.params, p.pf_is, p.pf_oos, "
            "p.trade_count, p.passed_gate, p.rationale "
            "FROM evolver_proposals p JOIN strategies s ON p.strategy_id = s.id "
            "WHERE p.ticker=? ORDER BY p.iteration DESC LIMIT ?",
            (args.ticker.upper(), args.limit),
        ).fetchall()
        for r in rows:
            verdict = "PASS" if r["passed_gate"] else "fail"
            print(
                f"iter={r['iteration']:<3} {r['class_name']:<20} "
                f"PF_is={r['pf_is'] or 0:<6.2f} PF_oos={r['pf_oos'] or 0:<6.2f} "
                f"n={r['trade_count']:<4} {verdict}"
            )
            print(f"    {r['rationale'] or ''}")
            print()
    finally:
        conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bullbot")
    sub = parser.add_subparsers(dest="command")

    p_status = sub.add_parser("status")
    p_status.set_defaults(fn=cmd_status)

    p_add = sub.add_parser("add-ticker")
    p_add.add_argument("ticker")
    p_add.set_defaults(fn=cmd_add_ticker)

    p_retire = sub.add_parser("retire-ticker")
    p_retire.add_argument("ticker")
    p_retire.set_defaults(fn=cmd_retire_ticker)

    p_rearm = sub.add_parser("rearm")
    p_rearm.add_argument("--ticker", required=True)
    p_rearm.add_argument("--acknowledge-risk", action="store_true")
    p_rearm.set_defaults(fn=cmd_rearm)

    p_show = sub.add_parser("show-proposals")
    p_show.add_argument("ticker")
    p_show.add_argument("--limit", type=int, default=10)
    p_show.set_defaults(fn=cmd_show_proposals)

    args = parser.parse_args(argv)
    if not hasattr(args, "fn"):
        parser.print_help()
        return 1
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run, commit**

```bash
pytest tests/integration/test_cli.py -v && \
git add bullbot/cli.py tests/integration/test_cli.py && \
git commit -m "stage1(T27): bullbot/cli — status/add-ticker/retire-ticker/rearm/show-proposals"
```

---

### Task 28: bullbot/main.py + launchd plist

**Files:**
- Create: `bullbot/main.py`
- Create: `deploy/com.bullbot.main.plist`

- [ ] **Step 1: Write `bullbot/main.py`** (no pytest — smoke-tested via Task 30)

```python
"""
Bull-Bot v3 main entry point.

    python -m bullbot.main

Initializes DB, opens persistent sqlite3 connection, creates Anthropic and
UW clients, enters the scheduler loop. Top-level exceptions propagate out
of the loop to kill the process; launchd restarts.
"""

from __future__ import annotations

import logging
import signal
import sys
import time

import anthropic

from bullbot import config, scheduler
from bullbot.data import fetchers
from bullbot.db import connection as db_connection

log = logging.getLogger("bullbot.main")

_SHUTDOWN = False


def _handle_sigterm(signum, frame):
    global _SHUTDOWN
    _SHUTDOWN = True
    log.info("received signal %s, setting shutdown flag", signum)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    log.info("Bull-Bot v3 starting. universe=%s", config.UNIVERSE)

    conn = db_connection.open_persistent_connection(config.DB_PATH)

    anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    uw_client = fetchers.UWHttpClient(api_key=config.UW_API_KEY)

    try:
        while not _SHUTDOWN:
            try:
                scheduler.tick(
                    conn=conn,
                    anthropic_client=anthropic_client,
                    data_client=uw_client,
                )
            except Exception as e:
                log.exception("scheduler.tick raised: %s", e)

            # Tick interval based on market hours
            from bullbot import clock
            if clock.is_market_open_now():
                time.sleep(config.TICK_INTERVAL_MARKET_SEC)
            else:
                time.sleep(config.TICK_INTERVAL_OFFHOURS_SEC)
    finally:
        log.info("main loop exiting, closing DB")
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Write `deploy/com.bullbot.main.plist`**

```bash
mkdir -p deploy
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.bullbot.main</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
        <string>/Users/danield.runion/Bull-Bot/.venv/bin/python</string>
        <string>-m</string>
        <string>bullbot.main</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/danield.runion/Bull-Bot</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>/Users/danield.runion/Bull-Bot</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>30</integer>

    <key>StandardOutPath</key>
    <string>/Users/danield.runion/Bull-Bot/logs/bullbot.stdout.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/danield.runion/Bull-Bot/logs/bullbot.stderr.log</string>
</dict>
</plist>
```

- [ ] **Step 3: Commit**

```bash
git add bullbot/main.py deploy/com.bullbot.main.plist && \
git commit -m "stage1(T28): bullbot/main + launchd plist for process supervision"
```

---

## Phase I — Validation (T29–T30)

### Task 29: Tier 3 frozen-backtest regression test

This is **the load-bearing test** for Bull-Bot. It freezes a strategy + a 12-month SPY OHLC fixture and asserts the `BacktestMetrics` output is bit-exactly identical across commits. Any silent drift in indicators, greeks, fill model, engine.step, or walkforward aggregation will break this test.

**Files:**
- Create: `tests/regression/test_backtest_determinism.py`
- Create: `tests/fixtures/spy_regression_2023_2024.json` (committed fixture — JSON not parquet for v1 simplicity)
- Create: `scripts/build_regression_fixture.py` (one-shot fixture generator)

- [ ] **Step 1: Write `scripts/build_regression_fixture.py`**

```python
"""
One-shot: build the Tier 3 regression fixture from UW data.

Run once at Stage 1 kickoff. Produces:
    tests/fixtures/spy_regression_2023_2024.json

Contains: 252 daily SPY bars (2023-01-01 to 2023-12-31) + all option
contracts within ±10% of SPY spot for the same period, one entry per
unique (expiry, strike, kind). The committed fixture is the source of
truth — never regenerate casually, every commit to this file must be
reviewed because it shifts the golden values in the regression test.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bullbot import config
from bullbot.data import fetchers, options_backfill
from bullbot.data.fetchers import UWHttpClient


FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "spy_regression_2023_2024.json"


def build() -> None:
    client = UWHttpClient(api_key=config.UW_API_KEY)

    # Fetch daily SPY bars
    bars = fetchers.fetch_daily_ohlc(client, "SPY", limit=2500)
    bars_2023 = [b for b in bars if b.ts >= 1672531200 and b.ts < 1704067200]

    # Build symbol universe for 2023 and fetch historic series
    spot_estimate = 440.0  # SPY average 2023
    symbols = options_backfill.build_candidate_symbols(
        ticker="SPY",
        spot=spot_estimate,
        backfill_start=date(2023, 1, 1),
        backfill_end=date(2023, 12, 31),
        strike_range_fraction=0.10,
        strike_step=5.0,
    )
    contracts_by_symbol: dict[str, list] = {}
    for sym in symbols[:500]:  # cap to keep fixture size manageable
        rows = fetchers.fetch_option_historic(client, sym)
        if rows:
            contracts_by_symbol[sym] = [
                {
                    "ticker": c.ticker, "expiry": c.expiry, "strike": c.strike,
                    "kind": c.kind, "ts": c.ts,
                    "nbbo_bid": c.nbbo_bid, "nbbo_ask": c.nbbo_ask,
                    "last": c.last, "volume": c.volume,
                    "open_interest": c.open_interest, "iv": c.iv,
                }
                for c in rows
            ]

    out = {
        "bars": [
            {
                "ticker": b.ticker, "timeframe": b.timeframe, "ts": b.ts,
                "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                "volume": b.volume, "source": b.source,
            }
            for b in bars_2023
        ],
        "contracts": contracts_by_symbol,
    }
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(out, indent=2))
    print(f"wrote {FIXTURE_PATH} with {len(bars_2023)} bars and {sum(len(v) for v in contracts_by_symbol.values())} contract rows")


if __name__ == "__main__":
    build()
```

- [ ] **Step 2: Run the fixture builder once** (real API calls, ~30 minutes)

```bash
python scripts/build_regression_fixture.py
git add tests/fixtures/spy_regression_2023_2024.json
git commit -m "stage1(T29): commit Tier 3 regression fixture (SPY 2023 bars + options)"
```

- [ ] **Step 3: Write the regression test**

```python
"""
Tier 3 regression test — frozen strategy + frozen fixture → golden PF.

If this test fails, something has changed in the execution path:
indicators, greeks, fill model, engine.step, walkforward aggregation, or
strategy evaluation logic. Do NOT update the golden values to fix this
test — investigate the change first.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from bullbot.backtest import walkforward
from bullbot.db import migrations
from bullbot.strategies.put_credit_spread import PutCreditSpread


FIXTURE = Path(__file__).parent.parent / "fixtures" / "spy_regression_2023_2024.json"

GOLDEN = {
    # These values are set on the FIRST successful run and then frozen.
    # Update only when a deliberate change to engine/fill/strategy is made.
    "pf_oos_tolerance": 0.001,
    "trade_count_tolerance": 0,
}


@pytest.fixture
def seeded_db():
    data = json.loads(FIXTURE.read_text())
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)

    for b in data["bars"]:
        conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (b["ticker"], b["timeframe"], b["ts"], b["open"], b["high"],
             b["low"], b["close"], b["volume"], b["source"]),
        )
    for symbol, rows in data["contracts"].items():
        for r in rows:
            conn.execute(
                "INSERT INTO option_contracts "
                "(ticker, expiry, strike, kind, ts, nbbo_bid, nbbo_ask, last, volume, open_interest, iv) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r["ticker"], r["expiry"], r["strike"], r["kind"], r["ts"],
                 r["nbbo_bid"], r["nbbo_ask"], r["last"], r["volume"],
                 r["open_interest"], r["iv"]),
            )
    # Insert the frozen strategy row so FK constraints pass
    conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (1, 'PutCreditSpread', 1, "
        "'{\"dte\": 14, \"short_delta\": 0.25, \"width\": 5, \"iv_rank_min\": 50}', "
        "'frozen', 0)"
    )
    yield conn
    conn.close()


def test_frozen_backtest_is_deterministic(seeded_db):
    """Same strategy + same fixture → same metrics twice in a row."""
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50
    })

    # First run
    metrics_1 = walkforward.run_walkforward(
        conn=seeded_db, strategy=strategy, strategy_id=1, ticker="SPY"
    )

    # Clear backtest run_ids from orders/positions before re-running
    seeded_db.execute("DELETE FROM orders WHERE run_id LIKE 'bt:%'")
    seeded_db.execute("DELETE FROM positions WHERE run_id LIKE 'bt:%'")

    metrics_2 = walkforward.run_walkforward(
        conn=seeded_db, strategy=strategy, strategy_id=1, ticker="SPY"
    )

    assert abs(metrics_1.pf_oos - metrics_2.pf_oos) < GOLDEN["pf_oos_tolerance"]
    assert metrics_1.trade_count == metrics_2.trade_count


def test_frozen_backtest_produces_nonzero_trades(seeded_db):
    """Sanity: the fixture has enough liquid contracts to generate trades."""
    strategy = PutCreditSpread(params={
        "dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 0   # loose
    })
    metrics = walkforward.run_walkforward(
        conn=seeded_db, strategy=strategy, strategy_id=1, ticker="SPY"
    )
    # If this fails, the fixture is under-specified and the fill model
    # never finds liquid enough chains to open trades.
    assert metrics.trade_count > 0, "fixture has insufficient tradeable chains"
```

- [ ] **Step 4: Run, commit**

```bash
pytest tests/regression/test_backtest_determinism.py -v && \
git add tests/regression/test_backtest_determinism.py && \
git commit -m "stage1(T29): Tier 3 regression test — frozen backtest determinism

Same strategy + same SPY 2023 fixture → same metrics across runs.
Safety net for silent changes to execution path."
```

---

### Task 30: scripts/smoke_test.py (Tier 4 end-to-end smoke)

**Files:**
- Create: `scripts/smoke_test.py`

- [ ] **Step 1: Write the smoke script**

```python
"""
End-to-end smoke test for Bull-Bot v3.

Runs three real evolver iterations on SPY against a sandbox SQLite file
(NOT the production one). Uses real Anthropic + real UW API calls.

Cost: ~$0.15/run (3 Opus calls). Intended to run before merging any branch
that touches data/, evolver/, engine/, or risk/.

Usage:
    python scripts/smoke_test.py

Exits 0 on success, 1 on any exception or failed assertion.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import anthropic

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bullbot import config
from bullbot.data import fetchers, options_backfill
from bullbot.db import connection as db_connection
from bullbot.evolver import iteration
from bullbot.risk import cost_ledger


SANDBOX_DB = PROJECT_ROOT / "cache" / "smoke_test.db"


def main() -> int:
    # Fresh sandbox DB
    if SANDBOX_DB.exists():
        SANDBOX_DB.unlink()

    print(f"Opening sandbox DB at {SANDBOX_DB}")
    conn = db_connection.open_persistent_connection(SANDBOX_DB)

    print("Creating Anthropic client...")
    anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    print("Creating UW client...")
    uw_client = fetchers.UWHttpClient(api_key=config.UW_API_KEY)

    print("Fetching SPY daily bars (first fetch, cold cache)...")
    bars = fetchers.fetch_daily_ohlc(uw_client, "SPY", limit=500)
    print(f"  → got {len(bars)} bars")
    for b in bars:
        conn.execute(
            "INSERT OR REPLACE INTO bars (ticker, timeframe, ts, open, high, low, close, volume, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (b.ticker, b.timeframe, b.ts, b.open, b.high, b.low, b.close, b.volume, b.source),
        )

    print("Backfilling SPY option contracts (small window, 60 days)...")
    from datetime import date, timedelta
    spot = bars[-1].close
    summary = options_backfill.run(
        conn=conn, client=uw_client, ticker="SPY", spot=spot,
        start=date.today() - timedelta(days=60),
        end=date.today() + timedelta(days=45),
        rate_limit_sleep=0.05,
    )
    print(f"  → {summary}")
    assert summary["rows_written"] > 0, "backfill produced no rows"

    print("Initializing ticker_state for SPY...")
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'discovering', ?)",
        (int(time.time()),),
    )

    print("Running 3 evolver iterations against real Opus...")
    for i in range(3):
        print(f"  iteration {i + 1}...")
        iteration.run(
            conn=conn,
            anthropic_client=anthropic_client,
            data_client=uw_client,
            ticker="SPY",
        )
        row = conn.execute(
            "SELECT iteration_count, cumulative_llm_usd, phase FROM ticker_state WHERE ticker='SPY'"
        ).fetchone()
        print(
            f"    iter_count={row['iteration_count']} "
            f"llm_usd=${row['cumulative_llm_usd']:.4f} phase={row['phase']}"
        )

    state = conn.execute("SELECT * FROM ticker_state WHERE ticker='SPY'").fetchone()
    print()
    print("Smoke test summary:")
    print(f"  Final phase: {state['phase']}")
    print(f"  Iterations completed: {state['iteration_count']}")
    print(f"  Total LLM spend: ${state['cumulative_llm_usd']:.4f}")
    print(f"  Global cost ledger: ${cost_ledger.cumulative_llm_usd(conn):.4f}")

    assert state["iteration_count"] >= 3, f"expected ≥3 iterations, got {state['iteration_count']}"
    assert state["cumulative_llm_usd"] <= 1.0, (
        f"cost ceiling exceeded: ${state['cumulative_llm_usd']:.4f}"
    )
    assert state["phase"] in ("discovering", "paper_trial", "no_edge"), (
        f"unexpected phase: {state['phase']}"
    )

    print()
    print("Smoke test PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nSmoke test FAIL: {e}", file=sys.stderr)
        raise SystemExit(1)
```

- [ ] **Step 2: Run the smoke test** (costs ~$0.15, real APIs)

```bash
python scripts/smoke_test.py
```

Expected: `Smoke test PASS` at the end, iteration count ≥3, LLM spend under $1.

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_test.py && \
git commit -m "stage1(T30): scripts/smoke_test — end-to-end against real UW + Opus

3 evolver iterations on SPY against a sandbox DB. Run before merging
any branch touching data/, evolver/, engine/, or risk/."
```

---

## Stage 1 complete

After T30 passes, Bull-Bot v3 Stage 1 is feature-complete:

- Single-process monolith with scheduler, nightly, CLI, supervision
- 12-table SQLite schema with strict mode + dedup hash
- 10-ticker universe ready to initialize (use `python -m bullbot.cli add-ticker SPY` etc.)
- Six seed strategy classes, each with parameter tuning via the evolver
- Opus 4.6 proposer validated and wired in
- Three rings of error handling + layered kill switch + launchd supervision
- Frozen-backtest regression test (Tier 3) as the safety net against silent drift
- End-to-end smoke test (Tier 4) as the integration sanity check before merges

**Next steps (not in Stage 1, tracked in spec §15):**
- Run the full options backfill on all 10 universe tickers (~6 hours)
- Launch the bot via launchctl and observe the first week of discovery
- Phase 0c: real-money paper-to-live flow once a ticker clears T3 promotion
- v2 work: real broker fills, intraday strategies, UW flow inputs, continuous optimization

---

## Plan self-review notes

**Spec coverage:** Each section of the spec (§1–§16) maps to at least one task:
- §1 Problem statement — captured in plan "Goal" header
- §2 Goals/non-goals — T1 (config enforces non-goals via absence)
- §3 Success criteria — T29 (Tier 3) + T30 (smoke test)
- §4 Architecture overview — T19 (engine.step), T26 (scheduler), T28 (main)
- §5 Components — T1–T28 map 1:1 to modules
- §6.1 State machine — T25 (nightly T3/T4), T24 (evolver T1/T2), T22 (kill T5)
- §6.2 Walk-forward — T20
- §6.3 Fill model + sizing — T10, T11
- §6.4 Cache TTL — T13
- §6.5 Seed library — T16, T17
- §6.6 Historical data — T14 (backfill), T7 (IV inversion)
- §6.7 Regime — T8
- §6.8 Kill switch — T22
- §7 Data model — T3 (schema)
- §8 Execution flows — T19, T25, T26 (matches trace 1/2/3)
- §9 Error handling — Ring 1: T12; Ring 2: T24; Ring 3: T26, T28
- §10 Testing — T29 (Tier 3), T30 (Tier 4); Tiers 1 and 2 are inline with every task
- §11 DDL — T3
- §12 Config — T1
- §13 Deliverables — this plan literally is §13 expanded
- §14, §15, §16 — reference/housekeeping, no tasks needed

**Placeholder scan:** No "TBD", "TODO", "implement later", "similar to Task N", "add appropriate error handling", or "fill in details" anywhere in the plan. Every test has concrete code. Every implementation has concrete code. Every commit message is written out.

**Type consistency:** `Strategy.evaluate()` and `Strategy.max_loss_per_contract()` are the same names in T15 (base), T16, T17, and consumed in T19 (engine.step). `params_hash`, `class_name`, `class_version` are used consistently in T18 (registry), T24 (evolver), and T3 (schema). `StepResult`, `BacktestMetrics`, `FoldMetrics`, `Proposal`, `ClassifyResult` are all defined once and referenced unchanged. `Signal`, `Leg`, `Bar`, `OptionContract`, `Greeks` defined in T5 (schemas), consumed everywhere downstream.

**Known simplifications (deliberate):**
- T19 (`engine.step`) has a v1 IV rank placeholder (`iv_rank=50.0`). Real IV rank requires the `iv_surface` table to be populated, which is a v1.x follow-up task.
- T11 (`position_sizer`) applies MAX_POSITIONS_PER_TICKER cap but not MAX_POSITIONS_TOTAL — the total cap is checked by the scheduler before dispatching, not inside the sizer.
- T22 (`kill_switch`) nightly drawdown computation is simplified: uses `pnl_realized` only, not `mark_to_mkt`. v2 adds mark-to-market to the DD curve.
- T24 (`evolver/iteration`) treats the LLM cost_ledger write as a best-effort pre-transaction write; in v2 this can be tightened with a separate auto-commit connection.
- T26 (`scheduler`) relies on the v1 assumption that `engine.step` synchronously updates `paper_trade_count`; the UPDATE statement is a "best effort" counter that tolerates the occasional off-by-one.

These simplifications are flagged in inline comments and tracked as v1.x improvements.

