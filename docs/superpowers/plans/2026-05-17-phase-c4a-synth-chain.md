# Bull-Bot v2 Phase C.4a — Backtest synth_chain module — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `bullbot/v2/backtest/synth_chain.py` — the historical-chain synthesizer for the backtest harness. Given a ticker + asof date + bars (underlying + VIX) + strikes + expiries, it returns a `bullbot.v2.chains.Chain` object whose quotes are Black-Scholes-priced from a regime-aware IV estimate. Includes the Grok review Tier 1 Finding 3 event-day IV bump: on historical bars with `|return| ≥ 3%` OR `TR ≥ 3 × ATR_14`, inflate IV by 1.75× for that day, decaying linearly back to 1.0× over the following 5 trading days. After this lands, C.4b's `runner.py` can replay 2 years of bars and call `vehicle.pick` against synthesized chains for backtest evaluation.

**Architecture:** Pure-function module. Reuses everything from C.1 (`bullbot/v2/chains.py`) — `_iv_proxy` for the baseline regime IV, `_price_leg_bs` would be there but we go one layer lower and call `bullbot.data.synthetic_chain.bs_price` directly because we're pricing whole grids of strikes per expiry, not one leg at a time. New helper `_event_day_iv_multiplier(bars, asof_date)` looks back at the last 5 trading days for qualifying bars and returns a multiplier in [1.0, 1.75]. New public `synthesize()` orchestrates: filter expiries to 21-365 DTE, filter strikes to ATM ±10%, compute one IV per (ticker, asof_date), BS-price each (strike, kind, expiry) leg, return a `Chain`. Returned shape exactly matches `bullbot.v2.chains.Chain` so backtest code can pass synthesized chains into the same `price_leg` / dispatcher path as live ones.

**Tech Stack:** Python 3.11+, stdlib `datetime` / `math`, existing `bullbot.v2.chains` (Chain, ChainQuote, `_iv_proxy`), existing `bullbot.data.synthetic_chain.bs_price`, `pytest`. No new third-party libraries. No DB schema changes.

**Spec reference:** [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md`](../specs/2026-05-16-phase-c-vehicle-agent-design.md) sections 4.9 (backtest harness — primary spec), including the BS-pricing constraints (ATM ±10%, 21-365 DTE). [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md`](../specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md) **Tier 1 Finding 3** — event-day IV bump (the most load-bearing fix in this plan; absent this, credit-strategy backtest results are systematically over-optimistic).

---

## Pre-flight assumptions verified before writing tasks

- **`bullbot.v2.chains` exports** `Chain`, `ChainQuote`, `_iv_proxy`, `ATM_BAND_PCT = 0.05` (different from the 0.10 BS-pricing band in this plan — both coexist for different purposes).
- **`bullbot.data.synthetic_chain.bs_price(spot, strike, t_years, vol, r, kind)`** is a pure function with `kind ∈ {"C", "P"}` (different from `chains.ChainQuote.kind` which uses `"call" / "put"` — translate at call sites).
- **`bullbot.data.synthetic_chain.realized_vol(bars, window=30)`** returns annualized realized vol (used internally by `chains._iv_proxy`).
- **Bars are SimpleNamespace-shaped** with `.close`, `.high`, `.low`, `.ts` attributes. Same shape across all v2 modules.
- **VIX bars come from the `bars` table** under `ticker='VIX', timeframe='1d'` — caller loads them, this module never queries DB.
- **No new schema needed.** Backtest harness produces in-memory `Chain` objects that downstream code consumes; persistence happens in C.4b's runner if at all.
- **`bullbot/v2/backtest/` directory does not exist yet** — C.4a creates it. Distinct from the v1 `bullbot/backtest/` directory, which is unrelated.

---

## File Structure

| Path | Responsibility | Status |
|---|---|---|
| `bullbot/v2/backtest/__init__.py` | Empty package marker. | **Create** |
| `bullbot/v2/backtest/synth_chain.py` | Constants, `_event_day_iv_multiplier`, `_synth_iv`, `_strikes_in_band`, `_dtes_in_band`, public `synthesize`. | **Create** |
| `tests/unit/test_v2_backtest_synth_chain.py` | Unit tests per helper + `synthesize` end-to-end. | **Create** |
| Other v2 modules | Unchanged. | — |
| `bullbot/db/migrations.py` | Unchanged. | — |

Module size target: < 250 LOC.

---

## Task 1: Package skeleton + `_event_day_iv_multiplier` (Grok T1 F3)

**Files:**
- Create: `bullbot/v2/backtest/__init__.py` (empty)
- Create: `bullbot/v2/backtest/synth_chain.py`
- Create: `tests/unit/test_v2_backtest_synth_chain.py`

Per Grok Tier 1 Finding 3: when a historical bar has `|close-to-close return| ≥ 3%` OR `TR ≥ 3 × ATR_14`, inflate the IV proxy by 1.75× on that day. The bump decays linearly back to 1.0× over the following 5 trading days. Asof a given date, the multiplier is `1.0 + 0.75 × max((5 − days_since_event) / 5, 0)` evaluated over the most-recent qualifying event in the last 5 bars (if multiple events occurred, the most recent wins — recency dominates because that's what gives the strongest BS-pricing signal at the moment we're pricing).

When no qualifying event in the last 5 trading days, return `1.0`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_v2_backtest_synth_chain.py`:

```python
"""Unit tests for bullbot.v2.backtest.synth_chain."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from bullbot.v2.backtest import synth_chain


def _bar(close, high=None, low=None, ts=0):
    return SimpleNamespace(
        ts=ts, open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=1_000_000,
    )


def test_event_day_multiplier_returns_1_for_steady_bars():
    """No qualifying event in the last 5 bars -> multiplier = 1.0."""
    bars = [_bar(close=100.0 + i * 0.01) for i in range(30)]  # tiny drift
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == 1.0


def test_event_day_multiplier_returns_175_on_day_of_event():
    """A 5% spike on the most recent bar -> multiplier = 1.75 (event_age=0)."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    bars[-1] = _bar(close=105.0, high=106.0, low=99.0)  # 5% spike on last bar
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == pytest.approx(1.75, abs=0.01)


def test_event_day_multiplier_decays_linearly_back_to_1():
    """Event was 2 days ago: multiplier = 1.0 + 0.75 × (5 - 2)/5 = 1.45."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    bars[-3] = _bar(close=105.0, high=106.0, low=99.0)  # spike 2 days ago
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == pytest.approx(1.0 + 0.75 * (5 - 2) / 5, abs=0.01)


def test_event_day_multiplier_returns_1_after_5_day_decay():
    """Event was 5+ days ago -> multiplier back to 1.0."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    bars[-6] = _bar(close=105.0, high=106.0, low=99.0)  # 5 days ago (outside lookback)
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == 1.0


def test_event_day_multiplier_uses_true_range_rule():
    """Big TR on otherwise-flat close: TR rule fires even when return < 3%."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    # day at idx -1: close back to 100 but high/low blown out
    bars[-1] = _bar(close=100.0, high=110.0, low=90.0)
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == pytest.approx(1.75, abs=0.01)


def test_event_day_multiplier_picks_most_recent_event_when_multiple():
    """Two events in lookback: the more recent one wins (highest multiplier)."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    bars[-5] = _bar(close=110.0, high=112.0, low=98.0)  # event 4 days ago
    bars[-2] = _bar(close=105.0, high=106.0, low=99.0)  # event 1 day ago
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    # 1 day ago -> 1.0 + 0.75 * (5-1)/5 = 1.60
    assert mult == pytest.approx(1.0 + 0.75 * 4 / 5, abs=0.01)


def test_event_day_multiplier_returns_1_for_too_few_bars():
    """Need at least ATR_WINDOW + 1 = 15 bars for ATR computation."""
    bars = [_bar(close=100.0) for _ in range(10)]
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_synth_chain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bullbot.v2.backtest'`.

- [ ] **Step 3: Create the package + module + helper**

Create `bullbot/v2/backtest/__init__.py` as an empty file:

```python
"""Bull-Bot v2 Phase C.4 backtest harness."""
```

Create `bullbot/v2/backtest/synth_chain.py`:

```python
"""Historical chain synthesizer for the v2 backtest harness.

Given bars + VIX + asof date + (expiries, strikes), produce a
bullbot.v2.chains.Chain whose quotes are Black-Scholes-priced from a
regime-aware IV proxy that includes the Grok review Tier 1 Finding 3
event-day bump (1.75x on bars with |return| >= 3% OR TR >= 3 x ATR_14,
decaying linearly back to 1.0x over 5 trading days).

Constraints to keep BS error bounded:
  - Strike range restricted to ATM +/- 10%.
  - DTE restricted to 21 - 365 days.
Vehicle agent declares restricted mode in backtest context so it won't
pick legs outside these bounds.
"""
from __future__ import annotations

EVENT_DAY_RETURN_PCT = 0.03
EVENT_DAY_TR_MULT = 3.0
EVENT_DAY_BUMP_MULT = 1.75
EVENT_DAY_DECAY_BARS = 5
ATR_WINDOW = 14


def _event_day_iv_multiplier(*, bars: list, lookback: int = EVENT_DAY_DECAY_BARS) -> float:
    """Return a multiplier in [1.0, EVENT_DAY_BUMP_MULT] reflecting the
    most recent qualifying event in the last `lookback` trading days.

    Event qualifier: |close-to-close return| >= EVENT_DAY_RETURN_PCT
    OR true_range >= EVENT_DAY_TR_MULT * ATR_14.

    Decay: 1.0 + 0.75 * max((lookback - days_since_event) / lookback, 0).
    Most recent event wins (recency dominates BS-pricing impact).

    Returns 1.0 when bars too short to compute ATR or no qualifying event.
    """
    if len(bars) < ATR_WINDOW + 1:
        return 1.0
    # Compute trailing TRs (need prev_close)
    trs: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b.high - b.low)
            continue
        prev_close = bars[i - 1].close
        trs.append(max(
            b.high - b.low,
            abs(b.high - prev_close),
            abs(b.low - prev_close),
        ))
    atr_14 = sum(trs[-ATR_WINDOW:]) / ATR_WINDOW
    if atr_14 <= 0:
        atr_14 = float("inf")  # disable TR rule when baseline vol is zero

    # Scan the last `lookback` bars for events; track most recent (smallest age).
    most_recent_event_age: int | None = None
    for age in range(lookback):
        idx = -1 - age  # age=0 -> idx=-1 (most recent bar)
        if abs(idx) > len(bars):
            break
        if idx == -len(bars):
            continue  # no prev_close on the first bar
        b = bars[idx]
        prev_close = bars[idx - 1].close if idx - 1 >= -len(bars) else b.close
        ret = abs(b.close - prev_close) / prev_close if prev_close > 0 else 0.0
        tr = trs[idx]
        if ret >= EVENT_DAY_RETURN_PCT or tr >= EVENT_DAY_TR_MULT * atr_14:
            most_recent_event_age = age
            break  # we want the most recent — loop ascends from age=0

    if most_recent_event_age is None:
        return 1.0
    decay = max((lookback - most_recent_event_age) / lookback, 0.0)
    return 1.0 + (EVENT_DAY_BUMP_MULT - 1.0) * decay
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_synth_chain.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/backtest/__init__.py bullbot/v2/backtest/synth_chain.py tests/unit/test_v2_backtest_synth_chain.py
git commit -m "feat(v2/c4a): _event_day_iv_multiplier — Grok T1 F3 event-day IV bump with linear decay"
```

---

## Task 2: `_synth_iv` — combine `_iv_proxy` with event-day bump

**Files:**
- Modify: `bullbot/v2/backtest/synth_chain.py` (append `_synth_iv`)
- Modify: `tests/unit/test_v2_backtest_synth_chain.py` (append tests)

`_synth_iv` = `chains._iv_proxy(underlying_bars, vix_bars) × _event_day_iv_multiplier(underlying_bars)`. Same clamp as `_iv_proxy` (`[IV_PROXY_MIN, IV_PROXY_MAX]` from chains.py).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_backtest_synth_chain.py`:

```python
def _alternating_bars(n=60, base=100.0, pct=0.01):
    """n bars alternating ±pct%. Produces non-zero realized vol."""
    return [_bar(close=base * (1 + pct * ((-1) ** i)),
                 high=base * (1 + pct * ((-1) ** i)) + 0.5,
                 low=base * (1 + pct * ((-1) ** i)) - 0.5)
            for i in range(n)]


def test_synth_iv_returns_proxy_when_no_event_in_window():
    """Steady alternating bars + flat VIX → multiplier = 1.0,
    so _synth_iv equals chains._iv_proxy."""
    from bullbot.v2 import chains
    underlying = _alternating_bars()
    vix = [_bar(close=18.0) for _ in range(60)]
    proxy = chains._iv_proxy(underlying_bars=underlying, vix_bars=vix)
    synth = synth_chain._synth_iv(underlying_bars=underlying, vix_bars=vix)
    assert synth == pytest.approx(proxy, abs=0.001)


def test_synth_iv_inflates_when_recent_event_present():
    """Event on last bar → synth = proxy × 1.75 (subject to chains' [0.05, 3.0] clamp)."""
    from bullbot.v2 import chains
    underlying = _alternating_bars()
    underlying[-1] = _bar(close=120.0, high=121.0, low=118.0)  # ~20% spike
    vix = [_bar(close=18.0) for _ in range(60)]
    proxy = chains._iv_proxy(underlying_bars=underlying, vix_bars=vix)
    synth = synth_chain._synth_iv(underlying_bars=underlying, vix_bars=vix)
    expected = min(3.0, proxy * 1.75)
    assert synth == pytest.approx(expected, abs=0.01)


def test_synth_iv_clamps_to_iv_proxy_max():
    """Pathological inputs: proxy at ceiling (3.0) × 1.75 must still clamp to 3.0."""
    from bullbot.v2 import chains
    # Underlying with massive realized vol to push proxy near top of range
    underlying = [_bar(close=100.0 * (1 + 0.15 * ((-1) ** i))) for i in range(60)]
    vix_bars = [_bar(close=10.0)] * 59 + [_bar(close=80.0)]  # 8x regime spike
    underlying[-1] = _bar(close=130.0, high=132.0, low=125.0)  # event today too
    synth = synth_chain._synth_iv(underlying_bars=underlying, vix_bars=vix_bars)
    assert synth == chains.IV_PROXY_MAX  # 3.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_synth_chain.py -v`
Expected: FAIL on the 3 new tests with `AttributeError: module 'bullbot.v2.backtest.synth_chain' has no attribute '_synth_iv'`.

- [ ] **Step 3: Implement `_synth_iv`**

Append to `bullbot/v2/backtest/synth_chain.py`:

```python
from bullbot.v2.chains import _iv_proxy, IV_PROXY_MIN, IV_PROXY_MAX


def _synth_iv(*, underlying_bars: list, vix_bars: list) -> float:
    """Synthetic-chain IV = baseline proxy × event-day multiplier, clamped.

    Composes chains._iv_proxy (realized vol × VIX regime) with
    _event_day_iv_multiplier (Grok T1 F3). Both must be applied — the proxy
    captures regime, the bump captures the jump-day theta-crush spike that
    real chains see but proxies miss.
    """
    baseline = _iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    multiplier = _event_day_iv_multiplier(bars=underlying_bars)
    return max(IV_PROXY_MIN, min(IV_PROXY_MAX, baseline * multiplier))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_synth_chain.py -v`
Expected: PASS (10 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/backtest/synth_chain.py tests/unit/test_v2_backtest_synth_chain.py
git commit -m "feat(v2/c4a): _synth_iv combines _iv_proxy + event-day bump with clamp"
```

---

## Task 3: Strike + DTE filters (`_strikes_in_band`, `_dtes_in_band`)

**Files:**
- Modify: `bullbot/v2/backtest/synth_chain.py` (append filters)
- Modify: `tests/unit/test_v2_backtest_synth_chain.py` (append tests)

Per design §4.9 constraints to keep BS error bounded:
- Strikes restricted to ATM ±10% (`BACKTEST_STRIKE_BAND_PCT = 0.10` — wider than `chains.ATM_BAND_PCT = 0.05` used for IV-rank computation; both legal coexistent constants).
- Expiries restricted to 21-365 DTE.

Pure filters — no side effects.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_backtest_synth_chain.py`:

```python
def test_strikes_in_band_keeps_within_10pct_of_spot():
    spot = 100.0
    strikes = [85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0]
    out = synth_chain._strikes_in_band(strikes=strikes, spot=spot)
    # ±10% = [90, 110] inclusive
    assert out == [90.0, 95.0, 100.0, 105.0, 110.0]


def test_strikes_in_band_rejects_zero_or_negative_spot():
    assert synth_chain._strikes_in_band(strikes=[100.0], spot=0.0) == []
    assert synth_chain._strikes_in_band(strikes=[100.0], spot=-1.0) == []


def test_strikes_in_band_returns_empty_when_input_empty():
    assert synth_chain._strikes_in_band(strikes=[], spot=100.0) == []


def test_dtes_in_band_keeps_21_to_365():
    today = date(2026, 5, 17)
    expiries = [
        "2026-05-25",  # 8 DTE — too short
        "2026-06-19",  # 33 DTE — in band
        "2026-09-19",  # 125 DTE — in band
        "2027-05-21",  # 369 DTE — too long
    ]
    out = synth_chain._dtes_in_band(expiries=expiries, today=today)
    assert out == ["2026-06-19", "2026-09-19"]


def test_dtes_in_band_includes_boundary_values_inclusive():
    today = date(2026, 5, 17)
    # 21 DTE = today + 21 days = 2026-06-07
    # 365 DTE = today + 365 days = 2027-05-17
    expiries = ["2026-06-07", "2027-05-17"]
    out = synth_chain._dtes_in_band(expiries=expiries, today=today)
    assert out == ["2026-06-07", "2027-05-17"]


def test_dtes_in_band_handles_malformed_expiry_gracefully():
    today = date(2026, 5, 17)
    expiries = ["2026-06-19", "not-a-date", "2026-09-19"]
    out = synth_chain._dtes_in_band(expiries=expiries, today=today)
    assert out == ["2026-06-19", "2026-09-19"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_synth_chain.py -v`
Expected: FAIL on the 6 new tests.

- [ ] **Step 3: Implement filters**

Append to `bullbot/v2/backtest/synth_chain.py`:

```python
from datetime import date as _date

BACKTEST_STRIKE_BAND_PCT = 0.10
BACKTEST_MIN_DTE = 21
BACKTEST_MAX_DTE = 365


def _strikes_in_band(*, strikes: list[float], spot: float) -> list[float]:
    """Keep strikes within BACKTEST_STRIKE_BAND_PCT (10%) of spot.
    Returns empty list for non-positive spot."""
    if spot <= 0:
        return []
    lo = spot * (1 - BACKTEST_STRIKE_BAND_PCT)
    hi = spot * (1 + BACKTEST_STRIKE_BAND_PCT)
    return [s for s in strikes if lo <= s <= hi]


def _dtes_in_band(*, expiries: list[str], today: _date) -> list[str]:
    """Keep expiries whose DTE from today is in [21, 365]. Malformed
    expiry strings are silently dropped (synth chain skips them)."""
    out: list[str] = []
    for expiry in expiries:
        try:
            exp = _date.fromisoformat(expiry)
        except (TypeError, ValueError):
            continue
        dte = (exp - today).days
        if BACKTEST_MIN_DTE <= dte <= BACKTEST_MAX_DTE:
            out.append(expiry)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_synth_chain.py -v`
Expected: PASS (16 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/backtest/synth_chain.py tests/unit/test_v2_backtest_synth_chain.py
git commit -m "feat(v2/c4a): _strikes_in_band + _dtes_in_band filters (ATM ±10%, 21-365 DTE)"
```

---

## Task 4: `synthesize()` — main entry returning a `Chain`

**Files:**
- Modify: `bullbot/v2/backtest/synth_chain.py` (append `synthesize` + constants)
- Modify: `tests/unit/test_v2_backtest_synth_chain.py` (append integration tests)

`synthesize(ticker, asof_ts, today, spot, underlying_bars, vix_bars, expiries, strikes) -> Chain` orchestrates:
1. Filter strikes via `_strikes_in_band`.
2. Filter expiries via `_dtes_in_band`.
3. Compute one IV via `_synth_iv`.
4. For each (expiry, strike, kind) combination, BS-price via `bs_price`. Per-quote `bid` = `ask` = `last` = computed_price (BS is a single midpoint; no spread modeling in backtest).
5. Return `Chain(ticker, asof_ts, quotes=[ChainQuote(...)])` with each quote `source='bs'`.

Risk-free rate reused from `bullbot.v2.chains._RISK_FREE_RATE`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_backtest_synth_chain.py`:

```python
def test_synthesize_returns_chain_with_quotes_for_each_strike_x_expiry():
    """3 strikes × 2 expiries × 2 kinds (call + put) = 12 quotes."""
    underlying = _alternating_bars()
    vix = [_bar(close=18.0) for _ in range(60)]
    chain = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=underlying, vix_bars=vix,
        expiries=["2026-06-19", "2026-09-19"],
        strikes=[95.0, 100.0, 105.0],
    )
    assert chain.ticker == "AAPL"
    assert chain.asof_ts == 1_700_000_000
    assert len(chain.quotes) == 12  # 3 × 2 × 2


def test_synthesize_filters_strikes_outside_10pct_band():
    underlying = _alternating_bars()
    vix = [_bar(close=18.0) for _ in range(60)]
    chain = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=underlying, vix_bars=vix,
        expiries=["2026-06-19"],
        strikes=[80.0, 95.0, 100.0, 105.0, 120.0],  # 80 + 120 outside band
    )
    in_band_strikes = {q.strike for q in chain.quotes}
    assert in_band_strikes == {95.0, 100.0, 105.0}


def test_synthesize_filters_expiries_outside_21_365_dte():
    underlying = _alternating_bars()
    vix = [_bar(close=18.0) for _ in range(60)]
    chain = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=underlying, vix_bars=vix,
        expiries=["2026-05-25", "2026-06-19", "2027-09-19"],  # 8d, 33d, 489d
        strikes=[100.0],
    )
    in_band_expiries = {q.expiry for q in chain.quotes}
    assert in_band_expiries == {"2026-06-19"}


def test_synthesize_quotes_are_bs_priced_with_source_bs():
    """Each quote has bid=ask=last=BS_price and source='bs'."""
    underlying = _alternating_bars()
    vix = [_bar(close=18.0) for _ in range(60)]
    chain = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=underlying, vix_bars=vix,
        expiries=["2026-06-19"], strikes=[100.0],
    )
    for q in chain.quotes:
        assert q.source == "bs"
        assert q.bid == q.ask == q.last
        assert q.bid > 0  # ATM near-term option should have non-zero premium
        assert q.iv is not None


def test_synthesize_returns_empty_chain_when_all_strikes_filtered_out():
    underlying = _alternating_bars()
    vix = [_bar(close=18.0) for _ in range(60)]
    chain = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=underlying, vix_bars=vix,
        expiries=["2026-06-19"], strikes=[50.0, 200.0],  # both way outside band
    )
    assert chain.quotes == []


def test_synthesize_event_day_inflates_quote_iv_vs_steady_day():
    """Same setup with vs without event in the last 5 bars: IV should differ."""
    steady = _alternating_bars()
    spike = _alternating_bars()
    spike[-1] = _bar(close=120.0, high=121.0, low=119.0)
    vix = [_bar(close=18.0) for _ in range(60)]
    chain_steady = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=steady, vix_bars=vix,
        expiries=["2026-06-19"], strikes=[100.0],
    )
    chain_spike = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=spike, vix_bars=vix,
        expiries=["2026-06-19"], strikes=[100.0],
    )
    iv_steady = chain_steady.quotes[0].iv
    iv_spike = chain_spike.quotes[0].iv
    assert iv_spike > iv_steady * 1.5  # bump fired
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_synth_chain.py -v`
Expected: FAIL on the 6 new tests with `AttributeError: module 'bullbot.v2.backtest.synth_chain' has no attribute 'synthesize'`.

- [ ] **Step 3: Implement `synthesize`**

Append to `bullbot/v2/backtest/synth_chain.py`:

```python
from bullbot.data.synthetic_chain import bs_price
from bullbot.v2.chains import Chain, ChainQuote, _RISK_FREE_RATE


def synthesize(
    *,
    ticker: str,
    asof_ts: int,
    today: _date,
    spot: float,
    underlying_bars: list,
    vix_bars: list,
    expiries: list[str],
    strikes: list[float],
) -> Chain:
    """Produce a synthetic Chain for a backtest replay step.

    Filters input expiries/strikes to BS-pricable bands (21-365 DTE,
    ATM ±10%), computes one synthesized IV per (ticker, asof), then BS-prices
    every (expiry, strike, kind=call/put) combination. Each ChainQuote's
    bid=ask=last=BS_price and source='bs'.

    Empty chain (quotes=[]) is a valid return when filters strip everything.
    """
    in_band_strikes = _strikes_in_band(strikes=strikes, spot=spot)
    in_band_expiries = _dtes_in_band(expiries=expiries, today=today)
    if not in_band_strikes or not in_band_expiries:
        return Chain(ticker=ticker, asof_ts=asof_ts, quotes=[])

    iv = _synth_iv(underlying_bars=underlying_bars, vix_bars=vix_bars)

    quotes: list[ChainQuote] = []
    for expiry in in_band_expiries:
        exp_date = _date.fromisoformat(expiry)
        t_years = (exp_date - today).days / 365.0
        for strike in in_band_strikes:
            for kind in ("call", "put"):
                bs_kind = "C" if kind == "call" else "P"
                price = bs_price(
                    spot=spot, strike=strike, t_years=t_years,
                    vol=iv, r=_RISK_FREE_RATE, kind=bs_kind,
                )
                quotes.append(ChainQuote(
                    expiry=expiry, strike=strike, kind=kind,
                    bid=price, ask=price, last=price,
                    iv=iv, oi=None, source="bs",
                ))

    return Chain(ticker=ticker, asof_ts=asof_ts, quotes=quotes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_backtest_synth_chain.py -v`
Expected: PASS (22 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/backtest/synth_chain.py tests/unit/test_v2_backtest_synth_chain.py
git commit -m "feat(v2/c4a): synthesize() — BS-priced Chain with regime + event-day IV (Grok T1 F3)"
```

---

## Task 5: Full regression check

**Files:** none.

- [ ] **Step 1: Run full unit suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit -q`
Expected: 759 + 22 = 781 unit tests pass.

- [ ] **Step 2: Run integration suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration -q`
Expected: All 80 integration tests still pass.

- [ ] **Step 3: Static-import sanity check**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -c "from bullbot.v2.backtest import synth_chain; print(synth_chain.synthesize, synth_chain._synth_iv, synth_chain._event_day_iv_multiplier, synth_chain.EVENT_DAY_BUMP_MULT, synth_chain.BACKTEST_STRIKE_BAND_PCT, synth_chain.BACKTEST_MIN_DTE, synth_chain.BACKTEST_MAX_DTE)"`
Expected: prints all public + private symbols without ImportError.

- [ ] **Step 4: Optional marker commit**

```bash
git commit --allow-empty -m "chore(v2/c4a): Phase C.4a complete — synth_chain.py landed"
```

---

## Acceptance criteria

C.4a is complete when ALL of the following hold:

1. `bullbot/v2/backtest/__init__.py` exists (empty/package marker).
2. `bullbot/v2/backtest/synth_chain.py` exists and exports: `synthesize`, `_synth_iv`, `_event_day_iv_multiplier`, `_strikes_in_band`, `_dtes_in_band`, plus public constants `EVENT_DAY_RETURN_PCT`, `EVENT_DAY_TR_MULT`, `EVENT_DAY_BUMP_MULT`, `EVENT_DAY_DECAY_BARS`, `BACKTEST_STRIKE_BAND_PCT`, `BACKTEST_MIN_DTE`, `BACKTEST_MAX_DTE`.
3. `tests/unit/test_v2_backtest_synth_chain.py` has 22 tests, all passing.
4. Full unit + integration suite green (no regressions vs C.3c baseline of 759 unit + 80 integration).
5. Module < 250 LOC.
6. No new third-party dependencies.
7. No DB schema changes.
8. Event-day IV bump (Grok T1 F3) operates: most-recent-event recency rule, linear decay over 5 trading days, no firing on bars without events.

## What this unblocks

- **C.4b (`runner.py` + `report.py`):** the replay loop calls `synth_chain.synthesize(...)` once per simulated day to feed the vehicle agent. The synthesized Chain has the same shape as live Yahoo chains, so `vehicle.build_llm_context` works against either.

## Notes for the implementer

- **`bullbot.v2.chains._iv_proxy`** already exists and handles the realized-vol × VIX regime calc. Do NOT duplicate it here — just import and compose.
- **`bullbot.v2.chains._RISK_FREE_RATE`** is the rate to use (matches the rate used by live BS pricing in chains.py).
- **`bullbot.v2.chains.ATM_BAND_PCT = 0.05`** is the IV-rank computation band (narrower); **`BACKTEST_STRIKE_BAND_PCT = 0.10`** is the BS-pricing band (wider). Both legitimate; don't conflate.
- **Event-day bump is the most important piece** — without it, credit strategies look ~2x better in backtest than they should. The Grok review caught this in spec §4.9; this plan operationalizes the fix.
- **`bs_price` from synthetic_chain.py** uses kind `"C"|"P"` not `"call"|"put"`. Translate at call sites.
- **Worktree `.venv` path** is `/Users/danield.runion/Projects/bull-bot/.venv/bin/python`. Same as all prior phases.
