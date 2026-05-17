# Bull-Bot v2 Phase C.2 — Review Bundle for Grok

Three sections inline below: **Project Context**, **Implementation Plan**, **Review Prompt**.
Read in this order, then respond per the format in Section 3.

---

# SECTION 1 — Project Context


This document gives an outside reviewer (Grok, a human consultant, or another model) the project background needed to review the C.2 levels-module implementation plan critically.

## 1. What is Bull-Bot

Bull-Bot is a personal automated trading research project. It is built and operated by one person (Dan), runs on a single Mac mini ("pasture") via launchd, paper-trades a fixed universe of US equity tickers, and maintains a SQLite database (`cache/bullbot.db`) as its single source of truth.

The bot is a learning project, not a commercial product. There is no broker integration. All trades are simulated against Yahoo Finance bar and chain data. The goal is to develop trading judgment that could later be deployed with real capital, and to learn AI engineering by building agentic systems against a domain Dan cares about (markets).

Dan is a Product Manager by background, not a backend engineer. The bot is asked to communicate state in plain language ("we made $X today on AAPL"), and to make autonomous strategy/parameter decisions itself rather than asking the operator to pick deltas, DTEs, vehicles, or sizing — these are explicitly the bot's job to discover.

## 2. Where Phase C stands today

The Phase C design + Grok review response are committed at:
- `docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md`
- `docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md`
- `docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-context.md`

Phase C is broken into 7 sub-steps:
- **C.0 — Schema + positions.py + risk.py.** **MERGED** in PR [bull-bot#1](https://github.com/runiondd/bull-bot/pull/1) (2026-05-16).
- **C.1 — Chains module (Yahoo + BS).** **MERGED** in PR [bull-bot#3](https://github.com/runiondd/bull-bot/pull/3) (2026-05-17, formerly stacked on #1 as #2).
- **C.2 — Support/resistance module (`levels.py`).** **THIS PLAN.**
- C.3 — Earnings + vehicle agent (LLM) + exits.
- C.4 — Backtest harness (including event-day IV bump per Grok Tier 1 Finding 3).
- C.5 — Forward runner + dashboard tabs.
- C.6 — Ship to pasture + verify live.

## 3. What the C.2 plan delivers

The plan ships `bullbot/v2/levels.py` — a pure-function support/resistance calculator. Single public entry point:

`compute_sr(bars: list, lookback: int = 60) -> list[Level]`

Where `Level(price, kind, strength)` and `kind ∈ {swing_high, swing_low, sma_20, sma_50, sma_200, round_number}`, `strength ∈ [0.0, 1.0]`.

Pipeline:
1. **Swing extrema** with 3-bar confirmation on each side. Strength scales with touch count (how many bars sit within 0.5% of the swing).
2. **SMA values** at 20 / 50 / 200 windows. Strength scales with window length (200 > 50 > 20).
3. **Round-number snaps** within 2% of spot. Step size scales with spot magnitude ($1 / $5 / $10 / $50 across price tiers). Fixed strength = 0.3.
4. **Dedup** within 0.5% — clusters collapse to the level with highest strength (ties broken by kind priority: swing > sma_200 > sma_50 > sma_20 > round_number).
5. **Sort** by absolute distance to the most recent close.

Key design choices baked into the plan:
- **Stdlib only.** No NumPy, no pandas, no third-party libraries.
- **Pure function.** No DB reads, no I/O, no LLM. Bars are passed in.
- **Duck-typed bars.** Same SimpleNamespace shape (`.high`, `.low`, `.close`) the rest of the v2 codebase uses.
- **No persistence.** S/R levels are computed on demand. If C.5 dashboard wants to display them, that's a C.5 decision.
- **Strength is heuristic, not statistical.** The 0–1 scale is for the LLM's interpretability. Not over-engineered — C.3's prompt design will reveal what actually matters and can drive refinement.

## 4. What the plan does NOT touch

- `bullbot/db/migrations.py` — no schema changes.
- `bullbot/v2/positions.py`, `risk.py`, `chains.py` — finalized in C.0 / C.1.
- LLM / vehicle agent — C.3 scope.
- Backtest synth_chain (with event-day bump) — C.4 scope.
- Forward MtM loop, dashboard tabs — C.5 scope.

## 5. Plan structure

The plan follows the same TDD pattern as the C.0 and C.1 plans that have already shipped:

7 tasks. Each task = (failing test → run to see failure → minimal implementation → run to see pass → commit). Each task adds 5–7 new unit tests. Tasks are sized so a focused subagent session can complete one end-to-end.

The plan was written using the Superpowers `writing-plans` skill (same skill that produced C.0 and C.1 plans), which mandates:
- Exact file paths
- Complete code in every step (no placeholders / TBDs)
- Exact pytest commands with expected output
- TDD discipline (test-first, never skip the failing-test verification)
- One commit per task

## 6. Conventions specific to this codebase that may be relevant

- Tests live under `tests/unit/` and `tests/integration/`. The conftest auto-adds repo root to `sys.path`.
- Use `/Users/danield.runion/Projects/bull-bot/.venv/bin/python` as the runner; `.venv` lives at the main repo, not in the worktree.
- The existing v2 codebase pattern is: small single-responsibility modules (~75–200 LOC each), no inheritance, dataclasses for state, plain functions for behavior.
- For S/R specifically: trader convention uses N-bar confirmation (typically N=3) for swing detection and 20/50/200 as the canonical SMA windows. These constants are intentionally locked, not configurable in C.2 — if backtest reveals different windows matter, that's a C.4 tuning loop.

## 7. Dan's stated preferences (relevant to plan review)

- The bot picks vehicles/sizing/strikes/strategy autonomously — the plan does not expose tunable parameters to the operator.
- The S/R strengths and thresholds (touch count, dedup 0.5%, round-number 2%) are opinionated heuristics — Grok's review should challenge them with reasoning, not propose making them all configurable.
- MSTR/IBIT thesis means S/R may need to handle very wide ranges (e.g., $400 spot, $50 round-number steps); the plan's step-size table covers this.

---

# SECTION 2 — Implementation Plan


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `bullbot/v2/levels.py` — a deterministic, pure-function support/resistance calculator that turns a sequence of daily bars into a ranked list of price levels (swing highs/lows + moving-average values + round-number snaps). After this plan lands, the vehicle agent in C.3 can feed S/R proximity into the LLM context, and `exits.py` can compare current spot to "nearest_resistance / nearest_support" without re-implementing the math.

**Architecture:** Single-responsibility module, no I/O beyond the bar list passed in, no DB reads, no LLM. Internally composed of four private helpers — `_find_swing_extrema`, `_sma_levels`, `_round_number_levels`, `_dedup_levels` — wired together in one public `compute_sr` function. Mirrors the `bullbot.v2.chains.py` pattern (small dataclass at top, private helpers in the middle, one public entry point at the bottom). Reuses Python stdlib `statistics.mean` for the SMA computation — no NumPy/pandas dependency. Output is sorted by absolute distance to the most recent close so the vehicle agent can take the first N levels above and below.

**Tech Stack:** Python 3.11+, stdlib only (`dataclasses`, `statistics`), `pytest`. No new third-party dependencies. Operates on the same duck-typed bar shape the existing v2 codebase uses (`SimpleNamespace` with `.high`, `.low`, `.close` attributes — see `bullbot.v2.runner._load_bars` and `bullbot.v2.chains._load_bars` for the producer pattern).

**Spec reference:** [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md`](../specs/2026-05-16-phase-c-vehicle-agent-design.md) section 4.4 (primary spec), plus 4.5 (LLM context that consumes the `levels` field) and 4.7 (exit-rule evaluator that checks for `profit_target_price` / `stop_price` being tagged by underlying).

---

## Pre-flight assumptions verified before writing tasks

- **`bullbot/v2/` exists** with `signals.py`, `underlying.py`, `trades.py`, `trader.py`, `positions.py`, `risk.py`, `chains.py`, `__init__.py` after C.0 + C.1 landed.
- **Bars are SimpleNamespace-shaped** in the v2 codepath (not Pydantic `Bar` — the Pydantic one in `bullbot/data/schemas.py` is for the older v1 fetcher path).
- **No new DB tables needed.** S/R levels are computed on demand from bars; nothing is persisted. (If C.5 dashboard wants to display them, that's a snapshot decision for C.5, not C.2.)
- **`bullbot/v2/chains.py` already duplicates `_load_bars` from `runner.py`.** When a third module needs the same helper, promote it then. C.2 does NOT call `_load_bars` (bars are passed in by the caller), so the duplication stays at two for now.

---

## File Structure

| Path | Responsibility | Status |
|---|---|---|
| `bullbot/v2/levels.py` | `Level` dataclass, `compute_sr` public entry, four private helpers. | **Create** |
| `tests/unit/test_v2_levels.py` | Unit tests for each private helper + `compute_sr` orchestration. | **Create** |
| `bullbot/v2/positions.py` | Unchanged. | — |
| `bullbot/v2/risk.py` | Unchanged. | — |
| `bullbot/v2/chains.py` | Unchanged. | — |
| `bullbot/db/migrations.py` | Unchanged. (No persistence in C.2.) | — |

Module size target for `levels.py`: < 200 LOC. If the swing-extrema logic alone pushes past 100 lines (it shouldn't — straight loop with N-bar confirmation), revisit the helper decomposition before adding more.

---

## Task 1: `Level` dataclass + module skeleton

**Files:**
- Create: `bullbot/v2/levels.py`
- Create: `tests/unit/test_v2_levels.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_v2_levels.py`:

```python
"""Unit tests for bullbot.v2.levels — support/resistance computation."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bullbot.v2 import levels


def _bar(close: float, high: float | None = None, low: float | None = None):
    """Build a SimpleNamespace bar with the duck-typed shape v2 uses."""
    return SimpleNamespace(
        ts=0, open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=1_000_000.0,
    )


def test_level_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be one of"):
        levels.Level(price=100.0, kind="fibonacci_618", strength=0.5)


def test_level_rejects_strength_out_of_range():
    with pytest.raises(ValueError, match="strength must be in"):
        levels.Level(price=100.0, kind="swing_high", strength=1.5)
    with pytest.raises(ValueError, match="strength must be in"):
        levels.Level(price=100.0, kind="swing_high", strength=-0.1)


def test_level_distance_to_returns_absolute_difference():
    lvl = levels.Level(price=105.0, kind="swing_high", strength=0.5)
    assert lvl.distance_to(spot=100.0) == 5.0
    assert lvl.distance_to(spot=110.0) == 5.0


def test_level_distance_pct_to_uses_spot_as_denominator():
    lvl = levels.Level(price=105.0, kind="swing_high", strength=0.5)
    assert lvl.distance_pct_to(spot=100.0) == pytest.approx(0.05)


def test_level_is_above_spot_for_resistance():
    lvl = levels.Level(price=110.0, kind="swing_high", strength=0.5)
    assert lvl.is_above(spot=100.0) is True
    assert lvl.is_above(spot=120.0) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_levels.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bullbot.v2.levels'`.

- [ ] **Step 3: Implement the skeleton**

Create `bullbot/v2/levels.py`:

```python
"""Support/resistance level computation for v2 Phase C.

Pure function over a list of bars — no DB, no I/O, no LLM. Returns a list
of Level objects ranked by absolute distance to the most recent close.

The vehicle agent (C.3) feeds the top-N nearest_resistance / nearest_support
levels into the LLM context. The exits.py module (C.3) compares the current
spot to stored profit_target_price / stop_price values that are themselves
derived from these levels at entry time.
"""
from __future__ import annotations

from dataclasses import dataclass

VALID_KINDS = (
    "swing_high", "swing_low",
    "sma_20", "sma_50", "sma_200",
    "round_number",
)


@dataclass(frozen=True)
class Level:
    """A single price level with provenance and a [0, 1] strength score."""

    price: float
    kind: str
    strength: float

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}; got {self.kind!r}")
        if not (0.0 <= self.strength <= 1.0):
            raise ValueError(f"strength must be in [0.0, 1.0]; got {self.strength}")

    def distance_to(self, *, spot: float) -> float:
        """Absolute dollar distance from this level to `spot`."""
        return abs(self.price - spot)

    def distance_pct_to(self, *, spot: float) -> float:
        """Absolute percent distance from this level to `spot` (using spot as denom)."""
        return abs(self.price - spot) / spot

    def is_above(self, *, spot: float) -> bool:
        """True if this level sits above `spot` (resistance side)."""
        return self.price > spot
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_levels.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/levels.py tests/unit/test_v2_levels.py
git commit -m "feat(v2/c2): Level dataclass + distance/is_above helpers"
```

---

## Task 2: `_find_swing_extrema` — local highs/lows with N-bar confirmation

**Files:**
- Modify: `bullbot/v2/levels.py` (append `_find_swing_extrema` + import helpers)
- Modify: `tests/unit/test_v2_levels.py` (append swing tests)

A swing high at index `i` requires `bars[i].high` to be strictly greater than `bars[i-1].high, bars[i-2].high, bars[i-3].high` AND `bars[i+1].high, bars[i+2].high, bars[i+3].high` (3-bar confirmation on each side, N=3). The most-recent 3 bars cannot be swing highs because they lack right-side confirmation. Swing lows mirror this on `.low`.

Strength = `min(1.0, touch_count / 5.0)` where `touch_count` is the number of OTHER bars within 0.5% of the swing level — a level touched many times before is stronger than a one-off spike.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_levels.py`:

```python
def test_find_swing_extrema_detects_simple_peak():
    """A clear peak in the middle with rising-then-falling highs."""
    bars = [
        _bar(close=h, high=h, low=h-0.5)
        for h in [100, 101, 102, 103, 105, 103, 102, 101, 100, 99, 98]
        # idx 0   1   2   3   4*   5   6   7   8   9  10
        # idx 4 is the peak (105 > all neighbors within 3 bars on each side)
    ]
    extrema = levels._find_swing_extrema(bars, n_confirm=3)
    swing_highs = [lvl for lvl in extrema if lvl.kind == "swing_high"]
    assert len(swing_highs) == 1
    assert swing_highs[0].price == 105.0


def test_find_swing_extrema_detects_simple_trough():
    bars = [
        _bar(close=l, high=l+0.5, low=l)
        for l in [100, 99, 98, 97, 95, 97, 98, 99, 100, 101, 102]
        # idx 4 is the trough (95 < all neighbors within 3 bars on each side)
    ]
    extrema = levels._find_swing_extrema(bars, n_confirm=3)
    swing_lows = [lvl for lvl in extrema if lvl.kind == "swing_low"]
    assert len(swing_lows) == 1
    assert swing_lows[0].price == 95.0


def test_find_swing_extrema_skips_unconfirmed_recent_bars():
    """A bar that LOOKS like a high but has fewer than n_confirm bars to its
    right is not yet confirmed and should not be returned."""
    bars = [
        _bar(close=h, high=h, low=h-0.5)
        for h in [100, 101, 102, 103, 105, 103, 102]
        # idx 4 is the highest, but only 2 bars to its right (n_confirm=3) -> unconfirmed
    ]
    extrema = levels._find_swing_extrema(bars, n_confirm=3)
    swing_highs = [lvl for lvl in extrema if lvl.kind == "swing_high"]
    assert swing_highs == []


def test_find_swing_extrema_handles_short_series_gracefully():
    """Fewer than 2*n_confirm + 1 bars -> nothing can be confirmed."""
    bars = [_bar(close=100 + i) for i in range(5)]
    extrema = levels._find_swing_extrema(bars, n_confirm=3)
    assert extrema == []


def test_find_swing_extrema_strength_scales_with_touch_count():
    """A level touched (within 0.5%) by many subsequent bars is stronger."""
    # Peak at 100, then prices return to ~100 many times
    closes = [95, 96, 98, 99, 100, 99, 99.6, 100.0, 99.5, 100.2, 99.8, 100.1, 99.7]
    bars = [_bar(close=c, high=c, low=c-0.2) for c in closes]
    extrema = levels._find_swing_extrema(bars, n_confirm=3)
    swing_highs = [lvl for lvl in extrema if lvl.kind == "swing_high"]
    assert len(swing_highs) >= 1
    # Strength must be > 0 (many touches near 100)
    assert swing_highs[0].strength > 0.2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_levels.py -v`
Expected: FAIL on the 5 new tests with `AttributeError: module 'bullbot.v2.levels' has no attribute '_find_swing_extrema'`.

- [ ] **Step 3: Implement `_find_swing_extrema`**

Append to `bullbot/v2/levels.py`:

```python
TOUCH_PCT = 0.005  # within 0.5% counts as a "touch" for strength scoring


def _find_swing_extrema(bars: list, n_confirm: int = 3) -> list[Level]:
    """Find local high / low peaks with `n_confirm` bars on each side strictly
    less / greater than the candidate. Returns a list of Level objects with
    kind='swing_high' or 'swing_low' and strength scaled by touch count.

    The last `n_confirm` bars cannot be classified (no right-side confirmation).
    """
    out: list[Level] = []
    if len(bars) < 2 * n_confirm + 1:
        return out

    for i in range(n_confirm, len(bars) - n_confirm):
        cand_high = bars[i].high
        cand_low = bars[i].low
        is_swing_high = all(
            bars[j].high < cand_high
            for j in range(i - n_confirm, i + n_confirm + 1) if j != i
        )
        is_swing_low = all(
            bars[j].low > cand_low
            for j in range(i - n_confirm, i + n_confirm + 1) if j != i
        )
        if is_swing_high:
            touches = sum(
                1 for b in bars
                if abs(b.high - cand_high) / cand_high <= TOUCH_PCT
            ) - 1
            strength = min(1.0, max(touches, 0) / 5.0)
            out.append(Level(price=cand_high, kind="swing_high", strength=strength))
        if is_swing_low:
            touches = sum(
                1 for b in bars
                if abs(b.low - cand_low) / cand_low <= TOUCH_PCT
            ) - 1
            strength = min(1.0, max(touches, 0) / 5.0)
            out.append(Level(price=cand_low, kind="swing_low", strength=strength))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_levels.py -v`
Expected: PASS (10 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/levels.py tests/unit/test_v2_levels.py
git commit -m "feat(v2/c2): _find_swing_extrema with N-bar confirmation + touch-count strength"
```

---

## Task 3: `_sma_levels` — 20/50/200 simple moving averages as dynamic levels

**Files:**
- Modify: `bullbot/v2/levels.py` (append `_sma_levels`)
- Modify: `tests/unit/test_v2_levels.py` (append SMA tests)

Compute SMA at three windows (20, 50, 200) from the most recent bars' closes. Each SMA value becomes a `Level` with kind `sma_20` / `sma_50` / `sma_200` and a fixed strength scaled by window length (200-day is "stronger" than 20-day for dynamic S/R): strength = `min(1.0, window / 200.0)`. Returns an empty list for any window that doesn't have enough bars yet.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_levels.py`:

```python
def test_sma_levels_emits_one_level_per_window_with_enough_bars():
    bars = [_bar(close=100 + i * 0.1) for i in range(250)]  # 250 bars, all SMAs computable
    sma_lvls = levels._sma_levels(bars)
    kinds = {lvl.kind for lvl in sma_lvls}
    assert kinds == {"sma_20", "sma_50", "sma_200"}


def test_sma_levels_skips_windows_with_insufficient_bars():
    bars = [_bar(close=100.0) for _ in range(30)]  # only 30 bars
    sma_lvls = levels._sma_levels(bars)
    kinds = {lvl.kind for lvl in sma_lvls}
    assert kinds == {"sma_20"}  # 50 and 200 don't have enough bars


def test_sma_levels_computes_arithmetic_mean_of_last_n_closes():
    """100 bars at close=100.0 -> SMA_20 = 100.0, SMA_50 = 100.0."""
    bars = [_bar(close=100.0) for _ in range(100)]
    sma_lvls = levels._sma_levels(bars)
    sma_20 = next(lvl for lvl in sma_lvls if lvl.kind == "sma_20")
    sma_50 = next(lvl for lvl in sma_lvls if lvl.kind == "sma_50")
    assert sma_20.price == pytest.approx(100.0)
    assert sma_50.price == pytest.approx(100.0)


def test_sma_levels_window_200_has_higher_strength_than_window_20():
    bars = [_bar(close=100.0) for _ in range(250)]
    sma_lvls = levels._sma_levels(bars)
    sma_20 = next(lvl for lvl in sma_lvls if lvl.kind == "sma_20")
    sma_200 = next(lvl for lvl in sma_lvls if lvl.kind == "sma_200")
    assert sma_200.strength > sma_20.strength


def test_sma_levels_returns_empty_for_no_bars():
    assert levels._sma_levels([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_levels.py -v`
Expected: FAIL on the 5 new tests with `AttributeError`.

- [ ] **Step 3: Implement `_sma_levels`**

Append to `bullbot/v2/levels.py`:

```python
from statistics import mean

SMA_WINDOWS = (20, 50, 200)


def _sma_levels(bars: list) -> list[Level]:
    """For each window in (20, 50, 200), if enough bars exist, emit a Level
    at the current SMA value with kind sma_<window> and strength scaled by
    window length.

    Longer windows = stronger dynamic S/R (institutional algos watch 200-day
    closer than 20-day).
    """
    out: list[Level] = []
    for w in SMA_WINDOWS:
        if len(bars) < w:
            continue
        sma_value = mean(b.close for b in bars[-w:])
        strength = min(1.0, w / 200.0)
        out.append(Level(price=sma_value, kind=f"sma_{w}", strength=strength))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_levels.py -v`
Expected: PASS (15 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/levels.py tests/unit/test_v2_levels.py
git commit -m "feat(v2/c2): _sma_levels emits 20/50/200 SMA as dynamic Levels"
```

---

## Task 4: `_round_number_levels` — snap psychological round numbers within 2% of spot

**Files:**
- Modify: `bullbot/v2/levels.py` (append `_round_number_levels`)
- Modify: `tests/unit/test_v2_levels.py` (append round-number tests)

For spot `$103`, round numbers within 2% are `$101, $102, $103, $104, $105`. For spot `$237.50`, the unit step is `$5` (rounded numbers scale with price magnitude), so the candidates are `$235, $240`. For spot `$1850`, the unit step is `$50`: `$1800, $1850, $1900`.

Step size:
- spot < $50 → step = $1
- $50 ≤ spot < $200 → step = $5
- $200 ≤ spot < $1000 → step = $10
- spot ≥ $1000 → step = $50

Strength is fixed at 0.3 — round numbers matter but less than confirmed swing points.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_levels.py`:

```python
def test_round_number_levels_for_spot_under_50_uses_dollar_step():
    """spot=23, 2% band = ±$0.46 -> only $23 is within 2%."""
    rn = levels._round_number_levels(spot=23.0)
    prices = sorted(lvl.price for lvl in rn)
    assert prices == [23.0]


def test_round_number_levels_for_mid_priced_stock_uses_five_dollar_step():
    """spot=103, step=$5, 2% band = ±$2.06 -> only $105 is within 2% (above)
    and $100 is just outside ($3 away, > 2%)."""
    rn = levels._round_number_levels(spot=103.0)
    prices = sorted(lvl.price for lvl in rn)
    assert prices == [105.0]


def test_round_number_levels_for_mid_priced_stock_captures_both_sides_when_close():
    """spot=102.5, step=$5, 2% band = ±$2.05 -> $100 ($2.5 away, out) and
    $105 ($2.5 away, out) -> neither captured. But spot=101.0 -> $100 ($1 away, in)."""
    rn = levels._round_number_levels(spot=101.0)
    prices = sorted(lvl.price for lvl in rn)
    assert prices == [100.0]


def test_round_number_levels_for_expensive_stock_uses_ten_dollar_step():
    """spot=237.50, step=$10, 2% band = ±$4.75 -> $240 is $2.50 away (in),
    $230 is $7.50 away (out)."""
    rn = levels._round_number_levels(spot=237.50)
    prices = sorted(lvl.price for lvl in rn)
    assert prices == [240.0]


def test_round_number_levels_for_high_priced_stock_uses_fifty_dollar_step():
    """spot=1010, step=$50, 2% band = ±$20.20 -> $1000 ($10 away, in),
    $1050 ($40 away, out)."""
    rn = levels._round_number_levels(spot=1010.0)
    prices = sorted(lvl.price for lvl in rn)
    assert prices == [1000.0]


def test_round_number_levels_all_have_kind_round_number_and_fixed_strength():
    rn = levels._round_number_levels(spot=100.5)
    assert all(lvl.kind == "round_number" for lvl in rn)
    assert all(lvl.strength == 0.3 for lvl in rn)


def test_round_number_levels_for_zero_or_negative_spot_returns_empty():
    assert levels._round_number_levels(spot=0.0) == []
    assert levels._round_number_levels(spot=-5.0) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_levels.py -v`
Expected: FAIL on the 7 new tests with `AttributeError`.

- [ ] **Step 3: Implement `_round_number_levels`**

Append to `bullbot/v2/levels.py`:

```python
ROUND_NUMBER_BAND_PCT = 0.02
ROUND_NUMBER_STRENGTH = 0.3


def _round_step(spot: float) -> float:
    """Step size for round-number candidates, scaled by spot magnitude."""
    if spot < 50.0:
        return 1.0
    if spot < 200.0:
        return 5.0
    if spot < 1000.0:
        return 10.0
    return 50.0


def _round_number_levels(*, spot: float) -> list[Level]:
    """Emit Levels at round-number prices within ROUND_NUMBER_BAND_PCT (2%) of spot.
    Step size scales with spot magnitude (see _round_step)."""
    if spot <= 0:
        return []
    step = _round_step(spot)
    band = spot * ROUND_NUMBER_BAND_PCT
    # Find the nearest multiple of `step` at or below spot
    floor_mult = (spot // step) * step
    out: list[Level] = []
    # Walk ±2 steps and keep anything inside the band
    for k in (-2, -1, 0, 1, 2):
        candidate = floor_mult + k * step
        if candidate <= 0:
            continue
        if abs(candidate - spot) <= band:
            out.append(Level(price=candidate, kind="round_number",
                             strength=ROUND_NUMBER_STRENGTH))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_levels.py -v`
Expected: PASS (22 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/levels.py tests/unit/test_v2_levels.py
git commit -m "feat(v2/c2): _round_number_levels with magnitude-scaled step size"
```

---

## Task 5: `_dedup_levels` — collapse levels within 0.5% of each other

**Files:**
- Modify: `bullbot/v2/levels.py` (append `_dedup_levels`)
- Modify: `tests/unit/test_v2_levels.py` (append dedup tests)

Two levels within 0.5% of each other (e.g., a swing high at $100.0 and an SMA at $100.30) are effectively the same level. Keep the one with the higher `strength`; if tied, keep the one whose `kind` comes first in this priority order: `swing_high`, `swing_low`, `sma_200`, `sma_50`, `sma_20`, `round_number`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_levels.py`:

```python
DEDUP_PCT = 0.005  # 0.5% — same threshold as the impl


def test_dedup_levels_collapses_two_close_levels_keeping_stronger():
    a = levels.Level(price=100.0, kind="swing_high", strength=0.8)
    b = levels.Level(price=100.30, kind="sma_50", strength=0.25)
    out = levels._dedup_levels([a, b])
    assert len(out) == 1
    assert out[0] is a  # stronger one kept


def test_dedup_levels_preserves_levels_more_than_05pct_apart():
    a = levels.Level(price=100.0, kind="swing_high", strength=0.5)
    b = levels.Level(price=101.0, kind="sma_50", strength=0.25)  # 1% away
    out = levels._dedup_levels([a, b])
    assert len(out) == 2


def test_dedup_levels_tiebreaks_by_kind_priority_when_strength_equal():
    """If strengths tie, swing_high beats sma_50 beats round_number."""
    a = levels.Level(price=100.0, kind="round_number", strength=0.3)
    b = levels.Level(price=100.30, kind="sma_50", strength=0.3)
    c = levels.Level(price=100.10, kind="swing_high", strength=0.3)
    out = levels._dedup_levels([a, b, c])
    assert len(out) == 1
    assert out[0] is c  # swing_high wins the tie


def test_dedup_levels_handles_empty_input():
    assert levels._dedup_levels([]) == []


def test_dedup_levels_does_not_mutate_input():
    a = levels.Level(price=100.0, kind="swing_high", strength=0.8)
    b = levels.Level(price=100.30, kind="sma_50", strength=0.25)
    inp = [a, b]
    levels._dedup_levels(inp)
    assert inp == [a, b]  # input still intact


def test_dedup_levels_handles_chain_of_close_levels():
    """Three levels each within 0.5% of the next — should collapse to one."""
    a = levels.Level(price=100.0, kind="swing_high", strength=0.4)
    b = levels.Level(price=100.30, kind="sma_50", strength=0.5)
    c = levels.Level(price=100.60, kind="sma_20", strength=0.6)
    # a and b are within 0.5%; b and c are within 0.5%; a and c are 0.6% apart.
    # Sweep should still collapse all three.
    out = levels._dedup_levels([a, b, c])
    assert len(out) == 1
    assert out[0] is c  # highest strength of the cluster
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_levels.py -v`
Expected: FAIL on the 6 new tests with `AttributeError`.

- [ ] **Step 3: Implement `_dedup_levels`**

Append to `bullbot/v2/levels.py`:

```python
DEDUP_BAND_PCT = 0.005  # 0.5% — levels within this are considered duplicates

_KIND_PRIORITY = {
    "swing_high": 0,
    "swing_low": 1,
    "sma_200": 2,
    "sma_50": 3,
    "sma_20": 4,
    "round_number": 5,
}


def _dedup_levels(input_levels: list[Level]) -> list[Level]:
    """Collapse levels within DEDUP_BAND_PCT (0.5%) of each other.

    Strategy: sort by price, sweep forward, group adjacent close-priced
    levels into clusters. For each cluster, keep the level with the highest
    strength (ties broken by _KIND_PRIORITY).
    """
    if not input_levels:
        return []

    sorted_levels = sorted(input_levels, key=lambda lvl: lvl.price)
    clusters: list[list[Level]] = [[sorted_levels[0]]]
    for lvl in sorted_levels[1:]:
        prev = clusters[-1][-1]
        if abs(lvl.price - prev.price) / prev.price <= DEDUP_BAND_PCT:
            clusters[-1].append(lvl)
        else:
            clusters.append([lvl])

    out: list[Level] = []
    for cluster in clusters:
        best = max(
            cluster,
            key=lambda lvl: (lvl.strength, -_KIND_PRIORITY[lvl.kind]),
        )
        out.append(best)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_levels.py -v`
Expected: PASS (28 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/levels.py tests/unit/test_v2_levels.py
git commit -m "feat(v2/c2): _dedup_levels collapses within-0.5pct clusters by strength + kind priority"
```

---

## Task 6: `compute_sr` — public entry orchestrating all four helpers

**Files:**
- Modify: `bullbot/v2/levels.py` (append `compute_sr`)
- Modify: `tests/unit/test_v2_levels.py` (append orchestration tests)

The public function takes a list of bars (most recent close used as spot) and an optional `lookback` parameter that limits which bars feed `_find_swing_extrema`. Returns the combined, deduplicated, distance-sorted list of levels.

Default `lookback = 60` (about 3 trading months). Callers like the C.3 vehicle agent can pass a smaller lookback for short-term TA or a larger one for long-term theses.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_levels.py`:

```python
def test_compute_sr_returns_empty_for_empty_bars():
    assert levels.compute_sr([]) == []


def test_compute_sr_returns_levels_sorted_by_distance_to_last_close():
    """Build bars with a clear peak at 105 and a clear trough at 95, last
    close at 100. Levels should come back ordered by abs distance from 100."""
    closes = ([97, 98, 99, 100, 102, 103, 105, 103, 102, 100, 99, 97, 95,
               97, 99, 100, 101, 100])
    bars = [_bar(close=c, high=c + 0.5, low=c - 0.5) for c in closes]
    out = levels.compute_sr(bars, lookback=60)
    distances = [lvl.distance_to(spot=100.0) for lvl in out]
    assert distances == sorted(distances)


def test_compute_sr_includes_swing_sma_and_round_number_kinds():
    """Run with enough bars to populate all three Level sources."""
    bars = [_bar(close=100 + (i % 7), high=100 + (i % 7) + 0.5,
                 low=100 + (i % 7) - 0.5) for i in range(250)]
    out = levels.compute_sr(bars, lookback=60)
    kinds = {lvl.kind for lvl in out}
    # Must include at least sma_20 and probably round_number near 100
    assert "sma_20" in kinds
    assert "sma_50" in kinds
    assert "sma_200" in kinds


def test_compute_sr_respects_lookback_for_swing_detection():
    """Swing point far in the past (outside lookback) should NOT appear."""
    closes = [110.0] * 5  # old peak
    closes += [100.0] * 80  # 80 bars of flat at 100
    closes += [100.0]
    bars = [_bar(close=c, high=c + 0.5, low=c - 0.5) for c in closes]
    # lookback=60 means the old 110 peak (idx 0-4) is outside the lookback window
    out = levels.compute_sr(bars, lookback=60)
    swing_highs = [lvl for lvl in out if lvl.kind == "swing_high"]
    # No swing_high near 110 expected — the 110 peak is outside lookback
    assert not any(lvl.price > 105.0 for lvl in swing_highs)


def test_compute_sr_dedups_overlapping_sma_and_swing_levels():
    """If 50-day SMA happens to land near a swing high, expect ONE merged level."""
    closes = [100.0] * 60  # flat -> SMA_20 = SMA_50 = 100.0
    # Inject a swing high at 100.0 exactly
    closes[30] = 100.0  # already 100, but trigger the swing path
    bars = [_bar(close=c, high=c, low=c - 0.5) for c in closes]
    out = levels.compute_sr(bars, lookback=60)
    # Multiple sources land near 100.0; after dedup we expect AT MOST one level near 100.0
    near_100 = [lvl for lvl in out if abs(lvl.price - 100.0) < 0.5]
    assert len(near_100) <= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_levels.py -v`
Expected: FAIL on the 5 new tests with `AttributeError: module 'bullbot.v2.levels' has no attribute 'compute_sr'`.

- [ ] **Step 3: Implement `compute_sr`**

Append to `bullbot/v2/levels.py`:

```python
def compute_sr(bars: list, lookback: int = 60) -> list[Level]:
    """Compute the full S/R level set for a list of bars.

    Pipeline:
        1. Swing highs/lows over the last `lookback` bars (3-bar confirmation).
        2. 20 / 50 / 200 SMA values (from full bar history — these don't use lookback).
        3. Round-number snaps within 2% of the most recent close.
        4. Deduplicate within-0.5% clusters (keep highest strength).
        5. Sort by absolute distance to the most recent close.

    Returns an empty list if `bars` is empty.
    """
    if not bars:
        return []

    spot = bars[-1].close
    recent_bars = bars[-lookback:]

    candidates: list[Level] = []
    candidates.extend(_find_swing_extrema(recent_bars, n_confirm=3))
    candidates.extend(_sma_levels(bars))
    candidates.extend(_round_number_levels(spot=spot))

    deduped = _dedup_levels(candidates)
    return sorted(deduped, key=lambda lvl: lvl.distance_to(spot=spot))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_levels.py -v`
Expected: PASS (33 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/levels.py tests/unit/test_v2_levels.py
git commit -m "feat(v2/c2): compute_sr orchestrates swing + SMA + round-number + dedup + sort"
```

---

## Task 7: Full regression check

**Files:** none (test-only verification step)

- [ ] **Step 1: Run the full unit suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit -q`
Expected: All previously-passing tests still pass; the new `test_v2_levels.py` adds 33 tests, bringing unit total from 584 → 617.

- [ ] **Step 2: Run the integration suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration -q`
Expected: All 80 integration tests still pass (none directly exercise levels.py yet — that comes when C.3 wires it into the runner).

- [ ] **Step 3: Static-import sanity check**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -c "from bullbot.v2 import levels; print(levels.Level, levels.compute_sr, levels.VALID_KINDS)"`
Expected: prints the public exports without ImportError.

- [ ] **Step 4: Optional marker commit**

```bash
git commit --allow-empty -m "chore(v2/c2): Phase C.2 complete — levels.py landed"
```

---

## Acceptance criteria

C.2 is complete when ALL of the following hold:

1. `bullbot/v2/levels.py` exists and exports `Level`, `compute_sr`, and the public constant `VALID_KINDS`.
2. `tests/unit/test_v2_levels.py` contains the 33 tests listed in Tasks 1–6 and they all pass.
3. Full unit + integration suite is green (no regressions vs the C.1 baseline of 584 unit + 80 integration).
4. `levels.py` is under 200 LOC.
5. No new third-party dependencies introduced.
6. No DB schema changes, no migrations.
7. `compute_sr` is pure (same bars in → same Levels out, no I/O).

## What this unblocks

- **C.3 (vehicle agent):** `vehicle.build_llm_context()` calls `compute_sr(bars)` and feeds `nearest_resistance` / `nearest_support` into the LLM JSON.
- **C.3 (exits.py):** at entry time, the agent picks `profit_target_price` and `stop_price` from S/R levels (e.g., "target = nearest resistance above spot, stop = nearest support below"). The exit-rule evaluator just checks underlying ≷ stored targets — no recomputation per tick.

## Notes for the implementer

- **No new third-party libraries.** Stdlib `statistics.mean` is enough — no NumPy/pandas.
- **Bars are duck-typed** with `.high`, `.low`, `.close`. Tests use `SimpleNamespace`. Production callers pass the same shape from `runner._load_bars` / `chains._load_bars`.
- **Strength is heuristic, not statistical.** The 0–1 scale is for the LLM's interpretability; do not over-engineer the scoring function in C.2 — C.3's prompt design will reveal what actually matters.
- **`lookback` only affects swing detection.** SMA windows are fixed (20/50/200) and read from the full bar history because that's the standard trader convention. Round numbers don't depend on bars at all.
- **Dedup priority is opinionated.** `swing_high` wins ties over SMAs because confirmed price-action levels are what most TA traders actually watch. If the C.3 LLM context reveals this ordering hurts pick quality, revisit in a follow-up.
- **Worktree `.venv` path** is `/Users/danield.runion/Projects/bull-bot/.venv/bin/python`. Same note as C.0 and C.1.

---

# SECTION 3 — Review Prompt


## How to use this bundle

Three files together form the review bundle:

1. `2026-05-17-phase-c2-levels-support-resistance.md` — the implementation plan (7 TDD tasks, ~33 unit tests).
2. `2026-05-17-phase-c2-levels-support-resistance-context.md` — project background.
3. `2026-05-17-phase-c2-levels-support-resistance-review-prompt.md` — this file.

Read the context document first, then the plan, then return the review structured as below.

## What you are reviewing

An implementation plan for Phase C.2 of the Bull-Bot v2 vehicle-agent rollout. C.0 (schema + positions + risk) and C.1 (Yahoo + Black-Scholes chains) are already merged into `main`. C.2 ships `bullbot/v2/levels.py` — a pure, stdlib-only support/resistance calculator that the C.3 vehicle agent will consume as LLM context and that `exits.py` will use to set entry-time profit-target and stop-price values.

The plan was written using the Superpowers `writing-plans` skill (same skill used for C.0 and C.1). It is 7 TDD tasks: Level dataclass → 4 private helpers (swing extrema, SMA, round-number, dedup) → public `compute_sr` orchestrator → regression check.

## What you should review for

Please prioritize feedback in this order:

### Tier 1 — Things that would invalidate the plan

1. **Swing-detection correctness.** The plan uses strict-less-than for swing high confirmation: `bars[j].high < cand_high for all j in window, j != i`. Plateau bars (consecutive bars at the SAME high) will produce NO swing high because no bar is strictly greater. Is this the right behavior or should ties be handled differently (e.g., use `<=` for some neighbors)? Real-world stocks frequently form double-tops at the same level.
2. **Strength scaling math.** Swing strength = `min(1.0, touch_count / 5.0)`. SMA strength = `min(1.0, window / 200.0)`. Round-number strength = fixed `0.3`. Are these comparable on a 0–1 scale? When the C.3 vehicle agent sees `nearest_resistance.strength = 0.8` and `nearest_support.strength = 0.3`, will those numbers communicate what an experienced trader would expect?
3. **Round-number step-size table.** `$1 / $5 / $10 / $50` at `< $50 / < $200 / < $1000 / >= $1000`. The 200/1000 thresholds are arbitrary — should they be quartiles of the universe, log-scaled, or something else? For MSTR (currently ~$400), the step is $10 which feels right; for a $1500 BRK.B, the step is $50 which also feels right. Are there pathological cases?
4. **Dedup semantics.** Within-0.5% clusters collapse to the highest-strength level (tiebreak by `swing > sma_200 > sma_50 > sma_20 > round_number`). Is 0.5% the right band? For a $400 stock that's $2 — about a tick or two of normal noise. For a $50 stock that's $0.25 — finer than typical resolution. Is this OK or should the band scale?

### Tier 2 — Things that would improve the plan

5. **Lookback vs full-history split for SMAs.** The plan uses `lookback=60` for swing detection but feeds SMAs the full bar history. Is this right? Or should everything use the same lookback? An older 200-day SMA computed over the most recent 200 bars vs a 200-day SMA over a longer history with the most recent 200 sampled — semantically the same, but worth confirming.
6. **Bar shape duck-typing vs Pydantic.** The plan uses `SimpleNamespace` bars (duck-typed) matching v2 convention. The repo also has a strict Pydantic `Bar` schema in `bullbot/data/schemas.py` used by the v1 path. Is the duck-typing OK, or should `levels.py` accept the Pydantic Bar too (or convert)?
7. **Missing test scenarios.** Any meaningful S/R scenarios the test list misses? Examples: gap days where high << prev_low, single-day spikes that are then immediately retraced, bars with `low > close` (shouldn't happen but if it does), very long flat periods, plateau swing detection (related to Finding 1).

### Tier 3 — Things to flag but not necessarily fix

8. Is the 7-task granularity right, or should any tasks be split / merged for subagent execution?
9. Anything in "Notes for the implementer" that should be promoted into a task body?
10. Is the strength heuristic over- or under-engineered for what the C.3 LLM agent will actually use?

## Format your response as

```
## Tier 1 findings

### Finding 1
- What: <one-sentence description>
- Why it matters: <2-3 sentences>
- Suggested change: <concrete edit to plan>

### Finding 2
...

## Tier 2 findings

(same format)

## Tier 3 findings

(same format)

## Things you got right (brief)

(short bulleted list of plan decisions you'd specifically endorse)

## Overall recommendation

(approve as-is / approve with the Tier 1 changes / reject and rewrite — pick one and justify in 3-5 sentences)
```

## Constraints on your review

- Do not propose using a paid data source or moving off Yahoo Finance — out of scope.
- Do not propose changing the TDD plan structure (failing-test-first, one commit per task) — that's locked by the `writing-plans` skill.
- Do not propose adding NumPy or pandas — stdlib-only is a deliberate constraint to keep the v2 codepath dependency-light.
- Do not rewrite C.0 or C.1 (shipped) or expand into C.3+ scope (vehicle agent, backtest, runner) — those have their own plans.
- The reader (Dan) is a PM, not a backend engineer. Frame Tier 1 findings in terms of trading consequences (wrong levels surfaced to the LLM, missed entries, false signals), not refactor opinions.
