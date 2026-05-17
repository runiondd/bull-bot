# Bull-Bot v2 Phase C.3a — Earnings module (`earnings.py`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `bullbot/v2/earnings.py` — yfinance-backed earnings-date lookup with two public entry points: `fetch_next_earnings(ticker, today, client=None) → EarningsEvent | None` and `earnings_window_active(ticker, today, iv_rank, client=None) → bool`. After this lands, both `exits.py` (C.3b) and `vehicle.py` (C.3c) can ask "is this ticker in its earnings window?" without re-implementing the lookup.

**Architecture:** Thin module, one source (yfinance `Ticker.get_earnings_dates`), one cache layer (none — yfinance call per query, ~30 queries/day across the universe is acceptable overhead). Yahoo client is injectable as a callable parameter, same pattern as `bullbot/v2/chains.py` so tests run with zero network dependency. Window trigger combines the design's two conditions: `days_to_earnings ≤ 14 OR iv_rank > 0.75` (Grok review Tier 2 Finding 7).

**Tech Stack:** Python 3.11+, stdlib `datetime`, `dataclasses`, `pandas` (already a project dependency, used to parse yfinance DataFrame), `pytest`. No new third-party libraries. No DB schema changes.

**Spec reference:** [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md`](../specs/2026-05-16-phase-c-vehicle-agent-design.md) sections 3 (earnings handling scope), 4.5 (LLM context `days_to_earnings` + `earnings_window_active` fields), 4.6 (validation step 6 — earnings/high-IV vehicle whitelist). [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md`](../specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md) Tier 2 Finding 7 — expanded trigger condition.

---

## Pre-flight assumptions verified before writing tasks

- **yfinance `Ticker.get_earnings_dates(limit=12)` returns a pandas DataFrame** indexed by tz-aware `DatetimeIndex` with both past and future earnings. Future earnings rows have `NaN` in `Reported EPS`. Verified via local repl.
- **Yahoo client injection pattern** is in `bullbot/v2/chains.py:_default_yf_client` — same shape used here.
- **No new DB schema needed.** Earnings dates are fetched on demand. (If C.5 reveals performance issues across the universe, add a `v2_earnings_cache` table later — out of C.3a scope.)
- **`bullbot/v2/levels.py`, `chains.py`, `positions.py`, `risk.py` are stable** after C.0 / C.1 / C.2 merges.

---

## File Structure

| Path | Responsibility | Status |
|---|---|---|
| `bullbot/v2/earnings.py` | `EarningsEvent` dataclass, `fetch_next_earnings`, `days_to_print`, `earnings_window_active`. | **Create** |
| `tests/unit/test_v2_earnings.py` | Unit tests for the dataclass, yfinance parsing, failure modes, and the public window helper. | **Create** |
| `bullbot/db/migrations.py` | Unchanged. | — |
| Other v2 modules | Unchanged. | — |

Module size target: < 150 LOC.

---

## Task 1: `EarningsEvent` dataclass + module skeleton

**Files:**
- Create: `bullbot/v2/earnings.py`
- Create: `tests/unit/test_v2_earnings.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_v2_earnings.py`:

```python
"""Unit tests for bullbot.v2.earnings — yfinance earnings-date lookup."""
from __future__ import annotations

from datetime import date

import pytest

from bullbot.v2 import earnings


def test_earningsevent_rejects_non_date_event_date():
    with pytest.raises(TypeError, match="event_date must be a date"):
        earnings.EarningsEvent(ticker="AAPL", event_date="2026-07-25")


def test_earningsevent_normalizes_ticker_to_uppercase():
    ev = earnings.EarningsEvent(ticker="aapl", event_date=date(2026, 7, 25))
    assert ev.ticker == "AAPL"


def test_earningsevent_days_until_returns_positive_for_future_event():
    ev = earnings.EarningsEvent(ticker="AAPL", event_date=date(2026, 6, 1))
    assert ev.days_until(today=date(2026, 5, 17)) == 15


def test_earningsevent_days_until_returns_zero_for_today():
    ev = earnings.EarningsEvent(ticker="AAPL", event_date=date(2026, 5, 17))
    assert ev.days_until(today=date(2026, 5, 17)) == 0


def test_earningsevent_days_until_returns_negative_for_past_event():
    ev = earnings.EarningsEvent(ticker="AAPL", event_date=date(2026, 5, 10))
    assert ev.days_until(today=date(2026, 5, 17)) == -7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_earnings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bullbot.v2.earnings'`.

- [ ] **Step 3: Implement the skeleton**

Create `bullbot/v2/earnings.py`:

```python
"""Earnings-date lookup for v2 Phase C.

Two public entry points:
- fetch_next_earnings(ticker, today, client=None) -> EarningsEvent | None
  Returns the soonest future earnings event (or None if none found within
  yfinance's 12-event window).
- earnings_window_active(ticker, today, iv_rank, client=None) -> bool
  True when days_to_earnings <= 14 OR iv_rank > 0.75 (Grok review Tier 2 #7).

Yahoo client is injected as a callable for testability — mirrors the pattern
in bullbot/v2/chains.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class EarningsEvent:
    ticker: str
    event_date: date

    def __post_init__(self) -> None:
        if not isinstance(self.event_date, date):
            raise TypeError(
                f"event_date must be a date; got {type(self.event_date).__name__}"
            )
        # Normalize ticker symbol to uppercase to match the rest of the v2 codebase.
        self.ticker = self.ticker.upper()

    def days_until(self, *, today: date) -> int:
        """Integer day count from `today` to `event_date`. Positive = future,
        zero = today, negative = past."""
        return (self.event_date - today).days
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_earnings.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/earnings.py tests/unit/test_v2_earnings.py
git commit -m "feat(v2/c3a): EarningsEvent dataclass + days_until helper"
```

---

## Task 2: `fetch_next_earnings` — yfinance parsing (happy path)

**Files:**
- Modify: `bullbot/v2/earnings.py` (append `_default_yf_client` + `fetch_next_earnings`)
- Modify: `tests/unit/test_v2_earnings.py` (append fetch tests)

`yfinance.Ticker(symbol).get_earnings_dates(limit=12)` returns a pandas DataFrame indexed by `DatetimeIndex` (tz-aware, mixed past and future events). Rows are sorted descending by date (most recent past first, then upcoming). Columns include `EPS Estimate`, `Reported EPS`, `Surprise(%)`. Future events have `NaN` in `Reported EPS`.

`fetch_next_earnings` filters to future-only events (where `index date >= today`), picks the soonest one, returns `EarningsEvent(ticker, event_date=that_date.date())`. Returns `None` if no future events in the 12-row window.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_earnings.py`:

```python
import pandas as pd


class _FakeYFTicker:
    """Mimics yfinance.Ticker minimally — only the get_earnings_dates surface."""
    def __init__(self, dates_df: pd.DataFrame | None):
        self._df = dates_df
    def get_earnings_dates(self, limit: int = 12):
        return self._df


def _earnings_df(*event_strings: str) -> pd.DataFrame:
    """Build a yfinance-shaped earnings DataFrame from ISO date strings."""
    idx = pd.DatetimeIndex([pd.Timestamp(s, tz="America/New_York") for s in event_strings])
    return pd.DataFrame(
        {"EPS Estimate": [None] * len(event_strings),
         "Reported EPS": [None] * len(event_strings),
         "Surprise(%)": [None] * len(event_strings)},
        index=idx,
    )


def test_fetch_next_earnings_returns_soonest_future_event():
    df = _earnings_df("2026-08-01", "2026-07-25", "2026-05-01", "2026-02-01")
    fake = _FakeYFTicker(df)
    ev = earnings.fetch_next_earnings(
        ticker="AAPL", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert ev is not None
    assert ev.ticker == "AAPL"
    assert ev.event_date == date(2026, 7, 25)


def test_fetch_next_earnings_returns_event_dated_today_as_future():
    """Earnings exactly on `today` count as future (days_until == 0)."""
    df = _earnings_df("2026-05-17", "2026-02-01")
    fake = _FakeYFTicker(df)
    ev = earnings.fetch_next_earnings(
        ticker="AAPL", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert ev is not None
    assert ev.event_date == date(2026, 5, 17)


def test_fetch_next_earnings_ignores_past_events_only():
    df = _earnings_df("2026-05-01", "2026-02-01", "2025-11-01")
    fake = _FakeYFTicker(df)
    ev = earnings.fetch_next_earnings(
        ticker="AAPL", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert ev is None  # nothing in the future


def test_fetch_next_earnings_normalizes_ticker_to_uppercase():
    df = _earnings_df("2026-06-01")
    fake = _FakeYFTicker(df)
    ev = earnings.fetch_next_earnings(
        ticker="aapl", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert ev.ticker == "AAPL"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_earnings.py -v`
Expected: FAIL on the 4 new tests with `AttributeError: module 'bullbot.v2.earnings' has no attribute 'fetch_next_earnings'`.

- [ ] **Step 3: Implement `fetch_next_earnings`**

Append to `bullbot/v2/earnings.py`:

```python
from typing import Callable


def _default_yf_client():
    """Lazy yfinance import — keeps tests independent of yfinance availability.
    Mirrors bullbot/v2/chains.py:_default_yf_client."""
    import yfinance as yf
    return lambda symbol: yf.Ticker(symbol)


def fetch_next_earnings(
    *,
    ticker: str,
    today: date,
    client: Callable[[str], object] | None = None,
) -> EarningsEvent | None:
    """Return the soonest future earnings event (event_date >= today) for
    `ticker`, or None if no upcoming earnings in yfinance's 12-row window.

    The yfinance DataFrame index is tz-aware (typically America/New_York);
    we strip tz and convert to a plain date for the EarningsEvent.
    """
    if client is None:
        client = _default_yf_client()

    ticker_obj = client(ticker)
    df = ticker_obj.get_earnings_dates(limit=12)
    if df is None or df.empty:
        return None

    # yfinance returns DatetimeIndex sorted descending; filter to >= today and
    # pick the smallest (soonest) future date.
    future_dates = [
        ts.date() for ts in df.index
        if ts.date() >= today
    ]
    if not future_dates:
        return None

    soonest = min(future_dates)
    return EarningsEvent(ticker=ticker, event_date=soonest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_earnings.py -v`
Expected: PASS (9 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/earnings.py tests/unit/test_v2_earnings.py
git commit -m "feat(v2/c3a): fetch_next_earnings — yfinance parsing happy path with injected client"
```

---

## Task 3: `fetch_next_earnings` — graceful failure modes

**Files:**
- Modify: `bullbot/v2/earnings.py` (wrap fetch in try/except + log)
- Modify: `tests/unit/test_v2_earnings.py` (append failure tests)

yfinance flakes regularly: network timeouts, delisted tickers, ETFs/funds with no earnings, transient 5xx. Contract: return `None` on any of those + log a structured warning. No persistence to clean up (no cache in C.3a).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_earnings.py`:

```python
def test_fetch_next_earnings_returns_none_when_yfinance_returns_none():
    """ETFs / funds / new IPOs often have get_earnings_dates() return None."""
    fake = _FakeYFTicker(None)
    ev = earnings.fetch_next_earnings(
        ticker="SPY", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert ev is None


def test_fetch_next_earnings_returns_none_when_dataframe_is_empty():
    fake = _FakeYFTicker(pd.DataFrame())
    ev = earnings.fetch_next_earnings(
        ticker="XYZ", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert ev is None


def test_fetch_next_earnings_returns_none_when_yfinance_raises_on_construct():
    def raising_client(symbol):
        raise ConnectionError("simulated yahoo timeout")
    ev = earnings.fetch_next_earnings(
        ticker="AAPL", today=date(2026, 5, 17),
        client=raising_client,
    )
    assert ev is None


def test_fetch_next_earnings_returns_none_when_get_earnings_dates_raises():
    class RaisingTicker:
        def get_earnings_dates(self, limit=12):
            raise ValueError("simulated yfinance parse error")
    ev = earnings.fetch_next_earnings(
        ticker="AAPL", today=date(2026, 5, 17),
        client=lambda symbol: RaisingTicker(),
    )
    assert ev is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_earnings.py -v`
Expected: 2 of 4 pass already (the None / empty cases — those returned None from the data path); the 2 raising-client tests fail with unhandled exceptions propagating out.

- [ ] **Step 3: Add failure handling**

Replace the `fetch_next_earnings` function body in `bullbot/v2/earnings.py` with the version below (the change: wrap the entire Yahoo call in try/except, log on failure, return None):

```python
import logging

_log = logging.getLogger(__name__)


def fetch_next_earnings(
    *,
    ticker: str,
    today: date,
    client: Callable[[str], object] | None = None,
) -> EarningsEvent | None:
    """Return the soonest future earnings event (event_date >= today) for
    `ticker`, or None if no upcoming earnings or any failure.

    Failure modes that yield None:
      - Yahoo client construct raises (network error, bad ticker)
      - get_earnings_dates raises (yfinance parse error, schema change)
      - yfinance returns None (ETFs, funds, new IPOs)
      - DataFrame empty
      - DataFrame contains only past events
    """
    if client is None:
        client = _default_yf_client()

    try:
        ticker_obj = client(ticker)
        df = ticker_obj.get_earnings_dates(limit=12)
    except Exception as exc:  # noqa: BLE001 — Yahoo can raise anything
        _log.warning("fetch_next_earnings: yfinance failed for %s: %s", ticker, exc)
        return None

    if df is None or df.empty:
        return None

    future_dates = [
        ts.date() for ts in df.index
        if ts.date() >= today
    ]
    if not future_dates:
        return None

    soonest = min(future_dates)
    return EarningsEvent(ticker=ticker, event_date=soonest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_earnings.py -v`
Expected: PASS (13 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/earnings.py tests/unit/test_v2_earnings.py
git commit -m "feat(v2/c3a): fetch_next_earnings — graceful failure on yfinance errors"
```

---

## Task 4: `days_to_print` — convenience wrapper returning int

**Files:**
- Modify: `bullbot/v2/earnings.py` (append `days_to_print`)
- Modify: `tests/unit/test_v2_earnings.py` (append days-to-print tests)

`days_to_print` is the most common downstream call (used by `earnings_window_active` AND by `vehicle.py` LLM context). It wraps `fetch_next_earnings` and returns an `int`: positive days until next earnings, or `DAYS_TO_PRINT_NONE_SENTINEL` (999) when no upcoming earnings can be found. The sentinel is chosen so callers comparing `days_to_print <= 14` get `False` without needing to special-case `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_earnings.py`:

```python
def test_days_to_print_returns_int_for_future_earnings():
    df = _earnings_df("2026-06-01", "2026-02-01")
    fake = _FakeYFTicker(df)
    n = earnings.days_to_print(
        ticker="AAPL", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert n == 15


def test_days_to_print_returns_sentinel_when_no_upcoming_event():
    df = _earnings_df("2026-02-01", "2025-11-01")
    fake = _FakeYFTicker(df)
    n = earnings.days_to_print(
        ticker="AAPL", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert n == earnings.DAYS_TO_PRINT_NONE_SENTINEL
    # Sentinel must be large enough that downstream `<=14` checks return False
    assert n > 14


def test_days_to_print_returns_sentinel_when_yfinance_fails():
    def raising_client(symbol):
        raise ConnectionError("network down")
    n = earnings.days_to_print(
        ticker="AAPL", today=date(2026, 5, 17),
        client=raising_client,
    )
    assert n == earnings.DAYS_TO_PRINT_NONE_SENTINEL
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_earnings.py -v`
Expected: FAIL on the 3 new tests with `AttributeError: module 'bullbot.v2.earnings' has no attribute 'days_to_print'`.

- [ ] **Step 3: Implement `days_to_print`**

Append to `bullbot/v2/earnings.py`:

```python
DAYS_TO_PRINT_NONE_SENTINEL = 999  # large enough that any `<= N` check returns False


def days_to_print(
    *,
    ticker: str,
    today: date,
    client: Callable[[str], object] | None = None,
) -> int:
    """Days from `today` until the next upcoming earnings event.

    Returns DAYS_TO_PRINT_NONE_SENTINEL (999) when no upcoming earnings
    can be found (no events in yfinance window, all past, or fetch failure).
    The sentinel lets callers do `if days_to_print(...) <= 14` without
    branching on None.
    """
    ev = fetch_next_earnings(ticker=ticker, today=today, client=client)
    if ev is None:
        return DAYS_TO_PRINT_NONE_SENTINEL
    return ev.days_until(today=today)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_earnings.py -v`
Expected: PASS (16 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/earnings.py tests/unit/test_v2_earnings.py
git commit -m "feat(v2/c3a): days_to_print convenience wrapper with sentinel for missing events"
```

---

## Task 5: `earnings_window_active` — combined trigger (Grok Tier 2 Finding 7)

**Files:**
- Modify: `bullbot/v2/earnings.py` (append `earnings_window_active`)
- Modify: `tests/unit/test_v2_earnings.py` (append window tests)

The trigger condition from the design + Grok review: `days_to_earnings ≤ 14 OR iv_rank > 0.75`. Returns a plain bool. Constants for the thresholds (`EARNINGS_WINDOW_DAYS = 14`, `HIGH_IV_RANK_THRESHOLD = 0.75`) live as module-level so the C.3c vehicle agent can re-export or reference them when building the LLM context.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_earnings.py`:

```python
def test_earnings_window_active_true_when_within_14_days():
    df = _earnings_df("2026-05-25")  # 8 days away from 2026-05-17
    fake = _FakeYFTicker(df)
    assert earnings.earnings_window_active(
        ticker="AAPL", today=date(2026, 5, 17), iv_rank=0.30,
        client=lambda symbol: fake,
    ) is True


def test_earnings_window_active_false_when_outside_14_days_and_low_iv():
    df = _earnings_df("2026-06-15")  # 29 days away
    fake = _FakeYFTicker(df)
    assert earnings.earnings_window_active(
        ticker="AAPL", today=date(2026, 5, 17), iv_rank=0.30,
        client=lambda symbol: fake,
    ) is False


def test_earnings_window_active_true_when_iv_rank_above_75pct():
    """Grok Tier 2 Finding 7: high IV alone should trigger the window even
    if earnings are far out (catches non-earnings vol spikes)."""
    df = _earnings_df("2026-09-01")  # 107 days away
    fake = _FakeYFTicker(df)
    assert earnings.earnings_window_active(
        ticker="AAPL", today=date(2026, 5, 17), iv_rank=0.80,
        client=lambda symbol: fake,
    ) is True


def test_earnings_window_active_false_when_iv_rank_at_75pct_threshold():
    """Trigger is STRICTLY > 0.75, not >=. iv_rank=0.75 exactly is not active."""
    df = _earnings_df("2026-09-01")
    fake = _FakeYFTicker(df)
    assert earnings.earnings_window_active(
        ticker="AAPL", today=date(2026, 5, 17), iv_rank=0.75,
        client=lambda symbol: fake,
    ) is False


def test_earnings_window_active_true_at_14_day_boundary_inclusive():
    """Days <= 14 is inclusive — earnings exactly 14 days out is in the window."""
    df = _earnings_df("2026-05-31")  # exactly 14 days from 2026-05-17
    fake = _FakeYFTicker(df)
    assert earnings.earnings_window_active(
        ticker="AAPL", today=date(2026, 5, 17), iv_rank=0.30,
        client=lambda symbol: fake,
    ) is True


def test_earnings_window_active_when_no_earnings_found_falls_back_to_iv_rank_only():
    """No upcoming earnings (sentinel returned) means the days check is
    effectively False. Then the iv_rank trigger decides."""
    fake = _FakeYFTicker(None)  # ETF / no earnings
    # Low IV + no earnings -> not in window
    assert earnings.earnings_window_active(
        ticker="SPY", today=date(2026, 5, 17), iv_rank=0.20,
        client=lambda symbol: fake,
    ) is False
    # High IV + no earnings -> in window via IV branch
    assert earnings.earnings_window_active(
        ticker="SPY", today=date(2026, 5, 17), iv_rank=0.90,
        client=lambda symbol: fake,
    ) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_earnings.py -v`
Expected: FAIL on the 6 new tests with `AttributeError: module 'bullbot.v2.earnings' has no attribute 'earnings_window_active'`.

- [ ] **Step 3: Implement `earnings_window_active`**

Append to `bullbot/v2/earnings.py`:

```python
EARNINGS_WINDOW_DAYS = 14
HIGH_IV_RANK_THRESHOLD = 0.75


def earnings_window_active(
    *,
    ticker: str,
    today: date,
    iv_rank: float,
    client: Callable[[str], object] | None = None,
) -> bool:
    """True when the ticker is in its earnings / high-IV window:
        days_to_earnings <= EARNINGS_WINDOW_DAYS (14)
        OR iv_rank > HIGH_IV_RANK_THRESHOLD (0.75)

    Grok review Tier 2 Finding 7 — the IV-rank branch catches non-earnings
    vol spikes where long-premium has poor expectancy regardless of an
    upcoming print.
    """
    days = days_to_print(ticker=ticker, today=today, client=client)
    return days <= EARNINGS_WINDOW_DAYS or iv_rank > HIGH_IV_RANK_THRESHOLD
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_earnings.py -v`
Expected: PASS (22 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/earnings.py tests/unit/test_v2_earnings.py
git commit -m "feat(v2/c3a): earnings_window_active — combined 14-day + IV>0.75 trigger (Grok T2 F7)"
```

---

## Task 6: Full regression check

**Files:** none (test-only verification step)

- [ ] **Step 1: Run the full unit suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit -q`
Expected: All previously-passing tests still pass; the new `test_v2_earnings.py` adds 22 tests, bringing unit total from 617 → 639.

- [ ] **Step 2: Run the integration suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration -q`
Expected: All 80 integration tests still pass (none directly exercise earnings.py yet — that comes when C.3c vehicle agent wires it in).

- [ ] **Step 3: Static-import sanity check**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -c "from bullbot.v2 import earnings; print(earnings.EarningsEvent, earnings.fetch_next_earnings, earnings.days_to_print, earnings.earnings_window_active, earnings.EARNINGS_WINDOW_DAYS, earnings.HIGH_IV_RANK_THRESHOLD, earnings.DAYS_TO_PRINT_NONE_SENTINEL)"`
Expected: prints all public exports without ImportError.

- [ ] **Step 4: Optional marker commit**

```bash
git commit --allow-empty -m "chore(v2/c3a): Phase C.3a complete — earnings.py landed"
```

---

## Acceptance criteria

C.3a is complete when ALL of the following hold:

1. `bullbot/v2/earnings.py` exists and exports: `EarningsEvent`, `fetch_next_earnings`, `days_to_print`, `earnings_window_active`, plus public constants `EARNINGS_WINDOW_DAYS`, `HIGH_IV_RANK_THRESHOLD`, `DAYS_TO_PRINT_NONE_SENTINEL`.
2. `tests/unit/test_v2_earnings.py` contains the 22 tests listed in Tasks 1–5 and they all pass.
3. Full unit + integration suite is green (no regressions vs the C.2 baseline of 617 unit + 80 integration).
4. `earnings.py` is under 150 LOC.
5. No new third-party dependencies introduced.
6. No DB schema changes.

## What this unblocks

- **C.3b (`exits.py`):** consumes `earnings_window_active` for the post-assignment-shares exit-plan derivation, and `days_to_print` if Phase A signal logic needs to suppress entries before earnings.
- **C.3c (`vehicle.py`):** `build_llm_context()` puts `days_to_earnings` + `earnings_window_active` into the JSON the LLM sees, and the validation step uses the window to enforce the defined-risk / short-premium whitelist.

## Notes for the implementer

- **No DB cache.** yfinance call per query. ~30 calls/day across the universe = acceptable. If C.5 reveals daily-run latency issues, add a `v2_earnings_cache` table in a follow-up (out of C.3a scope).
- **`pd.Timestamp` `.date()` strips the time component AND the tz**, returning a plain `datetime.date`. This matches the `EarningsEvent.event_date` type. Tests construct timestamps with tz=`America/New_York`; the conversion to `.date()` is straightforward.
- **`DAYS_TO_PRINT_NONE_SENTINEL = 999`** is deliberately chosen so any reasonable `<= N` check returns False without requiring callers to special-case None. If you change it, audit every caller in C.3b and C.3c.
- **`iv_rank` is passed in by the caller**, not fetched here. C.3c will compute it from chain data + the existing `chains.IV_PROXY_MIN` / `IV_PROXY_MAX` work. C.3a stays focused on the date math.
- **Worktree `.venv` path** is `/Users/danield.runion/Projects/bull-bot/.venv/bin/python`. Same note as prior phases.
