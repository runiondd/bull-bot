# Bull-Bot v2 Phase C.3c — Vehicle agent (`vehicle.py`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `bullbot/v2/vehicle.py` — the LLM-picked entry-decision agent. One main public entry point `pick(...)` that calls Claude Haiku with a structured context JSON and returns a validated `VehicleDecision` ready for the runner to act on. Plus all the deterministic supporting machinery: IV-rank computation, large-move counter, near-ATM liquidity stat, full LLM-context assembler, per-structure sanity validator (Grok T1 #2), and the full validation pipeline that enforces the three risk caps + earnings window + intent-structure match. After this lands, C.5's runner can call exactly two functions per ticker per day — `exits.evaluate` for held positions and `vehicle.pick` for flat tickers.

**Architecture:** Single module that ties together everything from C.0–C.3b. Public surface = `pick()`, `validate()`, `build_llm_context()`, `validate_structure_sanity()`, plus the `VehicleDecision`, `ValidationResult`, `SanityResult`, `LLMContext` dataclasses. LLM client (Anthropic) is injected as a callable, same pattern as Yahoo client in `chains.py` and `earnings.py`; tests use the existing `FakeAnthropicClient` fixture in `tests/conftest.py`. Structure sanity is a per-shape dispatch — bull call spread has different rules than iron condor — but they all share a common return type so the validator pipeline doesn't care about the shape internally. LLM is told to return ONE JSON object matching a documented schema; parser tolerates leading/trailing prose by extracting the first `{...}` block.

**Tech Stack:** Python 3.11+, stdlib `dataclasses` / `json` / `datetime` / `re`, existing `bullbot.v2.{positions,risk,levels,chains,earnings,exits,signals}`, `anthropic` SDK (already in project), `pytest` + the `fake_anthropic` conftest fixture. No new third-party dependencies. No DB schema changes.

**Spec reference:** [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md`](../specs/2026-05-16-phase-c-vehicle-agent-design.md) sections 4.5 (vehicle agent + LLM input JSON + output schema), 4.6 (validation pipeline). [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md`](../specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md) Tier 1 Finding 2 (structure sanity), Tier 2 Finding 5 (large_move + liquidity context), Tier 2 Finding 7 (earnings/IV window enforcement).

---

## Pre-flight assumptions verified before writing tasks

- **C.0 / C.1 / C.2 / C.3a / C.3b are merged** — vehicle.py imports from all of: `positions`, `risk`, `chains`, `levels`, `earnings`, `signals`. Verified via `git log --oneline -20`.
- **`tests/conftest.py:FakeAnthropicClient`** exists with `queue_response(text)` + `client.messages.create(**kwargs)` returning a `_Response` with `.content[0].text`. The fixture `fake_anthropic` is auto-injected by name.
- **`anthropic.Anthropic` SDK** is in the project venv (existing `bullbot/cli.py` uses `import anthropic`). Vehicle agent imports it lazily inside `_default_anthropic_client` so tests don't require the SDK to be available at import time.
- **Risk-cap defaults**: `per_trade_pct=0.02`, `per_ticker_pct=0.15`, `max_open_positions=12` from `bullbot/v2/risk.py`. These flow in as parameters to `validate()` so the runner can override per call.
- **`earnings.earnings_window_active`** already accepts a `client` kwarg for injecting a fake yfinance client.
- **Haiku model ID**: `claude-haiku-4-5-20251001` (per Anthropic's current SKU list — verify against `bullbot/config.py` at impl time and use whatever constant is there).

---

## File Structure

| Path | Responsibility | Status |
|---|---|---|
| `bullbot/v2/vehicle.py` | Public: `pick`, `validate`, `validate_structure_sanity`, `build_llm_context`, dataclasses. Private: IV-rank, large-move, liquidity, qty-from-ratios, LLM JSON parser, per-shape sanity helpers. | **Create** |
| `tests/unit/test_v2_vehicle.py` | Unit tests per task — table-driven sanity tests, JSON schema validation, end-to-end pick with mocked Anthropic. | **Create** |
| Other v2 modules | Unchanged. | — |
| `bullbot/db/migrations.py` | Unchanged. | — |

Module size target: < 500 LOC (largest v2 module — LLM prompt template + 5 shape sanity blocks + validation pipeline). If it goes over, split structure-sanity into `vehicle_sanity.py` in a follow-up.

---

## Task 1: Module skeleton + dataclasses

**Files:**
- Create: `bullbot/v2/vehicle.py`
- Create: `tests/unit/test_v2_vehicle.py`

Three result dataclasses + one input-context dataclass:
- `VehicleDecision` — parsed LLM output (`decision`, `intent`, `structure`, `legs`, `exit_plan`, `rationale`).
- `LegSpec` — per-leg fields the LLM returns (`action`, `kind`, `strike`, `expiry`, `qty_ratio`). Different from `OptionLeg` because the LLM only knows ratios, not absolute qty — those come later in qty sizing.
- `SanityResult` — `ok: bool, reason: str | None`.
- `ValidationResult` — same shape as SanityResult plus optional `sized_legs: list[OptionLeg]` (populated on success).
- `LLMContext` — typed wrapper around the input JSON for clarity (or just keep as `dict` — TBD per impl style).

Plus the `STRUCTURE_KINDS` constant enumerating every shape the LLM may return (per C.0 design + Grok T3 cut excluding calendars/diagonals).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_v2_vehicle.py`:

```python
"""Unit tests for bullbot.v2.vehicle — LLM-picked entry-decision agent."""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.v2 import vehicle, positions
from bullbot.v2.signals import DirectionalSignal


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def test_legspec_round_trip_through_asdict():
    spec = vehicle.LegSpec(
        action="buy", kind="call", strike=100.0, expiry="2026-06-19", qty_ratio=1,
    )
    assert spec.action == "buy"
    assert spec.qty_ratio == 1


def test_vehicle_decision_rejects_unknown_decision_value():
    with pytest.raises(ValueError, match="decision must be one of"):
        vehicle.VehicleDecision(
            decision="maybe", intent="trade", structure="long_call",
            legs=[], exit_plan={}, rationale="",
        )


def test_vehicle_decision_rejects_unknown_intent():
    with pytest.raises(ValueError, match="intent must be one of"):
        vehicle.VehicleDecision(
            decision="open", intent="speculate", structure="long_call",
            legs=[], exit_plan={}, rationale="",
        )


def test_vehicle_decision_rejects_unknown_structure():
    with pytest.raises(ValueError, match="structure must be one of"):
        vehicle.VehicleDecision(
            decision="open", intent="trade", structure="condor_with_diagonal_wings",
            legs=[], exit_plan={}, rationale="",
        )


def test_sanity_result_ok_true_when_no_reason():
    result = vehicle.SanityResult(ok=True, reason=None)
    assert result.ok is True


def test_structure_kinds_excludes_calendars_and_diagonals():
    """Grok review Tier 3 cut: deferred to C.7."""
    assert "calendar" not in vehicle.STRUCTURE_KINDS
    assert "diagonal" not in vehicle.STRUCTURE_KINDS
    # But the supported set IS present
    assert "long_call" in vehicle.STRUCTURE_KINDS
    assert "bull_call_spread" in vehicle.STRUCTURE_KINDS
    assert "iron_condor" in vehicle.STRUCTURE_KINDS
    assert "covered_call" in vehicle.STRUCTURE_KINDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bullbot.v2.vehicle'`.

- [ ] **Step 3: Implement the skeleton**

Create `bullbot/v2/vehicle.py`:

```python
"""LLM-picked entry-decision agent for v2 Phase C.

Public entry: pick(conn, ticker, signal, spot, ..., client=None) -> VehicleDecision.
Internally:
  1. build_llm_context — assemble the rich JSON input the LLM sees
  2. _call_llm — send to Haiku, get JSON back, parse to VehicleDecision
  3. validate — structure sanity + risk caps + earnings + intent match
  4. _compute_qty_from_ratios — scale LLM's qty_ratios via risk.size_position

The LLM picks SHAPE (structure_kind + leg ratios + strikes + expiries +
exit plan). We compute SIZE (actual contract qty) deterministically via
risk.py — prevents the LLM from rounding up against the risk cap.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DECISIONS = ("open", "pass")
INTENTS = ("trade", "accumulate")

STRUCTURE_KINDS = (
    "long_call", "long_put",
    "bull_call_spread", "bear_put_spread",
    "iron_condor", "butterfly",
    "covered_call", "csp",
    "long_shares", "short_shares",
)
# Note: 'calendar' and 'diagonal' deferred to C.7 (Grok review Tier 3 cut).


@dataclass
class LegSpec:
    """One leg as returned by the LLM — has qty_ratio (relative weight),
    not absolute qty. risk.size_position scales to actual contracts later."""
    action: str            # 'buy' | 'sell'
    kind: str              # 'call' | 'put' | 'share'
    strike: float | None
    expiry: str | None     # 'YYYY-MM-DD' or None for shares
    qty_ratio: int


@dataclass
class VehicleDecision:
    decision: str          # 'open' | 'pass'
    intent: str            # 'trade' | 'accumulate'
    structure: str         # one of STRUCTURE_KINDS
    legs: list[LegSpec]
    exit_plan: dict        # {profit_target_price, stop_price, time_stop_dte, assignment_acceptable}
    rationale: str

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(
                f"decision must be one of {DECISIONS}; got {self.decision!r}"
            )
        if self.intent not in INTENTS:
            raise ValueError(
                f"intent must be one of {INTENTS}; got {self.intent!r}"
            )
        if self.structure not in STRUCTURE_KINDS:
            raise ValueError(
                f"structure must be one of {STRUCTURE_KINDS}; got {self.structure!r}"
            )


@dataclass(frozen=True)
class SanityResult:
    ok: bool
    reason: str | None = None


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None = None
    sized_legs: list = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/vehicle.py tests/unit/test_v2_vehicle.py
git commit -m "feat(v2/c3c): LegSpec + VehicleDecision + SanityResult + ValidationResult dataclasses"
```

---

## Task 2: `_iv_rank` — IV percentile from cached chain snapshots

**Files:**
- Modify: `bullbot/v2/vehicle.py` (append `_iv_rank`)
- Modify: `tests/unit/test_v2_vehicle.py` (append IV-rank tests)

IV rank (0..1) for a ticker = where today's near-ATM IV sits in the 252-day trailing range. Read from `v2_chain_snapshots`: per-day median IV across ATM ±5% strikes (any expiry). Today's IV vs the min/max of the last 252 daily medians → rank.

Returns 0.5 (mid-range default) when fewer than 30 days of history exist (not enough sample). Returns 1.0 when only one day of data and today IS that day (degenerate but harmless).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_vehicle.py`:

```python
def _seed_chain_snapshot(conn, *, ticker, asof_ts, strike, kind, iv, spot=100.0):
    """Insert one row into v2_chain_snapshots for the iv_rank tests."""
    conn.execute(
        "INSERT OR REPLACE INTO v2_chain_snapshots "
        "(ticker, asof_ts, expiry, strike, kind, bid, ask, last, iv, oi, source) "
        "VALUES (?, ?, '2026-06-19', ?, ?, 1.0, 1.2, 1.1, ?, 100, 'yahoo')",
        (ticker, asof_ts, strike, kind, iv),
    )
    conn.commit()


def test_iv_rank_returns_default_when_no_history(conn):
    rank = vehicle._iv_rank(conn, ticker="AAPL", asof_ts=1_700_000_000, spot=100.0)
    assert rank == 0.5


def test_iv_rank_returns_default_when_under_30_days_history(conn):
    asof = 1_700_000_000
    for i in range(10):  # only 10 days
        _seed_chain_snapshot(
            conn, ticker="AAPL", asof_ts=asof - i * 86400,
            strike=100.0, kind="call", iv=0.30,
        )
    rank = vehicle._iv_rank(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    assert rank == 0.5


def test_iv_rank_returns_high_when_current_iv_at_top_of_range(conn):
    asof = 1_700_000_000
    # 30 days of low IV (0.20), today at high IV (0.50)
    for i in range(30):
        _seed_chain_snapshot(
            conn, ticker="AAPL", asof_ts=asof - (30 - i) * 86400,
            strike=100.0, kind="call", iv=0.20,
        )
    _seed_chain_snapshot(
        conn, ticker="AAPL", asof_ts=asof,
        strike=100.0, kind="call", iv=0.50,
    )
    rank = vehicle._iv_rank(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    assert rank > 0.95


def test_iv_rank_returns_low_when_current_iv_at_bottom_of_range(conn):
    asof = 1_700_000_000
    for i in range(30):
        _seed_chain_snapshot(
            conn, ticker="AAPL", asof_ts=asof - (30 - i) * 86400,
            strike=100.0, kind="call", iv=0.50,
        )
    _seed_chain_snapshot(
        conn, ticker="AAPL", asof_ts=asof,
        strike=100.0, kind="call", iv=0.20,
    )
    rank = vehicle._iv_rank(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    assert rank < 0.05


def test_iv_rank_filters_to_near_atm_strikes_only(conn):
    """Strikes far from spot (>5% away) are excluded — they wouldn't reflect
    the at-the-money IV anyway."""
    asof = 1_700_000_000
    for i in range(30):
        ts = asof - (30 - i) * 86400
        # Add far-OTM strike with WILDLY different IV — should be ignored
        _seed_chain_snapshot(
            conn, ticker="AAPL", asof_ts=ts,
            strike=200.0, kind="call", iv=2.0,  # noise
        )
        # ATM strike with reasonable IV
        _seed_chain_snapshot(
            conn, ticker="AAPL", asof_ts=ts,
            strike=100.0, kind="call", iv=0.30,
        )
    _seed_chain_snapshot(
        conn, ticker="AAPL", asof_ts=asof,
        strike=100.0, kind="call", iv=0.30,
    )
    rank = vehicle._iv_rank(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    # If far-OTM strike included, today's 0.30 would look LOW (max 2.0).
    # Filtered correctly, today's IV equals the historical median.
    assert 0.3 < rank < 0.7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: FAIL on the 5 new tests with `AttributeError: module 'bullbot.v2.vehicle' has no attribute '_iv_rank'`.

- [ ] **Step 3: Implement `_iv_rank`**

Append to `bullbot/v2/vehicle.py`:

```python
import sqlite3
from statistics import median

ATM_BAND_PCT = 0.05
IV_RANK_LOOKBACK_DAYS = 252
IV_RANK_MIN_HISTORY_DAYS = 30
IV_RANK_DEFAULT = 0.5


def _iv_rank(
    conn: sqlite3.Connection, *, ticker: str, asof_ts: int, spot: float,
) -> float:
    """IV rank in [0.0, 1.0] for `ticker` as of `asof_ts`.

    Method: per-day median IV across ATM ±5% strikes from v2_chain_snapshots,
    over a 252-day trailing window. Today's IV vs (min, max) of the daily
    medians -> rank.

    Returns IV_RANK_DEFAULT (0.5) when fewer than IV_RANK_MIN_HISTORY_DAYS
    of data exist.
    """
    lookback_start_ts = asof_ts - IV_RANK_LOOKBACK_DAYS * 86400
    lo_strike = spot * (1 - ATM_BAND_PCT)
    hi_strike = spot * (1 + ATM_BAND_PCT)

    rows = conn.execute(
        "SELECT asof_ts, iv FROM v2_chain_snapshots "
        "WHERE ticker=? AND asof_ts BETWEEN ? AND ? "
        "AND strike BETWEEN ? AND ? AND iv IS NOT NULL",
        (ticker, lookback_start_ts, asof_ts, lo_strike, hi_strike),
    ).fetchall()

    # Group IVs by asof_ts and take median per day
    by_day: dict[int, list[float]] = {}
    for r in rows:
        by_day.setdefault(r["asof_ts"], []).append(r["iv"])
    daily_medians = sorted(median(ivs) for ivs in by_day.values())

    if len(daily_medians) < IV_RANK_MIN_HISTORY_DAYS:
        return IV_RANK_DEFAULT

    iv_min = daily_medians[0]
    iv_max = daily_medians[-1]
    if iv_max <= iv_min:
        return IV_RANK_DEFAULT

    today_ivs = by_day.get(asof_ts)
    if not today_ivs:
        return IV_RANK_DEFAULT
    today_iv = median(today_ivs)

    return max(0.0, min(1.0, (today_iv - iv_min) / (iv_max - iv_min)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: PASS (11 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/vehicle.py tests/unit/test_v2_vehicle.py
git commit -m "feat(v2/c3c): _iv_rank from cached chain snapshots, ATM-band filtered"
```

---

## Task 3: `_large_move_count_90d` (Grok T2 Finding 5)

**Files:**
- Modify: `bullbot/v2/vehicle.py` (append `_large_move_count_90d`)
- Modify: `tests/unit/test_v2_vehicle.py` (append tests)

Count of bars in the trailing 90 (oldest-to-newest input) where `|close-to-close return| >= 3%` OR `true_range >= 3 × ATR_14`. Surfaced to the LLM as `large_move_count_90d` so the agent prefers defined-risk on twitchy names.

True range = `max(high - low, abs(high - prev_close), abs(low - prev_close))`. ATR_14 = simple average of true range over the trailing 14 bars (Wilder smoothing not required for this proxy — keep it simple).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_vehicle.py`:

```python
from types import SimpleNamespace


def _bar(close, high=None, low=None):
    return SimpleNamespace(
        ts=0, open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=1_000_000,
    )


def test_large_move_count_zero_for_steady_bars():
    bars = [_bar(close=100.0 + i * 0.01) for i in range(100)]  # tiny drift
    assert vehicle._large_move_count_90d(bars) == 0


def test_large_move_count_detects_large_close_to_close_return():
    bars = [_bar(close=100.0) for _ in range(50)]
    # day 30 spikes 5% — should count
    bars[30] = _bar(close=105.0, high=105.5, low=99.5)
    n = vehicle._large_move_count_90d(bars)
    assert n >= 1


def test_large_move_count_detects_large_true_range():
    """Big intra-day range but close near prior close — captured by TR rule."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(50)]
    # day 30: close still 100 but high/low blown out
    bars[30] = _bar(close=100.0, high=110.0, low=90.0)
    n = vehicle._large_move_count_90d(bars)
    assert n >= 1


def test_large_move_count_only_considers_last_90_bars():
    bars = [_bar(close=100.0, high=100.2, low=99.8) for _ in range(120)]
    # spike at idx 5 (outside last 90 = idx 30..120)
    bars[5] = _bar(close=110.0, high=115.0, low=100.0)
    # spike at idx 100 (inside last 90)
    bars[100] = _bar(close=110.0, high=115.0, low=100.0)
    n = vehicle._large_move_count_90d(bars)
    assert n == 1


def test_large_move_count_returns_zero_for_too_few_bars():
    bars = [_bar(close=100.0) for _ in range(5)]
    # 5 bars is below the 14-bar ATR floor; helper returns 0 rather than crashing.
    assert vehicle._large_move_count_90d(bars) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: FAIL on the 5 new tests with `AttributeError`.

- [ ] **Step 3: Implement `_large_move_count_90d`**

Append to `bullbot/v2/vehicle.py`:

```python
LARGE_MOVE_RETURN_PCT = 0.03
LARGE_MOVE_TR_MULT = 3.0
LARGE_MOVE_LOOKBACK = 90
ATR_WINDOW = 14


def _large_move_count_90d(bars: list) -> int:
    """Count of bars in the trailing 90 with |return| >= 3% OR TR >= 3 × ATR_14.
    Returns 0 when fewer than ATR_WINDOW bars exist."""
    if len(bars) < ATR_WINDOW + 1:
        return 0
    recent = bars[-LARGE_MOVE_LOOKBACK:]
    # Compute true range per bar (need prev close)
    trs: list[float] = []
    for i, b in enumerate(recent):
        if i == 0:
            trs.append(b.high - b.low)
            continue
        prev_close = recent[i - 1].close
        trs.append(max(
            b.high - b.low,
            abs(b.high - prev_close),
            abs(b.low - prev_close),
        ))
    atr_14 = sum(trs[-ATR_WINDOW:]) / ATR_WINDOW

    count = 0
    for i, b in enumerate(recent):
        if i == 0:
            continue
        prev_close = recent[i - 1].close
        ret = abs(b.close - prev_close) / prev_close if prev_close > 0 else 0.0
        if ret >= LARGE_MOVE_RETURN_PCT or trs[i] >= LARGE_MOVE_TR_MULT * atr_14:
            count += 1
    return count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: PASS (16 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/vehicle.py tests/unit/test_v2_vehicle.py
git commit -m "feat(v2/c3c): _large_move_count_90d — jump-day counter for LLM context (Grok T2 F5)"
```

---

## Task 4: `_near_atm_liquidity` (Grok T2 Finding 5)

**Files:**
- Modify: `bullbot/v2/vehicle.py` (append `_near_atm_liquidity`)
- Modify: `tests/unit/test_v2_vehicle.py` (append tests)

For the nearest two monthly expiries within ±5% of spot, return:
- `total_oi_within_5pct`: sum of `oi` across all in-band strikes (both calls and puts)
- `spread_avg_pct`: average (ask − bid) / mid across in-band strikes
- `nearest_monthly_expiry`: ISO date string of the nearest monthly expiry, or None

Reads from `v2_chain_snapshots` for the given `(ticker, asof_ts)`. Returns `{"total_oi_within_5pct": 0, "spread_avg_pct": None, "nearest_monthly_expiry": None}` when no data.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_vehicle.py`:

```python
def _seed_chain_with_oi(conn, *, ticker, asof_ts, expiry, strike, kind, bid, ask, oi):
    conn.execute(
        "INSERT OR REPLACE INTO v2_chain_snapshots "
        "(ticker, asof_ts, expiry, strike, kind, bid, ask, last, iv, oi, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'yahoo')",
        (ticker, asof_ts, expiry, strike, kind, bid, ask, (bid + ask) / 2, oi),
    )
    conn.commit()


def test_near_atm_liquidity_returns_zeros_when_no_data(conn):
    out = vehicle._near_atm_liquidity(conn, ticker="XYZ", asof_ts=1_700_000_000, spot=100.0)
    assert out["total_oi_within_5pct"] == 0
    assert out["spread_avg_pct"] is None
    assert out["nearest_monthly_expiry"] is None


def test_near_atm_liquidity_sums_oi_in_band_only(conn):
    asof = 1_700_000_000
    # In-band strikes (95, 100, 105 with spot=100)
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-06-19",
                        strike=100.0, kind="call", bid=2.0, ask=2.2, oi=1000)
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-06-19",
                        strike=104.0, kind="put", bid=1.5, ask=1.7, oi=500)
    # Out-of-band strike (110, > 5% above spot=100)
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-06-19",
                        strike=110.0, kind="call", bid=0.5, ask=0.7, oi=99999)
    out = vehicle._near_atm_liquidity(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    assert out["total_oi_within_5pct"] == 1500  # 1000 + 500, NOT 99999


def test_near_atm_liquidity_computes_average_bid_ask_spread_pct(conn):
    asof = 1_700_000_000
    # Two in-band strikes: spread 10% and 5%, avg = 7.5%
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-06-19",
                        strike=100.0, kind="call", bid=1.0, ask=1.1, oi=100)
    # spread = 0.1/1.05 ≈ 9.52%
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-06-19",
                        strike=100.0, kind="put", bid=2.0, ask=2.1, oi=100)
    # spread = 0.1/2.05 ≈ 4.88%
    out = vehicle._near_atm_liquidity(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    assert out["spread_avg_pct"] is not None
    assert 0.06 < out["spread_avg_pct"] < 0.08  # average ≈ 7.2%


def test_near_atm_liquidity_returns_nearest_expiry(conn):
    asof = 1_700_000_000
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-07-17",
                        strike=100.0, kind="call", bid=2.0, ask=2.2, oi=100)
    _seed_chain_with_oi(conn, ticker="AAPL", asof_ts=asof, expiry="2026-06-19",
                        strike=100.0, kind="call", bid=2.0, ask=2.2, oi=100)
    out = vehicle._near_atm_liquidity(conn, ticker="AAPL", asof_ts=asof, spot=100.0)
    assert out["nearest_monthly_expiry"] == "2026-06-19"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: FAIL on the 4 new tests.

- [ ] **Step 3: Implement `_near_atm_liquidity`**

Append to `bullbot/v2/vehicle.py`:

```python
def _near_atm_liquidity(
    conn: sqlite3.Connection, *, ticker: str, asof_ts: int, spot: float,
) -> dict:
    """For all v2_chain_snapshots rows at (ticker, asof_ts) with strike within
    ATM ±5%: sum oi, compute mean bid-ask spread as % of mid, return nearest expiry.

    Empty dict-style result with zeros / None when no data."""
    lo = spot * (1 - ATM_BAND_PCT)
    hi = spot * (1 + ATM_BAND_PCT)
    rows = conn.execute(
        "SELECT expiry, bid, ask, oi FROM v2_chain_snapshots "
        "WHERE ticker=? AND asof_ts=? AND strike BETWEEN ? AND ?",
        (ticker, asof_ts, lo, hi),
    ).fetchall()
    if not rows:
        return {
            "total_oi_within_5pct": 0,
            "spread_avg_pct": None,
            "nearest_monthly_expiry": None,
        }
    total_oi = sum(int(r["oi"] or 0) for r in rows)
    spreads = []
    for r in rows:
        if r["bid"] is None or r["ask"] is None:
            continue
        mid = (r["bid"] + r["ask"]) / 2
        if mid <= 0:
            continue
        spreads.append((r["ask"] - r["bid"]) / mid)
    spread_avg = sum(spreads) / len(spreads) if spreads else None
    nearest_expiry = min(r["expiry"] for r in rows)
    return {
        "total_oi_within_5pct": total_oi,
        "spread_avg_pct": spread_avg,
        "nearest_monthly_expiry": nearest_expiry,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: PASS (20 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/vehicle.py tests/unit/test_v2_vehicle.py
git commit -m "feat(v2/c3c): _near_atm_liquidity — OI sum + bid-ask spread for LLM context (Grok T2 F5)"
```

---

## Task 5: `build_llm_context` — JSON assembler

**Files:**
- Modify: `bullbot/v2/vehicle.py` (append `build_llm_context`)
- Modify: `tests/unit/test_v2_vehicle.py` (append context-builder tests)

Takes all the raw inputs and produces the dict that becomes the LLM's input JSON. Per design §4.5, includes: ticker, spot, signal, iv_rank, iv_percentile (same as iv_rank for now — separate field reserved for future), atr_14, rsi_14, dist_from_20sma_pct, levels (nearest_resistance/support + all within 5%), days_to_earnings, earnings_window_active, large_move_count_90d, near_atm_liquidity, budget_per_trade_usd, current_position (None for flat), recent_picks_this_ticker, portfolio_state (open_positions, ticker_concentration_pct).

Caller passes `bars`, `chain_snapshots_count`-style derived values, and S/R levels — `build_llm_context` doesn't re-fetch anything; it composes.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_v2_vehicle.py`:

```python
def _sample_signal():
    return DirectionalSignal(
        ticker="AAPL", asof_ts=1_700_000_000, direction="bullish",
        confidence=0.72, horizon_days=30, rationale="50/200 SMA cross",
        rules_version="v1.0",
    )


def test_build_llm_context_assembles_full_input_json(conn):
    bars = [_bar(close=185.0 + (i * 0.05)) for i in range(60)]
    signal = _sample_signal()
    ctx = vehicle.build_llm_context(
        conn,
        ticker="AAPL", spot=185.42, signal=signal,
        bars=bars, levels=[], days_to_earnings=23,
        earnings_window_active=False, iv_rank=0.34,
        budget_per_trade_usd=1500.0, asof_ts=1_700_000_000,
        nav=50_000.0,
        per_ticker_concentration_pct=0.0,
        open_positions_count=7,
    )
    assert ctx["ticker"] == "AAPL"
    assert ctx["spot"] == 185.42
    assert ctx["signal"]["direction"] == "bullish"
    assert ctx["signal"]["confidence"] == 0.72
    assert ctx["iv_rank"] == 0.34
    assert ctx["days_to_earnings"] == 23
    assert ctx["earnings_window_active"] is False
    assert ctx["budget_per_trade_usd"] == 1500.0
    assert ctx["current_position"] is None
    assert ctx["portfolio_state"]["open_positions"] == 7
    assert ctx["portfolio_state"]["ticker_concentration_pct"] == 0.0
    # large_move + liquidity stats included (computed inline)
    assert "large_move_count_90d" in ctx
    assert "near_atm_liquidity" in ctx


def test_build_llm_context_includes_current_position_when_held(conn):
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=190.0, expiry="2026-06-19",
        qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="long_call",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=200.0, stop_price=180.0,
        time_stop_dte=21, assignment_acceptable=False,
        nearest_leg_expiry_dte=30, rationale="",
    )
    bars = [_bar(close=185.0) for _ in range(60)]
    ctx = vehicle.build_llm_context(
        conn,
        ticker="AAPL", spot=185.42, signal=_sample_signal(),
        bars=bars, levels=[], days_to_earnings=23,
        earnings_window_active=False, iv_rank=0.34,
        budget_per_trade_usd=1500.0, asof_ts=1_700_000_000,
        nav=50_000.0,
        per_ticker_concentration_pct=0.02,
        open_positions_count=8,
        current_position=pos,
    )
    assert ctx["current_position"] is not None
    assert ctx["current_position"]["structure_kind"] == "long_call"
    assert ctx["current_position"]["intent"] == "trade"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: FAIL on the 2 new tests.

- [ ] **Step 3: Implement `build_llm_context`**

Append to `bullbot/v2/vehicle.py`:

```python
from bullbot.v2.positions import Position
from bullbot.v2.signals import DirectionalSignal


def build_llm_context(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    spot: float,
    signal: DirectionalSignal,
    bars: list,
    levels: list,
    days_to_earnings: int,
    earnings_window_active: bool,
    iv_rank: float,
    budget_per_trade_usd: float,
    asof_ts: int,
    nav: float,
    per_ticker_concentration_pct: float,
    open_positions_count: int,
    current_position: Position | None = None,
) -> dict:
    """Assemble the rich JSON input the LLM sees on a flat-ticker pick call.
    Pure composition — no I/O beyond reading v2_chain_snapshots via
    _near_atm_liquidity (caller already pre-fetched bars, levels, iv_rank,
    days_to_earnings, earnings_window_active)."""
    current_pos_repr = None
    if current_position is not None:
        current_pos_repr = {
            "structure_kind": current_position.structure_kind,
            "intent": current_position.intent,
            "days_held": (asof_ts - current_position.opened_ts) // 86400,
        }
    return {
        "ticker": ticker,
        "spot": spot,
        "signal": {
            "direction": signal.direction,
            "confidence": signal.confidence,
            "horizon_days": signal.horizon_days,
        },
        "iv_rank": iv_rank,
        "iv_percentile": iv_rank,  # placeholder: separate calc may diverge later
        "levels": [
            {"price": lvl.price, "kind": lvl.kind, "strength": lvl.strength}
            for lvl in levels
        ],
        "days_to_earnings": days_to_earnings,
        "earnings_window_active": earnings_window_active,
        "large_move_count_90d": _large_move_count_90d(bars),
        "near_atm_liquidity": _near_atm_liquidity(
            conn, ticker=ticker, asof_ts=asof_ts, spot=spot,
        ),
        "budget_per_trade_usd": budget_per_trade_usd,
        "current_position": current_pos_repr,
        "recent_picks_this_ticker": [],  # populated by C.5 runner from v2_positions history
        "portfolio_state": {
            "open_positions": open_positions_count,
            "ticker_concentration_pct": per_ticker_concentration_pct,
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: PASS (22 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/vehicle.py tests/unit/test_v2_vehicle.py
git commit -m "feat(v2/c3c): build_llm_context — JSON assembly for vehicle agent"
```

---

## Task 6: `validate_structure_sanity` — single-leg shapes (long_call/put, csp, shares)

**Files:**
- Modify: `bullbot/v2/vehicle.py` (append `validate_structure_sanity` + single-leg dispatch)
- Modify: `tests/unit/test_v2_vehicle.py` (append single-leg sanity tests)

Per Grok T1 #2, the LLM may return structurally-broken legs. Sanity validator dispatches by `structure_kind`:
- `long_call`: 1 leg, action=buy, kind=call, expiry >= today+7, strike within ±25% of spot.
- `long_put`: 1 leg, action=buy, kind=put, same expiry/moneyness rules.
- `csp`: 1 leg, action=sell, kind=put.
- `long_shares`: 1 leg, action=buy, kind=share, strike None, expiry None.
- `short_shares`: 1 leg, action=sell, kind=share, strike None, expiry None.

Each branch returns `SanityResult(ok=False, reason="...")` on violation, `SanityResult(ok=True)` on pass.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_vehicle.py`:

```python
def _spec(action, kind, strike, expiry, qty_ratio=1):
    return vehicle.LegSpec(action=action, kind=kind, strike=strike, expiry=expiry, qty_ratio=qty_ratio)


def test_sanity_long_call_valid(conn):
    legs = [_spec("buy", "call", 100.0, "2026-06-19")]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="long_call",
        today=date(2026, 5, 17),
    )
    assert result.ok is True


def test_sanity_long_call_rejects_wrong_leg_count():
    legs = [_spec("buy", "call", 100.0, "2026-06-19"),
            _spec("buy", "call", 105.0, "2026-06-19")]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="long_call",
        today=date(2026, 5, 17),
    )
    assert result.ok is False
    assert "leg" in result.reason.lower()


def test_sanity_long_call_rejects_sell_action():
    legs = [_spec("sell", "call", 100.0, "2026-06-19")]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="long_call",
        today=date(2026, 5, 17),
    )
    assert result.ok is False


def test_sanity_long_call_rejects_too_short_expiry():
    """Options expiring in less than 7 days from today are rejected at entry."""
    legs = [_spec("buy", "call", 100.0, "2026-05-20")]  # 3 days out
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="long_call",
        today=date(2026, 5, 17),
    )
    assert result.ok is False
    assert "expiry" in result.reason.lower()


def test_sanity_long_call_rejects_far_OTM_strike():
    """Strike more than 25% from spot is rejected (LLM hallucination guard)."""
    legs = [_spec("buy", "call", 200.0, "2026-06-19")]  # spot=100, +100%
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="long_call",
        today=date(2026, 5, 17),
    )
    assert result.ok is False
    assert "moneyness" in result.reason.lower() or "strike" in result.reason.lower()


def test_sanity_csp_valid():
    legs = [_spec("sell", "put", 95.0, "2026-06-19")]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="csp",
        today=date(2026, 5, 17),
    )
    assert result.ok is True


def test_sanity_csp_rejects_buy_action():
    legs = [_spec("buy", "put", 95.0, "2026-06-19")]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="csp",
        today=date(2026, 5, 17),
    )
    assert result.ok is False


def test_sanity_long_shares_valid():
    legs = [_spec("buy", "share", None, None)]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="long_shares",
        today=date(2026, 5, 17),
    )
    assert result.ok is True


def test_sanity_long_shares_rejects_strike_or_expiry_set():
    legs = [_spec("buy", "share", 100.0, None)]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="long_shares",
        today=date(2026, 5, 17),
    )
    assert result.ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: FAIL on the 9 new tests with `AttributeError: module 'bullbot.v2.vehicle' has no attribute 'validate_structure_sanity'`.

- [ ] **Step 3: Implement single-leg sanity**

Append to `bullbot/v2/vehicle.py`:

```python
from datetime import date as _date

MIN_DTE = 7
MAX_STRIKE_DEVIATION_PCT = 0.25


def _check_expiry_min_dte(expiry: str, today: _date) -> SanityResult | None:
    exp = _date.fromisoformat(expiry)
    if (exp - today).days < MIN_DTE:
        return SanityResult(ok=False, reason=f"expiry {expiry} too soon (< {MIN_DTE} DTE)")
    return None


def _check_moneyness(strike: float, spot: float) -> SanityResult | None:
    if abs(strike - spot) / spot > MAX_STRIKE_DEVIATION_PCT:
        return SanityResult(
            ok=False,
            reason=f"strike {strike} moneyness > {MAX_STRIKE_DEVIATION_PCT:.0%} from spot {spot}",
        )
    return None


def validate_structure_sanity(
    *,
    legs: list[LegSpec],
    spot: float,
    structure_kind: str,
    today: _date,
) -> SanityResult:
    """Dispatch by structure_kind. Returns SanityResult(ok=False, reason=...)
    on any structural violation (wrong leg count, wrong action/kind, bad strikes
    or expiries, broken ratios). Returns SanityResult(ok=True) on pass.

    Grok review Tier 1 Finding 2 — runs BEFORE any chain lookup or risk math.
    """
    if structure_kind in ("long_call", "long_put"):
        if len(legs) != 1:
            return SanityResult(ok=False, reason=f"{structure_kind} requires exactly 1 leg")
        leg = legs[0]
        expected_kind = "call" if structure_kind == "long_call" else "put"
        if leg.action != "buy" or leg.kind != expected_kind:
            return SanityResult(ok=False, reason=f"{structure_kind} requires buy {expected_kind}")
        bad = _check_expiry_min_dte(leg.expiry, today)
        if bad: return bad
        bad = _check_moneyness(leg.strike, spot)
        if bad: return bad
        return SanityResult(ok=True)

    if structure_kind == "csp":
        if len(legs) != 1:
            return SanityResult(ok=False, reason="csp requires exactly 1 leg")
        leg = legs[0]
        if leg.action != "sell" or leg.kind != "put":
            return SanityResult(ok=False, reason="csp requires sell put")
        bad = _check_expiry_min_dte(leg.expiry, today)
        if bad: return bad
        bad = _check_moneyness(leg.strike, spot)
        if bad: return bad
        return SanityResult(ok=True)

    if structure_kind in ("long_shares", "short_shares"):
        if len(legs) != 1:
            return SanityResult(ok=False, reason=f"{structure_kind} requires exactly 1 leg")
        leg = legs[0]
        expected_action = "buy" if structure_kind == "long_shares" else "sell"
        if leg.action != expected_action or leg.kind != "share":
            return SanityResult(ok=False, reason=f"{structure_kind} requires {expected_action} share")
        if leg.strike is not None or leg.expiry is not None:
            return SanityResult(ok=False, reason=f"{structure_kind} requires strike=None and expiry=None")
        return SanityResult(ok=True)

    # Multi-leg sanity arrives in Tasks 7, 8, 9.
    return SanityResult(ok=False, reason=f"sanity for {structure_kind} not yet implemented")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: PASS (31 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/vehicle.py tests/unit/test_v2_vehicle.py
git commit -m "feat(v2/c3c): validate_structure_sanity for single-leg shapes (Grok T1 F2 part 1)"
```

---

## Task 7: `validate_structure_sanity` — vertical spreads

**Files:**
- Modify: `bullbot/v2/vehicle.py` (extend `validate_structure_sanity`)
- Modify: `tests/unit/test_v2_vehicle.py` (append vertical-spread tests)

Per design §4.6 sanity rules for verticals:
- `bull_call_spread`: 2 calls, same expiry, qty_ratio 1:1, long strike < short strike, both in moneyness band.
- `bear_put_spread`: 2 puts, same expiry, qty_ratio 1:1, long strike > short strike, both in moneyness band.

Note: bull-put-credit and bear-call-credit spreads aren't in the structure menu (the LLM picks one of the 10 STRUCTURE_KINDS). If we later add them, they get their own sanity branch.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_vehicle.py`:

```python
def test_sanity_bull_call_spread_valid():
    legs = [
        _spec("buy", "call", 100.0, "2026-06-19"),
        _spec("sell", "call", 105.0, "2026-06-19"),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="bull_call_spread",
        today=date(2026, 5, 17),
    )
    assert result.ok is True


def test_sanity_bull_call_spread_rejects_inverted_strikes():
    """Bull call: long strike MUST be lower than short strike."""
    legs = [
        _spec("buy", "call", 105.0, "2026-06-19"),
        _spec("sell", "call", 100.0, "2026-06-19"),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="bull_call_spread",
        today=date(2026, 5, 17),
    )
    assert result.ok is False
    assert "strike" in result.reason.lower()


def test_sanity_bull_call_spread_rejects_mismatched_expiries():
    legs = [
        _spec("buy", "call", 100.0, "2026-06-19"),
        _spec("sell", "call", 105.0, "2026-07-17"),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="bull_call_spread",
        today=date(2026, 5, 17),
    )
    assert result.ok is False
    assert "expir" in result.reason.lower()


def test_sanity_bull_call_spread_rejects_wrong_kind():
    legs = [
        _spec("buy", "put", 100.0, "2026-06-19"),
        _spec("sell", "call", 105.0, "2026-06-19"),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="bull_call_spread",
        today=date(2026, 5, 17),
    )
    assert result.ok is False


def test_sanity_bear_put_spread_valid():
    legs = [
        _spec("buy", "put", 100.0, "2026-06-19"),
        _spec("sell", "put", 95.0, "2026-06-19"),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="bear_put_spread",
        today=date(2026, 5, 17),
    )
    assert result.ok is True


def test_sanity_bear_put_spread_rejects_inverted_strikes():
    """Bear put: long strike MUST be higher than short strike."""
    legs = [
        _spec("buy", "put", 95.0, "2026-06-19"),
        _spec("sell", "put", 100.0, "2026-06-19"),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="bear_put_spread",
        today=date(2026, 5, 17),
    )
    assert result.ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: FAIL on the 6 new tests (each returns "sanity for X not yet implemented").

- [ ] **Step 3: Add vertical-spread sanity**

Insert before the final "Multi-leg sanity" placeholder in `validate_structure_sanity`:

```python
    if structure_kind == "bull_call_spread":
        if len(legs) != 2:
            return SanityResult(ok=False, reason="bull_call_spread requires 2 legs")
        if any(leg.kind != "call" for leg in legs):
            return SanityResult(ok=False, reason="bull_call_spread requires both legs to be calls")
        if {leg.action for leg in legs} != {"buy", "sell"}:
            return SanityResult(ok=False, reason="bull_call_spread requires one buy + one sell")
        if legs[0].expiry != legs[1].expiry:
            return SanityResult(ok=False, reason="bull_call_spread requires matching expiries")
        buy = next(l for l in legs if l.action == "buy")
        sell = next(l for l in legs if l.action == "sell")
        if buy.strike >= sell.strike:
            return SanityResult(
                ok=False,
                reason=f"bull_call_spread requires long strike < short strike (got {buy.strike} >= {sell.strike})",
            )
        bad = _check_expiry_min_dte(buy.expiry, today)
        if bad: return bad
        for leg in legs:
            bad = _check_moneyness(leg.strike, spot)
            if bad: return bad
        return SanityResult(ok=True)

    if structure_kind == "bear_put_spread":
        if len(legs) != 2:
            return SanityResult(ok=False, reason="bear_put_spread requires 2 legs")
        if any(leg.kind != "put" for leg in legs):
            return SanityResult(ok=False, reason="bear_put_spread requires both legs to be puts")
        if {leg.action for leg in legs} != {"buy", "sell"}:
            return SanityResult(ok=False, reason="bear_put_spread requires one buy + one sell")
        if legs[0].expiry != legs[1].expiry:
            return SanityResult(ok=False, reason="bear_put_spread requires matching expiries")
        buy = next(l for l in legs if l.action == "buy")
        sell = next(l for l in legs if l.action == "sell")
        if buy.strike <= sell.strike:
            return SanityResult(
                ok=False,
                reason=f"bear_put_spread requires long strike > short strike (got {buy.strike} <= {sell.strike})",
            )
        bad = _check_expiry_min_dte(buy.expiry, today)
        if bad: return bad
        for leg in legs:
            bad = _check_moneyness(leg.strike, spot)
            if bad: return bad
        return SanityResult(ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: PASS (37 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/vehicle.py tests/unit/test_v2_vehicle.py
git commit -m "feat(v2/c3c): validate_structure_sanity for vertical spreads (Grok T1 F2 part 2)"
```

---

## Task 8: `validate_structure_sanity` — iron condor + butterfly

**Files:**
- Modify: `bullbot/v2/vehicle.py`
- Modify: `tests/unit/test_v2_vehicle.py`

- `iron_condor`: 4 legs (2 calls + 2 puts), same expiry, sell strikes inside buy strikes, no overlapping wings (short_put < long_call), qty_ratio 1:1:1:1.
- `butterfly` (long): 3 strikes, all same kind (call or put), same expiry, qty_ratio 1:2:1 with middle strike sold and equidistant from low/high wings (within 5% asymmetric tolerance).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_vehicle.py`:

```python
def test_sanity_iron_condor_valid():
    legs = [
        _spec("sell", "put", 95.0, "2026-06-19"),
        _spec("buy", "put", 90.0, "2026-06-19"),
        _spec("sell", "call", 105.0, "2026-06-19"),
        _spec("buy", "call", 110.0, "2026-06-19"),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="iron_condor",
        today=date(2026, 5, 17),
    )
    assert result.ok is True


def test_sanity_iron_condor_rejects_wrong_leg_count():
    legs = [
        _spec("sell", "put", 95.0, "2026-06-19"),
        _spec("buy", "put", 90.0, "2026-06-19"),
        _spec("sell", "call", 105.0, "2026-06-19"),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="iron_condor",
        today=date(2026, 5, 17),
    )
    assert result.ok is False


def test_sanity_iron_condor_rejects_overlapping_wings():
    """Short put strike must be < short call strike (no overlap)."""
    legs = [
        _spec("sell", "put", 108.0, "2026-06-19"),  # too high — overlaps call side
        _spec("buy", "put", 103.0, "2026-06-19"),
        _spec("sell", "call", 105.0, "2026-06-19"),
        _spec("buy", "call", 110.0, "2026-06-19"),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="iron_condor",
        today=date(2026, 5, 17),
    )
    assert result.ok is False


def test_sanity_long_butterfly_valid():
    legs = [
        _spec("buy", "call", 95.0, "2026-06-19", qty_ratio=1),
        _spec("sell", "call", 100.0, "2026-06-19", qty_ratio=2),
        _spec("buy", "call", 105.0, "2026-06-19", qty_ratio=1),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="butterfly",
        today=date(2026, 5, 17),
    )
    assert result.ok is True


def test_sanity_long_butterfly_rejects_wrong_qty_ratio():
    legs = [
        _spec("buy", "call", 95.0, "2026-06-19", qty_ratio=1),
        _spec("sell", "call", 100.0, "2026-06-19", qty_ratio=3),  # should be 2
        _spec("buy", "call", 105.0, "2026-06-19", qty_ratio=1),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="butterfly",
        today=date(2026, 5, 17),
    )
    assert result.ok is False
    assert "ratio" in result.reason.lower() or "qty" in result.reason.lower()


def test_sanity_long_butterfly_rejects_asymmetric_wings():
    """Wings must be equidistant (within 5% tolerance) for a clean butterfly."""
    legs = [
        _spec("buy", "call", 95.0, "2026-06-19", qty_ratio=1),
        _spec("sell", "call", 100.0, "2026-06-19", qty_ratio=2),
        _spec("buy", "call", 115.0, "2026-06-19", qty_ratio=1),  # 15 vs 5 — asymmetric
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="butterfly",
        today=date(2026, 5, 17),
    )
    assert result.ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: FAIL on the 6 new tests.

- [ ] **Step 3: Add iron condor + butterfly sanity**

Insert before the final "Multi-leg sanity" placeholder in `validate_structure_sanity`:

```python
    if structure_kind == "iron_condor":
        if len(legs) != 4:
            return SanityResult(ok=False, reason="iron_condor requires 4 legs")
        if len({leg.expiry for leg in legs}) != 1:
            return SanityResult(ok=False, reason="iron_condor requires all legs same expiry")
        calls = [l for l in legs if l.kind == "call"]
        puts = [l for l in legs if l.kind == "put"]
        if len(calls) != 2 or len(puts) != 2:
            return SanityResult(ok=False, reason="iron_condor requires 2 calls + 2 puts")
        if {l.action for l in calls} != {"buy", "sell"}:
            return SanityResult(ok=False, reason="iron_condor call wing requires 1 buy + 1 sell")
        if {l.action for l in puts} != {"buy", "sell"}:
            return SanityResult(ok=False, reason="iron_condor put wing requires 1 buy + 1 sell")
        short_put = next(l for l in puts if l.action == "sell")
        short_call = next(l for l in calls if l.action == "sell")
        long_put = next(l for l in puts if l.action == "buy")
        long_call = next(l for l in calls if l.action == "buy")
        # Put wing: long strike < short strike (puts -> long is lower-of-pair on a credit-iron condor put side)
        if long_put.strike >= short_put.strike:
            return SanityResult(
                ok=False,
                reason="iron_condor put wing requires long strike < short strike",
            )
        # Call wing: short strike < long strike
        if short_call.strike >= long_call.strike:
            return SanityResult(
                ok=False,
                reason="iron_condor call wing requires short strike < long strike",
            )
        # No overlap between put and call wings
        if short_put.strike >= short_call.strike:
            return SanityResult(
                ok=False,
                reason=f"iron_condor wings overlap: short_put {short_put.strike} >= short_call {short_call.strike}",
            )
        bad = _check_expiry_min_dte(legs[0].expiry, today)
        if bad: return bad
        for leg in legs:
            bad = _check_moneyness(leg.strike, spot)
            if bad: return bad
        return SanityResult(ok=True)

    if structure_kind == "butterfly":
        if len(legs) != 3:
            return SanityResult(ok=False, reason="butterfly requires 3 legs")
        if len({leg.kind for leg in legs}) != 1:
            return SanityResult(ok=False, reason="butterfly requires all legs same kind")
        if len({leg.expiry for leg in legs}) != 1:
            return SanityResult(ok=False, reason="butterfly requires all legs same expiry")
        sorted_legs = sorted(legs, key=lambda l: l.strike)
        low, mid, high = sorted_legs
        if low.action != "buy" or mid.action != "sell" or high.action != "buy":
            return SanityResult(ok=False, reason="butterfly requires buy/sell/buy across low/mid/high strikes")
        if low.qty_ratio != 1 or mid.qty_ratio != 2 or high.qty_ratio != 1:
            return SanityResult(
                ok=False,
                reason=f"butterfly requires qty_ratio 1:2:1 (got {low.qty_ratio}:{mid.qty_ratio}:{high.qty_ratio})",
            )
        # Wings must be near-equidistant (within 5% tolerance)
        low_wing = mid.strike - low.strike
        high_wing = high.strike - mid.strike
        if low_wing <= 0 or high_wing <= 0:
            return SanityResult(ok=False, reason="butterfly wings must be positive")
        wing_diff_pct = abs(low_wing - high_wing) / max(low_wing, high_wing)
        if wing_diff_pct > 0.05:
            return SanityResult(
                ok=False,
                reason=f"butterfly wings too asymmetric ({low_wing} vs {high_wing}, {wing_diff_pct:.1%} diff)",
            )
        bad = _check_expiry_min_dte(legs[0].expiry, today)
        if bad: return bad
        for leg in legs:
            bad = _check_moneyness(leg.strike, spot)
            if bad: return bad
        return SanityResult(ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: PASS (43 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/vehicle.py tests/unit/test_v2_vehicle.py
git commit -m "feat(v2/c3c): validate_structure_sanity for iron_condor + butterfly (Grok T1 F2 part 3)"
```

---

## Task 9: `validate_structure_sanity` — covered_call

**Files:**
- Modify: `bullbot/v2/vehicle.py`
- Modify: `tests/unit/test_v2_vehicle.py`

Covered call: 1 long share leg + 1 short call leg, share qty = call qty × 100, call expiry valid, call strike within moneyness band.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_vehicle.py`:

```python
def test_sanity_covered_call_valid():
    # Note: LegSpec qty_ratio for covered call should be 100:1 share:contract,
    # but per project convention vehicle.py LLM picks ratios; runner scales.
    # For sanity, validate the SHAPE is correct: 1 share leg + 1 short call.
    legs = [
        _spec("buy", "share", None, None, qty_ratio=100),
        _spec("sell", "call", 105.0, "2026-06-19", qty_ratio=1),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="covered_call",
        today=date(2026, 5, 17),
    )
    assert result.ok is True


def test_sanity_covered_call_rejects_wrong_leg_count():
    legs = [_spec("buy", "share", None, None, qty_ratio=100)]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="covered_call",
        today=date(2026, 5, 17),
    )
    assert result.ok is False


def test_sanity_covered_call_rejects_long_call():
    legs = [
        _spec("buy", "share", None, None, qty_ratio=100),
        _spec("buy", "call", 105.0, "2026-06-19", qty_ratio=1),  # should be sell
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="covered_call",
        today=date(2026, 5, 17),
    )
    assert result.ok is False


def test_sanity_covered_call_rejects_wrong_share_qty_ratio():
    """100 shares per 1 call contract."""
    legs = [
        _spec("buy", "share", None, None, qty_ratio=50),  # not 100
        _spec("sell", "call", 105.0, "2026-06-19", qty_ratio=1),
    ]
    result = vehicle.validate_structure_sanity(
        legs=legs, spot=100.0, structure_kind="covered_call",
        today=date(2026, 5, 17),
    )
    assert result.ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: FAIL on the 4 new tests.

- [ ] **Step 3: Add covered_call sanity**

Insert before the final "Multi-leg sanity" placeholder in `validate_structure_sanity`:

```python
    if structure_kind == "covered_call":
        if len(legs) != 2:
            return SanityResult(ok=False, reason="covered_call requires 2 legs")
        shares = [l for l in legs if l.kind == "share"]
        calls = [l for l in legs if l.kind == "call"]
        if len(shares) != 1 or len(calls) != 1:
            return SanityResult(ok=False, reason="covered_call requires 1 share leg + 1 call leg")
        share = shares[0]
        call = calls[0]
        if share.action != "buy":
            return SanityResult(ok=False, reason="covered_call share leg must be buy")
        if call.action != "sell":
            return SanityResult(ok=False, reason="covered_call call leg must be sell")
        if share.qty_ratio != call.qty_ratio * 100:
            return SanityResult(
                ok=False,
                reason=f"covered_call requires share qty_ratio = 100 × call qty_ratio (got {share.qty_ratio} vs {call.qty_ratio * 100})",
            )
        bad = _check_expiry_min_dte(call.expiry, today)
        if bad: return bad
        bad = _check_moneyness(call.strike, spot)
        if bad: return bad
        return SanityResult(ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: PASS (47 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/vehicle.py tests/unit/test_v2_vehicle.py
git commit -m "feat(v2/c3c): validate_structure_sanity for covered_call (Grok T1 F2 part 4)"
```

---

## Task 10: `validate` full pipeline + qty sizing

**Files:**
- Modify: `bullbot/v2/vehicle.py` (append `validate` + `_compute_qty_from_ratios`)
- Modify: `tests/unit/test_v2_vehicle.py` (append pipeline tests)

The full pipeline runs in this order per design §4.6:
1. Structure sanity (Task 6-9 helpers).
2. Strike+expiry exists in chain OR within BS-pricable range (ATM ±10%, 21-365 DTE).
3. `risk.compute_max_loss(legs) <= per_trade_cap` after qty sizing.
4. Ticker concentration after adding this position <= per_ticker_cap.
5. Total open positions + 1 <= max_open_positions.
6. If earnings_window_active: structure in {bull_call_spread, bear_put_spread, iron_condor, butterfly, csp, covered_call}.
7. If intent='accumulate': structure in {csp, long_shares, covered_call} (long_call deep-ITM check skipped here — too detailed for C.3c; revisit).

Qty sizing: scales the LLM's `qty_ratios` by computing how many "units" of the base ratio fit the per-trade cap via `risk.size_position` on the primary leg (= the leg with highest premium).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_vehicle.py`:

```python
def test_validate_full_pipeline_happy_path(conn):
    decision = vehicle.VehicleDecision(
        decision="open", intent="trade", structure="long_call",
        legs=[vehicle.LegSpec(
            action="buy", kind="call", strike=100.0, expiry="2026-06-19",
            qty_ratio=1,
        )],
        exit_plan={"profit_target_price": 110.0, "stop_price": 95.0,
                   "time_stop_dte": 21, "assignment_acceptable": False},
        rationale="bullish breakout",
    )
    result = vehicle.validate(
        decision=decision,
        spot=100.0, today=date(2026, 5, 17),
        nav=50_000.0, per_trade_pct=0.02, per_ticker_pct=0.15,
        max_open_positions=12, current_ticker_concentration_dollars=0.0,
        current_open_positions=5, earnings_window_active=False,
        entry_prices={0: 2.50},  # 1 contract × $2.50 = $250 risk, under $1000 cap
    )
    assert result.ok is True
    assert len(result.sized_legs) == 1
    # qty: cap=$1000 / per-contract risk $250 = 4 contracts
    assert result.sized_legs[0].qty == 4


def test_validate_rejects_invalid_structure_sanity(conn):
    decision = vehicle.VehicleDecision(
        decision="open", intent="trade", structure="bull_call_spread",
        legs=[
            vehicle.LegSpec(action="buy", kind="call", strike=105.0,
                            expiry="2026-06-19", qty_ratio=1),
            vehicle.LegSpec(action="sell", kind="call", strike=100.0,
                            expiry="2026-06-19", qty_ratio=1),
        ],  # inverted strikes
        exit_plan={}, rationale="",
    )
    result = vehicle.validate(
        decision=decision,
        spot=100.0, today=date(2026, 5, 17),
        nav=50_000.0, per_trade_pct=0.02, per_ticker_pct=0.15,
        max_open_positions=12, current_ticker_concentration_dollars=0.0,
        current_open_positions=5, earnings_window_active=False,
        entry_prices={0: 4.0, 1: 1.5},
    )
    assert result.ok is False
    assert "strike" in result.reason.lower()


def test_validate_rejects_when_earnings_window_blocks_long_premium(conn):
    decision = vehicle.VehicleDecision(
        decision="open", intent="trade", structure="long_call",
        legs=[vehicle.LegSpec(
            action="buy", kind="call", strike=100.0, expiry="2026-06-19",
            qty_ratio=1,
        )],
        exit_plan={}, rationale="",
    )
    result = vehicle.validate(
        decision=decision,
        spot=100.0, today=date(2026, 5, 17),
        nav=50_000.0, per_trade_pct=0.02, per_ticker_pct=0.15,
        max_open_positions=12, current_ticker_concentration_dollars=0.0,
        current_open_positions=5, earnings_window_active=True,
        entry_prices={0: 2.50},
    )
    assert result.ok is False
    assert "earnings" in result.reason.lower() or "iv" in result.reason.lower()


def test_validate_rejects_when_intent_accumulate_structure_mismatch(conn):
    """intent='accumulate' on a long_call (not deep-ITM-checked here) should
    still be allowed only for {csp, long_shares, covered_call}."""
    decision = vehicle.VehicleDecision(
        decision="open", intent="accumulate", structure="long_call",
        legs=[vehicle.LegSpec(
            action="buy", kind="call", strike=100.0, expiry="2026-06-19",
            qty_ratio=1,
        )],
        exit_plan={}, rationale="",
    )
    result = vehicle.validate(
        decision=decision,
        spot=100.0, today=date(2026, 5, 17),
        nav=50_000.0, per_trade_pct=0.02, per_ticker_pct=0.15,
        max_open_positions=12, current_ticker_concentration_dollars=0.0,
        current_open_positions=5, earnings_window_active=False,
        entry_prices={0: 2.50},
    )
    assert result.ok is False
    assert "intent" in result.reason.lower() or "accumulate" in result.reason.lower()


def test_validate_rejects_when_per_trade_cap_exceeded(conn):
    decision = vehicle.VehicleDecision(
        decision="open", intent="trade", structure="long_call",
        legs=[vehicle.LegSpec(
            action="buy", kind="call", strike=100.0, expiry="2026-06-19",
            qty_ratio=1,
        )],
        exit_plan={}, rationale="",
    )
    result = vehicle.validate(
        decision=decision,
        spot=100.0, today=date(2026, 5, 17),
        nav=50_000.0, per_trade_pct=0.02, per_ticker_pct=0.15,
        max_open_positions=12, current_ticker_concentration_dollars=0.0,
        current_open_positions=5, earnings_window_active=False,
        entry_prices={0: 15.0},  # $1500 per contract > $1000 cap, qty=0 -> reject
    )
    assert result.ok is False
    assert "loss" in result.reason.lower() or "cap" in result.reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: FAIL on the 5 new tests with `AttributeError: module 'bullbot.v2.vehicle' has no attribute 'validate'`.

- [ ] **Step 3: Implement `validate` + qty sizing**

Append to `bullbot/v2/vehicle.py`:

```python
from bullbot.v2 import risk
from bullbot.v2.positions import OptionLeg

EARNINGS_WHITELIST = {
    "bull_call_spread", "bear_put_spread", "iron_condor",
    "butterfly", "csp", "covered_call",
}
ACCUMULATE_WHITELIST = {"csp", "long_shares", "covered_call"}


def _spec_to_leg(spec: LegSpec, *, qty: int, entry_price: float) -> OptionLeg:
    """Materialize a LegSpec (LLM output) into an OptionLeg (DB-bound) with
    the actual qty (= spec.qty_ratio × unit_count) and a known entry_price."""
    return OptionLeg(
        action=spec.action, kind=spec.kind,
        strike=spec.strike, expiry=spec.expiry,
        qty=qty, entry_price=entry_price,
    )


def _compute_qty_from_ratios(
    *, legs: list[LegSpec], entry_prices: dict[int, float],
    spot: float, nav: float, per_trade_pct: float,
) -> int:
    """Compute the unit-count multiplier such that the total max-loss of the
    structure (legs scaled by qty_ratio × unit_count) fits the per-trade cap.

    Returns 0 if even a single-unit structure exceeds the cap.
    """
    # Build a single-unit version of the structure
    unit_legs = []
    for idx, spec in enumerate(legs):
        ep = entry_prices.get(idx, 0.0)
        unit_legs.append(_spec_to_leg(spec, qty=spec.qty_ratio, entry_price=ep))
    unit_loss = risk.compute_max_loss(unit_legs, spot=spot)
    import math
    if math.isinf(unit_loss) or unit_loss <= 0:
        return 0
    cap_dollars = nav * per_trade_pct
    return int(cap_dollars // unit_loss)


def validate(
    *,
    decision: VehicleDecision,
    spot: float,
    today: _date,
    nav: float,
    per_trade_pct: float,
    per_ticker_pct: float,
    max_open_positions: int,
    current_ticker_concentration_dollars: float,
    current_open_positions: int,
    earnings_window_active: bool,
    entry_prices: dict[int, float],
) -> ValidationResult:
    """Full validation pipeline. Per design §4.6:
      1. structure sanity
      2. strike+expiry within reasonable range (BS-pricable when no chain)
      3. risk caps (per-trade max-loss, ticker concentration, max positions)
      4. earnings/IV window
      5. intent <-> structure match

    Returns ValidationResult(ok=True, sized_legs=[...]) on success;
    ValidationResult(ok=False, reason="...") on first failure.
    """
    # 1. structure sanity
    sanity = validate_structure_sanity(
        legs=decision.legs, spot=spot,
        structure_kind=decision.structure, today=today,
    )
    if not sanity.ok:
        return ValidationResult(ok=False, reason=f"sanity: {sanity.reason}")

    # 2. earnings / IV window
    if earnings_window_active and decision.structure not in EARNINGS_WHITELIST:
        return ValidationResult(
            ok=False,
            reason=f"earnings_window_active + structure '{decision.structure}' not in whitelist",
        )

    # 3. intent <-> structure match
    if decision.intent == "accumulate" and decision.structure not in ACCUMULATE_WHITELIST:
        return ValidationResult(
            ok=False,
            reason=f"intent=accumulate + structure '{decision.structure}' mismatch",
        )

    # 4. qty sizing via risk caps
    unit_count = _compute_qty_from_ratios(
        legs=decision.legs, entry_prices=entry_prices,
        spot=spot, nav=nav, per_trade_pct=per_trade_pct,
    )
    if unit_count <= 0:
        return ValidationResult(
            ok=False,
            reason="per-trade max-loss cap exceeded (qty rounds to 0)",
        )
    sized_legs = [
        _spec_to_leg(spec, qty=spec.qty_ratio * unit_count, entry_price=entry_prices.get(idx, 0.0))
        for idx, spec in enumerate(decision.legs)
    ]

    # 5. ticker concentration cap
    proposed_loss = risk.compute_max_loss(sized_legs, spot=spot)
    new_concentration = current_ticker_concentration_dollars + proposed_loss
    if new_concentration > nav * per_ticker_pct:
        return ValidationResult(
            ok=False,
            reason=f"ticker concentration ${new_concentration:.0f} > cap ${nav * per_ticker_pct:.0f}",
        )

    # 6. total open positions cap
    if current_open_positions + 1 > max_open_positions:
        return ValidationResult(
            ok=False,
            reason=f"max open positions {max_open_positions} reached",
        )

    return ValidationResult(ok=True, reason=None, sized_legs=sized_legs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: PASS (52 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/vehicle.py tests/unit/test_v2_vehicle.py
git commit -m "feat(v2/c3c): validate() full pipeline + _compute_qty_from_ratios sizing"
```

---

## Task 11: `pick` — Haiku LLM call

**Files:**
- Modify: `bullbot/v2/vehicle.py` (append `pick` + `_parse_llm_response` + LLM client default)
- Modify: `tests/unit/test_v2_vehicle.py` (append pick tests)

`pick(conn, ticker, signal, spot, ..., client=None)` does:
1. Build the LLM context dict.
2. Serialize to JSON, embed in a prompt template asking for a single JSON object back.
3. Call `client.messages.create(model=HAIKU_MODEL, max_tokens=2000, messages=[{"role": "user", "content": prompt}])`.
4. Extract `response.content[0].text`, parse the JSON, materialize a `VehicleDecision`.
5. Return the `VehicleDecision` (or a "pass" decision if parse fails / LLM said pass).

LLM client default: `anthropic.Anthropic()` constructed lazily. Tests pass `fake_anthropic` (existing fixture).

JSON parsing tolerates leading/trailing prose by extracting the FIRST `{...}` block via regex.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_vehicle.py`:

```python
import json as _json


def test_pick_returns_open_decision_when_llm_returns_valid_json(conn, fake_anthropic):
    payload = {
        "decision": "open",
        "intent": "trade",
        "structure": "long_call",
        "legs": [{
            "action": "buy", "kind": "call", "strike": 100.0,
            "expiry": "2026-06-19", "qty_ratio": 1,
        }],
        "exit_plan": {
            "profit_target_price": 110.0, "stop_price": 95.0,
            "time_stop_dte": 21, "assignment_acceptable": False,
        },
        "rationale": "bullish breakout",
    }
    fake_anthropic.queue_response(_json.dumps(payload))
    bars = [_bar(close=100.0) for _ in range(60)]
    decision = vehicle.pick(
        conn,
        ticker="AAPL", spot=100.0, signal=_sample_signal(),
        bars=bars, levels=[], days_to_earnings=23,
        earnings_window_active=False, iv_rank=0.34,
        budget_per_trade_usd=1500.0, asof_ts=1_700_000_000,
        nav=50_000.0,
        per_ticker_concentration_pct=0.0,
        open_positions_count=7,
        client=fake_anthropic,
    )
    assert decision.decision == "open"
    assert decision.structure == "long_call"
    assert len(decision.legs) == 1


def test_pick_returns_pass_decision_when_llm_returns_pass(conn, fake_anthropic):
    fake_anthropic.queue_response('{"decision": "pass", "intent": "trade", '
                                  '"structure": "long_call", "legs": [], '
                                  '"exit_plan": {}, "rationale": "no edge"}')
    bars = [_bar(close=100.0) for _ in range(60)]
    decision = vehicle.pick(
        conn,
        ticker="AAPL", spot=100.0, signal=_sample_signal(),
        bars=bars, levels=[], days_to_earnings=23,
        earnings_window_active=False, iv_rank=0.34,
        budget_per_trade_usd=1500.0, asof_ts=1_700_000_000,
        nav=50_000.0,
        per_ticker_concentration_pct=0.0,
        open_positions_count=7,
        client=fake_anthropic,
    )
    assert decision.decision == "pass"


def test_pick_extracts_json_from_prose_wrapper(conn, fake_anthropic):
    """LLM sometimes wraps the JSON in 'Here is my pick: {...} hope that helps!'.
    The parser must extract the first {...} block."""
    payload = {
        "decision": "open", "intent": "trade", "structure": "csp",
        "legs": [{"action": "sell", "kind": "put", "strike": 95.0,
                  "expiry": "2026-06-19", "qty_ratio": 1}],
        "exit_plan": {}, "rationale": "lower basis",
    }
    fake_anthropic.queue_response(
        f"I think the right call here is to: {_json.dumps(payload)} Let me know if you want me to elaborate."
    )
    bars = [_bar(close=100.0) for _ in range(60)]
    decision = vehicle.pick(
        conn,
        ticker="AAPL", spot=100.0, signal=_sample_signal(),
        bars=bars, levels=[], days_to_earnings=23,
        earnings_window_active=False, iv_rank=0.34,
        budget_per_trade_usd=1500.0, asof_ts=1_700_000_000,
        nav=50_000.0,
        per_ticker_concentration_pct=0.0,
        open_positions_count=7,
        client=fake_anthropic,
    )
    assert decision.structure == "csp"


def test_pick_returns_pass_decision_when_llm_returns_invalid_json(conn, fake_anthropic):
    fake_anthropic.queue_response("I cannot make a decision today, sorry.")
    bars = [_bar(close=100.0) for _ in range(60)]
    decision = vehicle.pick(
        conn,
        ticker="AAPL", spot=100.0, signal=_sample_signal(),
        bars=bars, levels=[], days_to_earnings=23,
        earnings_window_active=False, iv_rank=0.34,
        budget_per_trade_usd=1500.0, asof_ts=1_700_000_000,
        nav=50_000.0,
        per_ticker_concentration_pct=0.0,
        open_positions_count=7,
        client=fake_anthropic,
    )
    assert decision.decision == "pass"
    assert "parse" in decision.rationale.lower() or "json" in decision.rationale.lower()


def test_pick_returns_pass_when_anthropic_raises(conn):
    class RaisingClient:
        messages = property(lambda self: self)
        def create(self, **kwargs):
            raise ConnectionError("simulated outage")
    bars = [_bar(close=100.0) for _ in range(60)]
    decision = vehicle.pick(
        conn,
        ticker="AAPL", spot=100.0, signal=_sample_signal(),
        bars=bars, levels=[], days_to_earnings=23,
        earnings_window_active=False, iv_rank=0.34,
        budget_per_trade_usd=1500.0, asof_ts=1_700_000_000,
        nav=50_000.0,
        per_ticker_concentration_pct=0.0,
        open_positions_count=7,
        client=RaisingClient(),
    )
    assert decision.decision == "pass"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: FAIL on the 5 new tests with `AttributeError: module 'bullbot.v2.vehicle' has no attribute 'pick'`.

- [ ] **Step 3: Implement `pick` + JSON parser**

Append to `bullbot/v2/vehicle.py`:

```python
import json
import logging
import re
from typing import Callable

_log = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
MAX_OUTPUT_TOKENS = 2000

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _default_anthropic_client():
    """Lazy anthropic import — keeps tests independent of SDK availability."""
    import anthropic
    return anthropic.Anthropic()


def _parse_llm_response(text: str) -> VehicleDecision | None:
    """Extract first {...} block, parse JSON, materialize VehicleDecision.
    Returns None on parse error or schema-rejection."""
    if not text:
        return None
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    try:
        legs = [
            LegSpec(
                action=leg["action"], kind=leg["kind"],
                strike=leg.get("strike"), expiry=leg.get("expiry"),
                qty_ratio=int(leg.get("qty_ratio", 1)),
            )
            for leg in payload.get("legs", [])
        ]
        return VehicleDecision(
            decision=payload["decision"], intent=payload["intent"],
            structure=payload["structure"], legs=legs,
            exit_plan=payload.get("exit_plan", {}),
            rationale=payload.get("rationale", ""),
        )
    except (KeyError, ValueError) as exc:
        _log.warning("LLM payload schema rejection: %s", exc)
        return None


_PROMPT_TEMPLATE = """You are a swing-trading vehicle-selection agent for a paper-trading research bot.

Given the following context as a single JSON object, return EXACTLY ONE JSON object describing the trade you would open today (or "pass" if no good trade exists). Use this schema:

{{
  "decision": "open" | "pass",
  "intent": "trade" | "accumulate",
  "structure": one of {structure_kinds},
  "legs": [
    {{"action": "buy"|"sell", "kind": "call"|"put"|"share",
      "strike": float | null, "expiry": "YYYY-MM-DD" | null,
      "qty_ratio": int}}
  ],
  "exit_plan": {{
    "profit_target_price": float | null,
    "stop_price": float | null,
    "time_stop_dte": int | null,
    "assignment_acceptable": bool
  }},
  "rationale": "<= 200 chars why this structure now"
}}

CONTEXT:
{context_json}

Return only the JSON object — no prose, no markdown fences."""


def pick(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    spot: float,
    signal: DirectionalSignal,
    bars: list,
    levels: list,
    days_to_earnings: int,
    earnings_window_active: bool,
    iv_rank: float,
    budget_per_trade_usd: float,
    asof_ts: int,
    nav: float,
    per_ticker_concentration_pct: float,
    open_positions_count: int,
    current_position: Position | None = None,
    client: object = None,
) -> VehicleDecision:
    """Main entry: assemble context, call Haiku, parse response, return decision.

    Returns a `pass` VehicleDecision (with a diagnostic rationale) on any
    LLM failure (network, invalid JSON, schema rejection). Validation against
    risk caps + structure sanity is the caller's job via validate()."""
    if client is None:
        client = _default_anthropic_client()

    ctx = build_llm_context(
        conn,
        ticker=ticker, spot=spot, signal=signal, bars=bars, levels=levels,
        days_to_earnings=days_to_earnings,
        earnings_window_active=earnings_window_active, iv_rank=iv_rank,
        budget_per_trade_usd=budget_per_trade_usd, asof_ts=asof_ts,
        nav=nav,
        per_ticker_concentration_pct=per_ticker_concentration_pct,
        open_positions_count=open_positions_count,
        current_position=current_position,
    )
    prompt = _PROMPT_TEMPLATE.format(
        structure_kinds=list(STRUCTURE_KINDS),
        context_json=json.dumps(ctx, indent=2),
    )
    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text if response.content else ""
    except Exception as exc:  # noqa: BLE001
        _log.warning("vehicle.pick: anthropic call failed for %s: %s", ticker, exc)
        return VehicleDecision(
            decision="pass", intent="trade", structure="long_call",
            legs=[], exit_plan={}, rationale=f"anthropic error: {exc}",
        )

    decision = _parse_llm_response(text)
    if decision is None:
        return VehicleDecision(
            decision="pass", intent="trade", structure="long_call",
            legs=[], exit_plan={}, rationale="LLM response failed to parse",
        )
    return decision
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_vehicle.py -v`
Expected: PASS (57 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/vehicle.py tests/unit/test_v2_vehicle.py
git commit -m "feat(v2/c3c): pick() — Haiku LLM call + JSON parser + failure-safe pass decisions"
```

---

## Task 12: Full regression check

**Files:** none (test-only verification step)

- [ ] **Step 1: Run the full unit suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit -q`
Expected: 693 + 57 = 750 unit tests pass.

- [ ] **Step 2: Run the integration suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration -q`
Expected: All 80 integration tests still pass.

- [ ] **Step 3: Static-import sanity check**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -c "from bullbot.v2 import vehicle; print(vehicle.pick, vehicle.validate, vehicle.build_llm_context, vehicle.validate_structure_sanity, vehicle.STRUCTURE_KINDS, vehicle.HAIKU_MODEL)"`
Expected: prints all public exports without ImportError.

- [ ] **Step 4: Optional marker commit**

```bash
git commit --allow-empty -m "chore(v2/c3c): Phase C.3c complete — vehicle.py landed"
```

---

## Acceptance criteria

C.3c is complete when ALL of the following hold:

1. `bullbot/v2/vehicle.py` exists and exports: `pick`, `validate`, `validate_structure_sanity`, `build_llm_context`, `VehicleDecision`, `LegSpec`, `SanityResult`, `ValidationResult`, plus constants `STRUCTURE_KINDS`, `HAIKU_MODEL`, `EARNINGS_WHITELIST`, `ACCUMULATE_WHITELIST`.
2. `tests/unit/test_v2_vehicle.py` contains the 57 tests listed in Tasks 1–11 and they all pass.
3. Full unit + integration suite is green (no regressions vs the C.3b baseline of 693 unit + 80 integration).
4. `vehicle.py` is under 500 LOC.
5. No new third-party dependencies introduced (anthropic SDK already in project).
6. No DB schema changes.
7. All 6 structure-sanity branches (single-leg + verticals + IC + butterfly + covered call) implemented (Grok T1 F2 satisfied).
8. Earnings whitelist enforcement matches the design + Grok T2 F7 expanded trigger.

## What this unblocks

- **C.4 (backtest harness):** the same `pick()` + `validate()` runs inside the replay loop. Synthesized chains feed `entry_prices`; the Haiku call still goes out (with backtest budget cap).
- **C.5 (`runner_c.py`):** for each flat ticker, runner calls `pick()` then `validate()`, then `positions.open_position(...)` on the validated `sized_legs`. For each held ticker, runner calls `exits.evaluate()` (shipped in C.3b).

## Notes for the implementer

- **`fake_anthropic` is auto-injected** by pytest from `tests/conftest.py:347`. Just declare it as a fixture parameter.
- **Long_call deep-ITM-delta check for intent=accumulate** is deliberately NOT enforced in this plan — would need delta computation per leg, deferred to a follow-up. For now, intent='accumulate' on a long_call is rejected by the simpler whitelist check.
- **`_PROMPT_TEMPLATE` is intentionally minimal** — the LLM needs the schema + the context, no role-playing instructions. If forward-mode reveals the agent makes systematic dumb picks, expand the prompt in a follow-up.
- **Failure handling philosophy:** any LLM problem (network, invalid JSON, schema rejection) produces a `pass` decision rather than raising. The runner treats a `pass` as "no trade today" and moves on. Logging captures the diagnostic.
- **Validation order matters:** sanity FIRST so we don't waste chain lookups on broken legs; then cheap checks (earnings whitelist, intent match); then sizing + concentration cap last.
- **Worktree `.venv` path** is `/Users/danield.runion/Projects/bull-bot/.venv/bin/python`. Same note as prior phases.
