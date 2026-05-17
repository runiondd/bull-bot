# Bull-Bot v2 Phase C.4b — Backtest runner — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `bullbot/v2/backtest/runner.py` — the replay loop that walks one ticker through N historical days, calling the same Phase C agent + validator + exit-rule pipeline against synthesized chains, accumulating trades + per-day mark-to-market into an in-memory `BacktestResult`. Plus an on-disk LLM-response cache so reruns are free (Anthropic budget cap = $5/run, but a cache hit costs $0). After this lands, C.4c can read `BacktestResult` and emit the CSV reports.

**Architecture:** Single public function `backtest(conn, ticker, start, end, starting_nav, llm_client) → BacktestResult`. Iterates trading days, slices bars up to that day, computes signals + S/R + IV, synthesizes a chain via `synth_chain.synthesize`, asks `vehicle.pick` for a decision (cached by input-hash on disk), validates via `vehicle.validate`, opens / closes via `positions` helpers, runs `exits.evaluate` on held positions, marks position to market end-of-day. All state lives in the SQLite conn the caller passes (same schema as forward mode — backtest uses an in-memory or scratch DB). LLM cache is a stdlib `sqlite3` table `backtest_llm_cache(prompt_sha, response_text)` so reruns hit the same cache regardless of process. No new third-party deps.

**Tech Stack:** Python 3.11+, stdlib `datetime` / `hashlib` / `json`, existing `bullbot.v2.{signals, underlying, levels, chains, earnings, vehicle, positions, exits, risk}`, existing `bullbot.v2.backtest.synth_chain` (from C.4a), `pytest`, the `fake_anthropic` fixture for tests. No new third-party libraries. One new SQLite table (`backtest_llm_cache`) added via `migrations.py`.

**Spec reference:** [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md`](../specs/2026-05-16-phase-c-vehicle-agent-design.md) section 4.9 (backtest harness — primary spec). [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md`](../specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md) Tier 1 Finding 3 (event-day IV bump — already in synth_chain from C.4a; runner just passes it through).

---

## Pre-flight assumptions verified before writing tasks

- **All upstream v2 modules are merged** (C.0 schema + positions/risk, C.1 chains, C.2 levels, C.3a earnings, C.3b exits, C.3c vehicle, C.4a synth_chain).
- **`bullbot/v2/backtest/synth_chain.py` exports** `synthesize(ticker, asof_ts, today, spot, underlying_bars, vix_bars, expiries, strikes) -> Chain`. Returns empty `Chain.quotes=[]` when filters strip everything.
- **`vehicle.pick`** accepts `client` kwarg. Pass `fake_anthropic` in tests, real Anthropic client in production. Returns `VehicleDecision` (or pass-decision on error).
- **`exits.evaluate`** signature: `evaluate(conn, *, position, signal, spot, atr_14, today, asof_ts, current_leg_prices)` — runner passes the synthesized chain quotes as `current_leg_prices`.
- **`bars` table** has historical OHLCV for every ticker (loaded by Phase A daily refresh — `bullbot/v2/runner._load_bars` shows the read pattern).
- **`positions.open_position`, `positions.open_for_ticker`, `positions.open_count`** already exist (C.0).
- **No new schema except `backtest_llm_cache`** — a 2-column table for response caching.
- **`bullbot/v2/backtest/` directory exists** (created by C.4a).

---

## File Structure

| Path | Responsibility | Status |
|---|---|---|
| `bullbot/v2/backtest/runner.py` | `BacktestTrade`, `BacktestResult`, `_replay_one_day`, `backtest`, LLM cache helpers. | **Create** |
| `bullbot/db/migrations.py` | Add `backtest_llm_cache` table to the migration block. | **Modify** |
| `tests/unit/test_v2_backtest_runner.py` | Unit tests for cache + `_replay_one_day` + end-to-end short backtest. | **Create** |
| Other v2 modules | Unchanged. | — |

Module size target: < 350 LOC.

---

## Task 1: Schema migration — `backtest_llm_cache` table

**Files:**
- Modify: `bullbot/db/migrations.py` (append `CREATE TABLE IF NOT EXISTS backtest_llm_cache`)
- Create: `tests/unit/test_v2_backtest_runner.py` (initial schema smoke test)

The cache table stores `(prompt_sha, response_text)` so the same vehicle.pick prompt yields the same response across runs. PK on `prompt_sha`. No timestamp column — cache entries don't expire (LLM prompts are deterministic given context, and backtest replays are idempotent on bars).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_v2_backtest_runner.py`:

```python
"""Unit tests for bullbot.v2.backtest.runner."""
from __future__ import annotations

import sqlite3

import pytest

from bullbot.db.migrations import apply_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def test_backtest_llm_cache_table_exists_with_expected_columns(conn):
    cols = _columns(conn, "backtest_llm_cache")
    assert cols == {"prompt_sha", "response_text"}


def test_backtest_llm_cache_pk_rejects_duplicate_prompt_sha(conn):
    conn.execute(
        "INSERT INTO backtest_llm_cache (prompt_sha, response_text) "
        "VALUES ('abc', 'first')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO backtest_llm_cache (prompt_sha, response_text) "
            "VALUES ('abc', 'second')"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_runner.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: backtest_llm_cache`.

- [ ] **Step 3: Add migration**

In `bullbot/db/migrations.py`, find the C.0 Phase C tables block (look for `CREATE TABLE IF NOT EXISTS v2_chain_snapshots`). Insert AFTER the `v2_chain_snapshots` block but BEFORE the `# leaderboard view` comment:

```python
    # Phase C.4b — Backtest LLM response cache. Keys on sha256 of the
    # prompt; reruns hit the cache and pay zero Anthropic cost.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS backtest_llm_cache (
            prompt_sha TEXT PRIMARY KEY,
            response_text TEXT NOT NULL
        );
    """)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_runner.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bullbot/db/migrations.py tests/unit/test_v2_backtest_runner.py
git commit -m "feat(v2/c4b): backtest_llm_cache table for response memoization"
```

---

## Task 2: LLM cache helpers (`_cache_key`, `_cache_get`, `_cache_put`)

**Files:**
- Create: `bullbot/v2/backtest/runner.py`
- Modify: `tests/unit/test_v2_backtest_runner.py` (append cache tests)

Three helpers: `_cache_key(prompt: str) → str` (sha256 hex digest), `_cache_get(conn, key) → str | None`, `_cache_put(conn, key, response)`. The vehicle agent's prompt is the cache key — same context → same key → same response on rerun. SHA collisions are vanishingly unlikely; we accept the lookup-by-sha pattern.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_backtest_runner.py`:

```python
from bullbot.v2.backtest import runner


def test_cache_key_returns_64_char_hex_digest():
    key = runner._cache_key(prompt="any prompt")
    assert len(key) == 64
    # sha256 hex digest only contains [0-9a-f]
    assert all(c in "0123456789abcdef" for c in key)


def test_cache_key_is_deterministic_for_same_input():
    a = runner._cache_key(prompt="hello")
    b = runner._cache_key(prompt="hello")
    assert a == b


def test_cache_key_differs_for_different_inputs():
    a = runner._cache_key(prompt="hello")
    b = runner._cache_key(prompt="world")
    assert a != b


def test_cache_get_returns_none_when_key_absent(conn):
    assert runner._cache_get(conn, key="abc" * 21 + "f") is None


def test_cache_put_then_get_round_trip(conn):
    key = "f" * 64
    runner._cache_put(conn, key=key, response="my response")
    assert runner._cache_get(conn, key=key) == "my response"


def test_cache_put_is_idempotent_on_collision(conn):
    """INSERT OR REPLACE — re-putting same key with new value overwrites."""
    key = "a" * 64
    runner._cache_put(conn, key=key, response="first")
    runner._cache_put(conn, key=key, response="second")
    assert runner._cache_get(conn, key=key) == "second"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bullbot.v2.backtest.runner'`.

- [ ] **Step 3: Implement cache helpers**

Create `bullbot/v2/backtest/runner.py`:

```python
"""Backtest replay runner for v2 Phase C.

Single public entry: backtest(conn, ticker, start, end, starting_nav, llm_client)
-> BacktestResult. Walks one ticker through N historical days, calling the
same Phase C agent + validator + exit-rule pipeline as forward mode but
against chains synthesized from bars via synth_chain.synthesize.

LLM responses are cached on disk (sqlite table backtest_llm_cache) so reruns
of the same backtest cost $0 in Anthropic credits. Cache key is sha256 of
the full LLM prompt.
"""
from __future__ import annotations

import hashlib
import sqlite3


def _cache_key(*, prompt: str) -> str:
    """sha256 hex digest of the full LLM prompt — used as the cache PK."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _cache_get(conn: sqlite3.Connection, *, key: str) -> str | None:
    row = conn.execute(
        "SELECT response_text FROM backtest_llm_cache WHERE prompt_sha=?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return row["response_text"]


def _cache_put(conn: sqlite3.Connection, *, key: str, response: str) -> None:
    """INSERT OR REPLACE so re-running with a new response overwrites
    (typically only useful when developing the prompt template)."""
    conn.execute(
        "INSERT OR REPLACE INTO backtest_llm_cache (prompt_sha, response_text) "
        "VALUES (?, ?)",
        (key, response),
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_runner.py -v`
Expected: PASS (8 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/backtest/runner.py tests/unit/test_v2_backtest_runner.py
git commit -m "feat(v2/c4b): _cache_key / _cache_get / _cache_put — LLM response memoization"
```

---

## Task 3: `BacktestTrade` + `BacktestResult` dataclasses

**Files:**
- Modify: `bullbot/v2/backtest/runner.py` (append dataclasses)
- Modify: `tests/unit/test_v2_backtest_runner.py` (append dataclass tests)

Two records C.4c will consume:
- `BacktestTrade(ticker, structure_kind, intent, opened_ts, closed_ts, close_reason, realized_pnl, rationale)` — one per closed position.
- `BacktestResult(ticker, start_date, end_date, starting_nav, ending_nav, trades, daily_mtm)` where `daily_mtm` is `list[tuple[int, float]]` of `(asof_ts, nav)` snapshots.

`ending_nav` computed as `starting_nav + sum(t.realized_pnl for t in trades) + open_positions_mtm_at_end`. Pure dataclasses; no I/O.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_backtest_runner.py`:

```python
from datetime import date


def test_backtest_trade_rejects_unknown_intent():
    with pytest.raises(ValueError, match="intent must be one of"):
        runner.BacktestTrade(
            ticker="AAPL", structure_kind="long_call", intent="speculate",
            opened_ts=1_700_000_000, closed_ts=1_700_100_000,
            close_reason="profit_target", realized_pnl=50.0, rationale="",
        )


def test_backtest_trade_realized_pnl_can_be_negative():
    trade = runner.BacktestTrade(
        ticker="AAPL", structure_kind="long_call", intent="trade",
        opened_ts=1_700_000_000, closed_ts=1_700_100_000,
        close_reason="stop", realized_pnl=-150.0, rationale="",
    )
    assert trade.realized_pnl == -150.0


def test_backtest_result_total_realized_pnl_sums_trades():
    result = runner.BacktestResult(
        ticker="AAPL", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
        starting_nav=50_000.0, ending_nav=52_000.0,
        trades=[
            runner.BacktestTrade(
                ticker="AAPL", structure_kind="long_call", intent="trade",
                opened_ts=1, closed_ts=2, close_reason="profit_target",
                realized_pnl=300.0, rationale="",
            ),
            runner.BacktestTrade(
                ticker="AAPL", structure_kind="csp", intent="accumulate",
                opened_ts=3, closed_ts=4, close_reason="expired_worthless",
                realized_pnl=200.0, rationale="",
            ),
        ],
        daily_mtm=[],
    )
    assert result.total_realized_pnl() == 500.0


def test_backtest_result_total_realized_pnl_returns_zero_for_no_trades():
    result = runner.BacktestResult(
        ticker="AAPL", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
        starting_nav=50_000.0, ending_nav=50_000.0, trades=[], daily_mtm=[],
    )
    assert result.total_realized_pnl() == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_runner.py -v`
Expected: FAIL — `AttributeError: module 'bullbot.v2.backtest.runner' has no attribute 'BacktestTrade'`.

- [ ] **Step 3: Implement dataclasses**

Append to `bullbot/v2/backtest/runner.py`:

```python
from dataclasses import dataclass, field
from datetime import date as _date

INTENTS = ("trade", "accumulate")


@dataclass(frozen=True)
class BacktestTrade:
    ticker: str
    structure_kind: str
    intent: str
    opened_ts: int
    closed_ts: int
    close_reason: str
    realized_pnl: float
    rationale: str

    def __post_init__(self) -> None:
        if self.intent not in INTENTS:
            raise ValueError(f"intent must be one of {INTENTS}; got {self.intent!r}")


@dataclass
class BacktestResult:
    ticker: str
    start_date: _date
    end_date: _date
    starting_nav: float
    ending_nav: float
    trades: list[BacktestTrade]
    daily_mtm: list[tuple[int, float]]  # (asof_ts, nav)

    def total_realized_pnl(self) -> float:
        return sum(t.realized_pnl for t in self.trades)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_runner.py -v`
Expected: PASS (12 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/backtest/runner.py tests/unit/test_v2_backtest_runner.py
git commit -m "feat(v2/c4b): BacktestTrade + BacktestResult dataclasses with PnL aggregator"
```

---

## Task 4: `_replay_one_day` — single-day pipeline (with cache-aware LLM call)

**Files:**
- Modify: `bullbot/v2/backtest/runner.py` (append `_replay_one_day` + small helpers)
- Modify: `tests/unit/test_v2_backtest_runner.py` (append per-day pipeline tests)

`_replay_one_day(conn, *, ticker, today, asof_ts, starting_nav_today, llm_client) → dict | None` runs ONE simulated day:
1. Slice bars + VIX bars up to today via the bars table.
2. If fewer than 30 bars exist, skip (return None).
3. Compute signal via `signals.generate` is NOT a thing — Phase A signal generation requires the underlying agent. Workaround: call `bullbot.v2.underlying.generate_signal(...)` if exists, else stub. Simpler: build a `DirectionalSignal` directly from bars using `bullbot.v2.underlying.generate` per Phase A convention.
4. Compute S/R via `levels.compute_sr`.
5. Synthesize chain via `synth_chain.synthesize` with stub strikes/expiries (we generate them — Yahoo doesn't run in backtest).
6. If position exists: call `exits.evaluate(...)` with current_leg_prices from synthesized chain.
7. If flat (after exit): call `vehicle.pick(..., client=cached_client)` — cached client wraps the real call so repeat asks hit the cache.
8. If decision == "open": call `vehicle.validate(...)` with synthesized entry_prices. If ok, `positions.open_position`.
9. Mark all open positions to market at end-of-day (compute MtM from synthesized chain).
10. Return `{"action_taken": ..., "trade_closed": Optional[BacktestTrade], "mtm_nav": float}`.

The pre-flight note: this is a LOT in one task. We're going to keep the orchestration tight and defer some complexity (full signal generation, dynamic strike/expiry grid) to simple stubs in this plan; C.4c report layer will surface the gaps.

**Pragmatic scope cut for C.4b runner:** We don't need to fully replicate Phase A's signal logic to test the replay loop. We accept a `signal_fn(bars) → DirectionalSignal` parameter the caller passes in. In tests, we pass a stub returning a fixed signal. In production, the caller passes `bullbot.v2.underlying.generate_signal` (or whatever the C.3-era equivalent is). Same for strike grid: caller passes `strike_grid_fn(spot) → list[float]` returning e.g. `[spot*0.9, spot*0.95, ..., spot*1.1]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_backtest_runner.py`:

```python
from types import SimpleNamespace

from bullbot.v2.signals import DirectionalSignal


def _bar(close, high=None, low=None, ts=0):
    return SimpleNamespace(
        ts=ts, open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=1_000_000,
    )


def _seed_bars(conn, ticker, asof_start_ts, n=60, base_close=100.0):
    """Seed n daily bars into the bars table, ending at asof_start_ts."""
    for i in range(n):
        ts = asof_start_ts - (n - 1 - i) * 86400
        c = base_close + (i * 0.01)
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, 1_000_000)",
            (ticker, ts, c, c + 0.3, c - 0.3, c),
        )
    conn.commit()


def _stub_signal_fn(bars):
    return DirectionalSignal(
        ticker="AAPL", asof_ts=bars[-1].ts, direction="bullish",
        confidence=0.7, horizon_days=30, rationale="stub",
        rules_version="stub",
    )


def _stub_strike_grid_fn(spot):
    return [round(spot + (i * 5)) for i in range(-4, 5)]  # 9 strikes spanning ATM ±20%


def _stub_expiries_fn(today):
    """Two expiries: 33 DTE and 65 DTE."""
    from datetime import timedelta
    return [
        (today + timedelta(days=33)).isoformat(),
        (today + timedelta(days=65)).isoformat(),
    ]


def test_replay_one_day_returns_none_when_too_few_bars(conn, fake_anthropic):
    """No bars seeded → can't compute signal → skip the day."""
    out = runner._replay_one_day(
        conn=conn, ticker="AAPL",
        today=date(2026, 5, 17), asof_ts=1_700_000_000,
        starting_nav_today=50_000.0,
        signal_fn=_stub_signal_fn, strike_grid_fn=_stub_strike_grid_fn,
        expiries_fn=_stub_expiries_fn,
        llm_client=fake_anthropic, llm_cache_conn=conn,
    )
    assert out is None


def test_replay_one_day_opens_position_on_valid_llm_decision(conn, fake_anthropic):
    """Seeded bars + LLM returns valid long_call → position opens, no trade closed yet."""
    import json
    asof = 1_700_000_000
    _seed_bars(conn, "AAPL", asof, n=60, base_close=100.0)
    _seed_bars(conn, "VIX", asof, n=60, base_close=18.0)
    fake_anthropic.queue_response(json.dumps({
        "decision": "open", "intent": "trade", "structure": "long_call",
        "legs": [{"action": "buy", "kind": "call", "strike": 100.0,
                  "expiry": (date(2026, 5, 17).fromordinal(
                      date(2026, 5, 17).toordinal() + 33)).isoformat(),
                  "qty_ratio": 1}],
        "exit_plan": {"profit_target_price": 110.0, "stop_price": 95.0,
                      "time_stop_dte": 21, "assignment_acceptable": False},
        "rationale": "bullish",
    }))
    out = runner._replay_one_day(
        conn=conn, ticker="AAPL",
        today=date(2026, 5, 17), asof_ts=asof,
        starting_nav_today=50_000.0,
        signal_fn=_stub_signal_fn, strike_grid_fn=_stub_strike_grid_fn,
        expiries_fn=_stub_expiries_fn,
        llm_client=fake_anthropic, llm_cache_conn=conn,
    )
    assert out is not None
    assert out["action_taken"] in {"opened", "pass", "held"}
    # Verify a position was actually opened in the DB
    from bullbot.v2 import positions
    open_pos = positions.open_for_ticker(conn, "AAPL")
    if out["action_taken"] == "opened":
        assert open_pos is not None


def test_replay_one_day_uses_llm_cache_on_repeat_call(conn, fake_anthropic):
    """First call hits LLM (queued response). Second call same day → cache hit."""
    import json
    asof = 1_700_000_000
    _seed_bars(conn, "AAPL", asof, n=60, base_close=100.0)
    _seed_bars(conn, "VIX", asof, n=60, base_close=18.0)
    fake_anthropic.queue_response(json.dumps({
        "decision": "pass", "intent": "trade", "structure": "long_call",
        "legs": [], "exit_plan": {}, "rationale": "first call",
    }))
    runner._replay_one_day(
        conn=conn, ticker="AAPL",
        today=date(2026, 5, 17), asof_ts=asof,
        starting_nav_today=50_000.0,
        signal_fn=_stub_signal_fn, strike_grid_fn=_stub_strike_grid_fn,
        expiries_fn=_stub_expiries_fn,
        llm_client=fake_anthropic, llm_cache_conn=conn,
    )
    # Verify cache was populated
    cache_count = conn.execute(
        "SELECT COUNT(*) AS n FROM backtest_llm_cache"
    ).fetchone()["n"]
    assert cache_count == 1
    # Second call SHOULD NOT call the LLM (no new queued response, would error if called)
    runner._replay_one_day(
        conn=conn, ticker="AAPL",
        today=date(2026, 5, 17), asof_ts=asof,
        starting_nav_today=50_000.0,
        signal_fn=_stub_signal_fn, strike_grid_fn=_stub_strike_grid_fn,
        expiries_fn=_stub_expiries_fn,
        llm_client=fake_anthropic, llm_cache_conn=conn,
    )
    # No new cache entry — same key
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM backtest_llm_cache"
    ).fetchone()["n"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_runner.py -v`
Expected: FAIL on 3 new tests — `AttributeError: module 'bullbot.v2.backtest.runner' has no attribute '_replay_one_day'`.

- [ ] **Step 3: Implement `_replay_one_day` + cached LLM client wrapper**

Append to `bullbot/v2/backtest/runner.py`:

```python
import json
from typing import Callable

from bullbot.v2 import positions, exits, vehicle
from bullbot.v2.backtest import synth_chain
from bullbot.v2.chains import _iv_proxy
from bullbot.v2.signals import DirectionalSignal


def _load_bars_up_to(conn: sqlite3.Connection, *, ticker: str, asof_ts: int, limit: int = 400):
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM bars "
        "WHERE ticker=? AND timeframe='1d' AND ts<=? "
        "ORDER BY ts DESC LIMIT ?",
        (ticker, asof_ts, limit),
    ).fetchall()
    from types import SimpleNamespace
    bars = [
        SimpleNamespace(
            ts=r["ts"], open=r["open"], high=r["high"],
            low=r["low"], close=r["close"], volume=r["volume"],
        )
        for r in rows
    ]
    bars.reverse()
    return bars


def _atr_14_simple(bars: list) -> float:
    """ATR-14 from bars (simple average TR). Returns 0.0 when <15 bars."""
    if len(bars) < 15:
        return 0.0
    trs = []
    for i, b in enumerate(bars[-15:]):
        if i == 0:
            continue
        prev_close = bars[-15:][i - 1].close
        trs.append(max(
            b.high - b.low,
            abs(b.high - prev_close),
            abs(b.low - prev_close),
        ))
    return sum(trs) / 14


def _compute_position_mtm(*, position, current_chain) -> float:
    """Sum per-leg mid prices × qty for a position using the current chain.
    Share legs use the latest close (passed in via the caller; for now, use entry_price)."""
    total = 0.0
    for leg in position.legs:
        if leg.kind == "share":
            total += leg.entry_price * leg.qty  # placeholder: caller refines
            continue
        quote = current_chain.find_quote(expiry=leg.expiry, strike=leg.strike, kind=leg.kind)
        if quote is None or quote.mid_price() is None:
            continue
        sign = 1.0 if leg.action == "buy" else -1.0
        total += sign * quote.mid_price() * leg.qty * 100
    return total


def _replay_one_day(
    *,
    conn: sqlite3.Connection,
    ticker: str,
    today: _date,
    asof_ts: int,
    starting_nav_today: float,
    signal_fn: Callable,
    strike_grid_fn: Callable,
    expiries_fn: Callable,
    llm_client: object,
    llm_cache_conn: sqlite3.Connection,
) -> dict | None:
    """Replay one historical day for one ticker.

    Returns dict with `action_taken`, `trade_closed` (Optional[BacktestTrade]),
    `mtm_nav` (float). Returns None when too few bars to compute signal.

    The LLM client is wrapped in a cache check: first call for a given prompt
    hits the real client; subsequent calls with the same prompt hit the cache.
    """
    underlying_bars = _load_bars_up_to(conn, ticker=ticker, asof_ts=asof_ts)
    if len(underlying_bars) < 30:
        return None
    vix_bars = _load_bars_up_to(conn, ticker="VIX", asof_ts=asof_ts, limit=60)
    spot = underlying_bars[-1].close

    signal = signal_fn(underlying_bars)
    iv = _iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    chain = synth_chain.synthesize(
        ticker=ticker, asof_ts=asof_ts, today=today, spot=spot,
        underlying_bars=underlying_bars, vix_bars=vix_bars,
        expiries=expiries_fn(today),
        strikes=strike_grid_fn(spot),
    )

    # 1. Exit evaluation on held position (if any)
    open_pos = positions.open_for_ticker(conn, ticker)
    trade_closed: BacktestTrade | None = None
    if open_pos is not None:
        leg_prices = {}
        for leg in open_pos.legs:
            if leg.kind == "share":
                leg_prices[leg.id] = spot
                continue
            q = chain.find_quote(expiry=leg.expiry, strike=leg.strike, kind=leg.kind)
            if q is not None and q.mid_price() is not None:
                leg_prices[leg.id] = q.mid_price()
        exit_action = exits.evaluate(
            conn, position=open_pos, signal=signal, spot=spot,
            atr_14=_atr_14_simple(underlying_bars),
            today=today, asof_ts=asof_ts,
            current_leg_prices=leg_prices,
        )
        if exit_action.kind != "hold":
            # Materialize a BacktestTrade from the closed position
            closed = positions.load_position(conn, open_pos.id)
            realized = sum(
                ((leg.exit_price or 0) - leg.entry_price) * leg.qty *
                (100 if leg.kind != "share" else 1) *
                (1 if leg.action == "buy" else -1)
                for leg in closed.legs
            )
            trade_closed = BacktestTrade(
                ticker=ticker, structure_kind=closed.structure_kind,
                intent=closed.intent, opened_ts=closed.opened_ts,
                closed_ts=closed.closed_ts or asof_ts,
                close_reason=closed.close_reason or "unknown",
                realized_pnl=realized, rationale=closed.rationale or "",
            )

    # 2. Vehicle pick on flat tickers
    action_taken = "skipped"
    if positions.open_for_ticker(conn, ticker) is None:
        # Build prompt once, check cache, only call LLM on miss
        ctx = vehicle.build_llm_context(
            conn, ticker=ticker, spot=spot, signal=signal,
            bars=underlying_bars, levels=[],
            days_to_earnings=999, earnings_window_active=False,
            iv_rank=0.5, budget_per_trade_usd=starting_nav_today * 0.02,
            asof_ts=asof_ts, per_ticker_concentration_pct=0.0,
            open_positions_count=positions.open_count(conn),
            current_position=None,
        )
        prompt = json.dumps(ctx, sort_keys=True)
        cache_key = _cache_key(prompt=prompt)
        cached = _cache_get(llm_cache_conn, key=cache_key)
        if cached is not None:
            decision = vehicle._parse_llm_response(cached)
            if decision is None:
                decision = vehicle.VehicleDecision(
                    decision="pass", intent="trade", structure="long_call",
                    legs=[], exit_plan={}, rationale="cached parse failed",
                )
        else:
            decision = vehicle.pick(
                conn, ticker=ticker, spot=spot, signal=signal,
                bars=underlying_bars, levels=[],
                days_to_earnings=999, earnings_window_active=False,
                iv_rank=0.5, budget_per_trade_usd=starting_nav_today * 0.02,
                asof_ts=asof_ts, per_ticker_concentration_pct=0.0,
                open_positions_count=positions.open_count(conn),
                client=llm_client,
            )
            _cache_put(llm_cache_conn, key=cache_key, response=json.dumps({
                "decision": decision.decision, "intent": decision.intent,
                "structure": decision.structure,
                "legs": [{"action": l.action, "kind": l.kind, "strike": l.strike,
                          "expiry": l.expiry, "qty_ratio": l.qty_ratio}
                         for l in decision.legs],
                "exit_plan": decision.exit_plan,
                "rationale": decision.rationale,
            }))

        if decision.decision == "open":
            # Build entry_prices dict by indexing legs to BS-priced quotes
            entry_prices = {}
            for idx, spec in enumerate(decision.legs):
                if spec.kind == "share":
                    entry_prices[idx] = spot
                    continue
                q = chain.find_quote(expiry=spec.expiry, strike=spec.strike, kind=spec.kind)
                if q is not None and q.mid_price() is not None:
                    entry_prices[idx] = q.mid_price()
                else:
                    entry_prices[idx] = 0.0
            validation = vehicle.validate(
                decision=decision, spot=spot, today=today,
                nav=starting_nav_today, per_trade_pct=0.02, per_ticker_pct=0.15,
                max_open_positions=12, current_ticker_concentration_dollars=0.0,
                current_open_positions=positions.open_count(conn),
                earnings_window_active=False, entry_prices=entry_prices,
            )
            if validation.ok:
                positions.open_position(
                    conn, ticker=ticker, intent=decision.intent,
                    structure_kind=decision.structure,
                    legs=validation.sized_legs, opened_ts=asof_ts,
                    profit_target_price=decision.exit_plan.get("profit_target_price"),
                    stop_price=decision.exit_plan.get("stop_price"),
                    time_stop_dte=decision.exit_plan.get("time_stop_dte"),
                    assignment_acceptable=bool(decision.exit_plan.get("assignment_acceptable", False)),
                    nearest_leg_expiry_dte=None,
                    rationale=decision.rationale,
                )
                action_taken = "opened"
            else:
                action_taken = "rejected"
        else:
            action_taken = "pass"
    else:
        action_taken = "held"

    # 3. End-of-day MtM
    mtm_total = 0.0
    open_now = positions.open_for_ticker(conn, ticker)
    if open_now is not None:
        mtm_total = _compute_position_mtm(position=open_now, current_chain=chain)

    return {
        "action_taken": action_taken,
        "trade_closed": trade_closed,
        "mtm_nav": starting_nav_today + mtm_total + (trade_closed.realized_pnl if trade_closed else 0.0),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_runner.py -v`
Expected: PASS (15 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/backtest/runner.py tests/unit/test_v2_backtest_runner.py
git commit -m "feat(v2/c4b): _replay_one_day — single-day pipeline with cache-aware LLM call"
```

---

## Task 5: `backtest()` main loop

**Files:**
- Modify: `bullbot/v2/backtest/runner.py` (append `backtest`)
- Modify: `tests/unit/test_v2_backtest_runner.py` (append loop tests)

`backtest(conn, *, ticker, start, end, starting_nav, signal_fn, strike_grid_fn, expiries_fn, llm_client, llm_cache_conn=None) → BacktestResult` iterates trading days from `start` to `end` inclusive, calls `_replay_one_day` per day, accumulates `trades` + `daily_mtm`, computes `ending_nav`. Defaults `llm_cache_conn = conn` (same DB).

Trading-day step: simple +1 calendar-day iteration. Weekends/holidays will simply have no bars and `_replay_one_day` returns None, which we skip. Acceptable for first cut.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_backtest_runner.py`:

```python
def test_backtest_iterates_days_and_skips_when_no_bars(conn, fake_anthropic):
    """No bars at all → empty result, no exceptions."""
    result = runner.backtest(
        conn=conn, ticker="AAPL",
        start=date(2024, 1, 1), end=date(2024, 1, 7),
        starting_nav=50_000.0,
        signal_fn=_stub_signal_fn, strike_grid_fn=_stub_strike_grid_fn,
        expiries_fn=_stub_expiries_fn,
        llm_client=fake_anthropic,
    )
    assert result.ticker == "AAPL"
    assert result.starting_nav == 50_000.0
    assert result.ending_nav == 50_000.0  # no trades, NAV unchanged
    assert result.trades == []
    assert result.daily_mtm == []


def test_backtest_returns_filled_result_with_seeded_bars(conn, fake_anthropic):
    """Seed 60 bars + queue 'pass' responses → backtest completes, daily_mtm populated."""
    import json
    # Seed 60 days ending at 2024-03-15
    end_ts = int(date(2024, 3, 15).strftime("%s"))
    _seed_bars(conn, "AAPL", end_ts, n=60, base_close=100.0)
    _seed_bars(conn, "VIX", end_ts, n=60, base_close=18.0)
    # Queue many "pass" responses — the cache means we only need one
    fake_anthropic.queue_response(json.dumps({
        "decision": "pass", "intent": "trade", "structure": "long_call",
        "legs": [], "exit_plan": {}, "rationale": "no edge",
    }))
    result = runner.backtest(
        conn=conn, ticker="AAPL",
        start=date(2024, 3, 13), end=date(2024, 3, 15),
        starting_nav=50_000.0,
        signal_fn=_stub_signal_fn, strike_grid_fn=_stub_strike_grid_fn,
        expiries_fn=_stub_expiries_fn,
        llm_client=fake_anthropic,
    )
    # 3 days iterated, all "pass" → no trades opened
    assert len(result.trades) == 0
    # daily_mtm should have at least 1 entry (the days with bars)
    assert len(result.daily_mtm) >= 1
    # Ending NAV equals starting NAV since no trades closed
    assert result.ending_nav == 50_000.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_runner.py -v`
Expected: FAIL on 2 new tests — `AttributeError: module 'bullbot.v2.backtest.runner' has no attribute 'backtest'`.

- [ ] **Step 3: Implement `backtest()`**

Append to `bullbot/v2/backtest/runner.py`:

```python
from datetime import timedelta as _timedelta


def backtest(
    *,
    conn: sqlite3.Connection,
    ticker: str,
    start: _date,
    end: _date,
    starting_nav: float,
    signal_fn: Callable,
    strike_grid_fn: Callable,
    expiries_fn: Callable,
    llm_client: object,
    llm_cache_conn: sqlite3.Connection | None = None,
) -> BacktestResult:
    """Replay one ticker through [start, end] calendar days.

    Iterates calendar days; days without bars (weekends, holidays) silently
    skip. LLM calls cached on disk via backtest_llm_cache table — reruns
    over the same input cost $0 in Anthropic credits.
    """
    if llm_cache_conn is None:
        llm_cache_conn = conn

    trades: list[BacktestTrade] = []
    daily_mtm: list[tuple[int, float]] = []
    running_nav = starting_nav

    current = start
    while current <= end:
        asof_ts = int(
            __import__("datetime").datetime(
                current.year, current.month, current.day, 23, 0
            ).timestamp()
        )
        outcome = _replay_one_day(
            conn=conn, ticker=ticker, today=current, asof_ts=asof_ts,
            starting_nav_today=running_nav,
            signal_fn=signal_fn, strike_grid_fn=strike_grid_fn,
            expiries_fn=expiries_fn,
            llm_client=llm_client, llm_cache_conn=llm_cache_conn,
        )
        if outcome is not None:
            if outcome.get("trade_closed") is not None:
                trades.append(outcome["trade_closed"])
                running_nav += outcome["trade_closed"].realized_pnl
            daily_mtm.append((asof_ts, outcome["mtm_nav"]))
        current += _timedelta(days=1)

    return BacktestResult(
        ticker=ticker, start_date=start, end_date=end,
        starting_nav=starting_nav, ending_nav=running_nav,
        trades=trades, daily_mtm=daily_mtm,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_runner.py -v`
Expected: PASS (17 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/backtest/runner.py tests/unit/test_v2_backtest_runner.py
git commit -m "feat(v2/c4b): backtest() main loop — calendar-day iteration with cache reuse"
```

---

## Task 6: Full regression check

**Files:** none.

- [ ] **Step 1: Run full unit suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit -q`
Expected: 781 + 17 = 798 unit tests pass.

- [ ] **Step 2: Run integration suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration -q`
Expected: All 80 integration tests still pass.

- [ ] **Step 3: Static-import sanity check**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -c "from bullbot.v2.backtest import runner; print(runner.backtest, runner.BacktestResult, runner.BacktestTrade, runner._replay_one_day, runner._cache_key)"`
Expected: prints all public + private symbols without ImportError.

- [ ] **Step 4: Optional marker commit**

```bash
git commit --allow-empty -m "chore(v2/c4b): Phase C.4b complete — backtest runner.py landed"
```

---

## Acceptance criteria

C.4b is complete when ALL of the following hold:

1. `bullbot/v2/backtest/runner.py` exists with public exports: `backtest`, `BacktestResult`, `BacktestTrade`. Private helpers `_replay_one_day`, `_cache_key`, `_cache_get`, `_cache_put`.
2. `backtest_llm_cache` table exists in the schema.
3. `tests/unit/test_v2_backtest_runner.py` has 17 tests, all passing.
4. Full unit + integration suite green (no regressions vs C.4a baseline of 781 unit + 80 integration).
5. Module < 350 LOC.
6. No new third-party dependencies.
7. LLM cache hit returns the cached response without making a network call (proven by the cache-reuse test — second call with no queued response would otherwise return empty text).

## What this unblocks

- **C.4c (report.py):** consumes `BacktestResult` to emit per-trade CSV + equity curve CSV + per-vehicle attribution CSV + per-regime attribution CSV.
- **C.5 dashboard:** can show backtest results from the latest `BacktestResult` if persisted (out of C.4b scope; could be added in C.5).

## Notes for the implementer

- **Earnings handling in backtest is hard-coded to `earnings_window_active=False`, `days_to_earnings=999`.** Backtest doesn't have a historical earnings-date source; this is an explicit accepted gap until a historical earnings table is added (would be its own phase).
- **The signal_fn / strike_grid_fn / expiries_fn dependency-injection pattern** lets tests pass stubs and lets production callers wire in `bullbot.v2.underlying.generate_signal` (or whatever the proper Phase A function is).
- **`asof_ts` is computed as `datetime(year, month, day, 23).timestamp()`** — end-of-day local time. This aligns with how Phase A signals get persisted and how chains.fetch_chain works in forward mode.
- **Cache key is sha256 of the full JSON-serialized context dict** (with `sort_keys=True` for determinism). Same context → same key → same response. This is what makes reruns free.
- **`_compute_position_mtm` is a first-pass estimate.** Share legs use `entry_price * qty` as a placeholder; real MtM would mark to current spot. C.4c report layer should refine this when computing per-day equity curves.
- **C.4b runner is INTENTIONALLY mechanical** — it wires together upstream pieces (vehicle.pick, exits.evaluate, synth_chain.synthesize, positions.open_position) without inventing new trading logic. All the actual decision-making is in those upstream modules. This keeps the runner small and the failure modes localized.
- **Worktree `.venv` path:** `/Users/danield.runion/Projects/bull-bot/.venv/bin/python`. Same as prior phases.
