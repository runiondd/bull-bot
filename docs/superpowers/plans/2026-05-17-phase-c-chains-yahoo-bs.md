# Bull-Bot v2 Phase C.1 — Chains module (Yahoo + Black-Scholes) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Reviewed & revised — approved with changes (Grok 2026-05-17). Both Tier 1 findings (atomic-persistence transaction handling in Task 5, snapshot freshness policy in Task 6) are integrated below. Full review response: [`2026-05-17-phase-c-chains-yahoo-bs-grok-review-response.md`](2026-05-17-phase-c-chains-yahoo-bs-grok-review-response.md).

**Goal:** Ship `bullbot/v2/chains.py` — the live option-pricing layer for Phase C. Two public entry points: `fetch_chain(ticker, asof)` pulls a Yahoo chain (caching the rows into `v2_chain_snapshots`) and `price_leg(leg, spot, iv, today)` returns a per-leg mid-price tagged with its source (`'yahoo'` or `'bs'`). Black-Scholes fallback kicks in whenever Yahoo gives nothing usable. After this plan lands, both the forward daily mark-to-market loop (C.5) and the backtest harness (C.4) can ask the same module for prices and get back data with explicit source provenance.

**Architecture:** Thin wrapper around two existing primitives plus one new piece. (1) Yahoo chains via the lazy-imported `yfinance.Ticker(ticker).option_chain(expiry)` pattern already used in `bullbot/data/daily_refresh.py` and `bullbot/data/fetchers.py`. (2) Black-Scholes via the existing `bullbot.data.synthetic_chain.bs_price` and `realized_vol` helpers — no duplicate pricer. (3) New: an IV proxy that scales realized-vol by a VIX-regime multiplier (`current_vix / median(VIX_60d)`) when Yahoo gives no IV. Persistence writes one row per (ticker, asof_ts, expiry, strike, kind) into `v2_chain_snapshots` — the table was already created by C.0 Task 1, so no schema migration is needed in this plan. The Yahoo client is injected as an optional callable parameter (same pattern `daily_refresh.py` uses) so tests don't need real network calls.

**Tech Stack:** Python 3.11+, SQLite via stdlib `sqlite3`, `yfinance` (already a project dependency), `dataclasses`, `pytest`, `unittest.mock.patch` / `monkeypatch` for mocking the yfinance import. Reuses `bullbot.data.synthetic_chain.bs_price` and `bullbot.data.synthetic_chain.realized_vol`. Reuses the `bars` SQLite table (read-only) for the underlying close history that feeds the IV proxy.

**Spec reference:** [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md`](../specs/2026-05-16-phase-c-vehicle-agent-design.md) sections 4.3 (schema for `v2_chain_snapshots`, already migrated in C.0), 4.8 (chains module — primary spec), and 4.9 (backtest harness, only the parts that describe what chains needs to expose to downstream code). [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md`](../specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md) — Tier 1 Finding 3 (event-day IV bump) lives entirely in `backtest/synth_chain.py` (C.4), NOT in this module; the forward `chains.py` uses the raw realized-vol × VIX-regime proxy when Yahoo IV is unavailable.

---

## Pre-flight assumptions verified before writing tasks

- **`v2_chain_snapshots` schema already exists** (C.0 Task 1, commit `44f79a9`). Columns: `ticker, asof_ts, expiry, strike, kind, bid, ask, last, iv, oi, source` with PK `(ticker, asof_ts, expiry, strike, kind)`. C.1 only writes rows, no migration.
- **BS pricer exists** at `bullbot/data/synthetic_chain.bs_price(spot, strike, t_years, vol, r, kind)` with `kind in {"C", "P"}`. C.1 wraps it; do not re-implement.
- **Realized-vol helper exists** at `bullbot/data/synthetic_chain.realized_vol(bars, window=30) -> float`. Returns annualized vol from log returns; falls back to 0.30 on insufficient bars.
- **VIX bars are stored in the `bars` table** under `ticker='VIX', timeframe='1d'` (see `bullbot/scheduler.py:44` for the existing read pattern).
- **Underlying bars loader pattern** is in `bullbot/v2/runner.py:_load_bars`; this plan introduces a similar private helper inside `chains.py` rather than depending on `runner._load_bars` (private symbol).
- **Yahoo-fetcher injection pattern** is in `bullbot/data/daily_refresh.py:36` — the lazy `import yfinance` lives inside a default-`fetcher` function so production callers omit it and tests pass a stub. C.1 follows the same pattern.
- **risk-free rate** for the BS pricer — match what `bullbot/data/synthetic_chain.generate_synthetic_chain` already uses; if it pulls from `bullbot.config`, do the same in `chains.py`. (Task 3 step 1 verifies this and matches the same constant.)

---

## File Structure

| Path | Responsibility | Status |
|---|---|---|
| `bullbot/v2/chains.py` | `ChainQuote` + `Chain` dataclasses, `fetch_chain`, `price_leg`, private IV-proxy + BS-pricing helpers. | **Create** |
| `tests/unit/test_v2_chains.py` | Unit tests for the IV proxy, BS pricing path, Yahoo parsing, persistence, failure modes, and the `price_leg` dispatcher. | **Create** |
| `tests/integration/test_v2_chains_end_to_end.py` | Integration test that wires `fetch_chain` → `v2_chain_snapshots` → `price_leg` and confirms Yahoo cache + BS fallback both work in one call sequence. | **Create** |
| `bullbot/v2/positions.py` | Unchanged. (`price_leg` accepts an `OptionLeg`; no leg-schema changes needed.) | — |
| `bullbot/v2/risk.py` | Unchanged. (Sizing math is fully decoupled from price source.) | — |
| `bullbot/db/migrations.py` | Unchanged. (Schema landed in C.0.) | — |

Module size target for `chains.py`: < 250 LOC, single-responsibility (live + historical price provision). If it grows beyond that during implementation, that's a signal to split before adding the C.5 dashboard or C.4 backtest dependencies — defer the split decision to whoever shows up with the next dependency.

---

## Task 1: Module skeleton + `ChainQuote` + `Chain` dataclasses

**Files:**
- Create: `bullbot/v2/chains.py`
- Create: `tests/unit/test_v2_chains.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_v2_chains.py`:

```python
"""Unit tests for bullbot.v2.chains — Yahoo + BS pricing layer."""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.v2 import chains
from bullbot.v2.positions import OptionLeg


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def test_chainquote_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be one of"):
        chains.ChainQuote(
            expiry="2026-06-19", strike=100.0, kind="future",
            bid=1.0, ask=1.2, last=1.1, iv=0.30, oi=100, source="yahoo",
        )


def test_chainquote_rejects_unknown_source():
    with pytest.raises(ValueError, match="source must be one of"):
        chains.ChainQuote(
            expiry="2026-06-19", strike=100.0, kind="call",
            bid=1.0, ask=1.2, last=1.1, iv=0.30, oi=100, source="polygon",
        )


def test_chainquote_mid_price_returns_bid_ask_midpoint():
    q = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=1.00, ask=1.20, last=None, iv=0.30, oi=100, source="yahoo",
    )
    assert q.mid_price() == pytest.approx(1.10)


def test_chainquote_mid_price_falls_back_to_last_when_bid_or_ask_missing():
    q = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=None, ask=None, last=1.15, iv=0.30, oi=100, source="yahoo",
    )
    assert q.mid_price() == 1.15


def test_chainquote_mid_price_returns_none_when_no_prices_available():
    q = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=None, ask=None, last=None, iv=0.30, oi=100, source="yahoo",
    )
    assert q.mid_price() is None


def test_chain_empty_quotes_is_valid():
    c = chains.Chain(ticker="AAPL", asof_ts=1_700_000_000, quotes=[])
    assert c.ticker == "AAPL"
    assert c.quotes == []


def test_chain_find_quote_returns_matching_strike_and_kind():
    q1 = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=1.0, ask=1.2, last=1.1, iv=0.30, oi=100, source="yahoo",
    )
    q2 = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="put",
        bid=0.8, ask=1.0, last=0.9, iv=0.32, oi=80, source="yahoo",
    )
    c = chains.Chain(ticker="AAPL", asof_ts=1_700_000_000, quotes=[q1, q2])
    assert c.find_quote(expiry="2026-06-19", strike=100.0, kind="call") is q1
    assert c.find_quote(expiry="2026-06-19", strike=100.0, kind="put") is q2
    assert c.find_quote(expiry="2026-06-19", strike=105.0, kind="call") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_chains.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bullbot.v2.chains'`.

- [ ] **Step 3: Implement the skeleton**

Create `bullbot/v2/chains.py`:

```python
"""Live option-chain integration for v2 Phase C.

Two public entry points:
- fetch_chain(ticker, asof, client=None): pull a Yahoo chain, cache rows
  into v2_chain_snapshots, return a Chain.
- price_leg(leg, spot, iv, today, conn=None): return (mid_price, source)
  for a single OptionLeg, trying Yahoo (cached snapshot or fresh fetch)
  before falling back to Black-Scholes.

BS math is reused from bullbot.data.synthetic_chain — do not re-implement.
"""
from __future__ import annotations

from dataclasses import dataclass

VALID_KINDS = ("call", "put")
VALID_SOURCES = ("yahoo", "bs")


@dataclass
class ChainQuote:
    """A single (expiry, strike, kind) quote with both market and model fields."""

    expiry: str            # 'YYYY-MM-DD'
    strike: float
    kind: str              # 'call' | 'put'
    bid: float | None
    ask: float | None
    last: float | None
    iv: float | None
    oi: int | None
    source: str            # 'yahoo' | 'bs'

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}; got {self.kind!r}")
        if self.source not in VALID_SOURCES:
            raise ValueError(f"source must be one of {VALID_SOURCES}; got {self.source!r}")

    def mid_price(self) -> float | None:
        """Bid-ask midpoint, or last price if either bid or ask is missing,
        or None if no prices are available."""
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        if self.last is not None:
            return self.last
        return None


@dataclass
class Chain:
    """A collection of ChainQuotes for one (ticker, asof_ts)."""

    ticker: str
    asof_ts: int
    quotes: list[ChainQuote]

    def find_quote(self, *, expiry: str, strike: float, kind: str) -> ChainQuote | None:
        """Linear lookup. Chains are O(few hundred) entries in practice, so
        a hash index would be premature optimization."""
        for q in self.quotes:
            if q.expiry == expiry and q.strike == strike and q.kind == kind:
                return q
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_chains.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/chains.py tests/unit/test_v2_chains.py
git commit -m "feat(v2/c1): ChainQuote + Chain dataclasses for v2 chains module"
```

---

## Task 2: `_iv_proxy` — realized vol × VIX regime multiplier

**Files:**
- Modify: `bullbot/v2/chains.py` (append helpers + private `_iv_proxy`)
- Modify: `tests/unit/test_v2_chains.py` (append IV-proxy tests)

The forward-mode IV proxy is used whenever Yahoo gives no IV for a leg's strike. It computes annualized realized-vol over the trailing 30 daily bars of the underlying and multiplies by `current_vix_close / median(vix_close_last_60d)`. The regime multiplier crudely captures "is volatility currently elevated vs its 60-day baseline?" without any options data. Returns 0.30 (the same default the existing `realized_vol` helper uses) if there aren't enough bars to compute anything sensible.

The IV proxy is bounded `[0.05, 3.0]` to prevent pathological inputs (a single-day VIX spike during a flash crash) from producing infinite option prices.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_chains.py`:

```python
from types import SimpleNamespace


def _bars(closes: list[float]) -> list[SimpleNamespace]:
    """Build a list of bar-shaped namespaces from a sequence of closes.
    Mirrors the shape that bullbot.v2.runner._load_bars produces."""
    return [
        SimpleNamespace(ts=1_700_000_000 + i * 86400,
                        open=c, high=c, low=c, close=c, volume=1_000_000.0)
        for i, c in enumerate(closes)
    ]


def test_iv_proxy_returns_realized_vol_when_regime_multiplier_is_one():
    """VIX flat at its 60-day median → multiplier = 1.0, IV proxy ≈ realized vol."""
    underlying_bars = _bars([100.0 * (1 + 0.01 * ((-1) ** i)) for i in range(60)])
    vix_bars = _bars([18.0] * 60)
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    # underlying alternates ±1% so realized vol ~ 16% annualized; should land
    # near that, well above the 0.05 floor.
    assert 0.05 < iv < 0.50


def test_iv_proxy_scales_up_when_vix_above_baseline():
    """Today's VIX = 30, 60-day median VIX = 15 → multiplier = 2.0,
    IV proxy = realized_vol * 2.0 (subject to the [0.05, 3.0] clamp)."""
    underlying_bars = _bars([100.0] * 60)  # zero realized vol triggers the 0.30 floor
    vix_bars = _bars([15.0] * 59 + [30.0])
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    # 0.30 floor (from realized_vol) × 2.0 regime = 0.60
    assert iv == pytest.approx(0.60, abs=0.02)


def test_iv_proxy_scales_down_when_vix_below_baseline():
    underlying_bars = _bars([100.0] * 60)
    vix_bars = _bars([20.0] * 59 + [10.0])
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    # 0.30 × 0.5 = 0.15
    assert iv == pytest.approx(0.15, abs=0.02)


def test_iv_proxy_clamps_to_upper_bound_on_pathological_vix_spike():
    underlying_bars = _bars([100.0] * 60)
    vix_bars = _bars([10.0] * 59 + [200.0])  # 20× spike (impossible but test the clamp)
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    assert iv == 3.0


def test_iv_proxy_falls_back_to_default_when_underlying_bars_too_few():
    underlying_bars = _bars([100.0] * 5)  # < 31 bars → realized_vol returns its 0.30 default
    vix_bars = _bars([18.0] * 60)
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    assert iv == pytest.approx(0.30, abs=0.01)


def test_iv_proxy_falls_back_to_default_when_vix_bars_too_few():
    underlying_bars = _bars([100.0 + i * 0.5 for i in range(60)])
    vix_bars = _bars([18.0] * 5)  # < 60 → can't compute regime multiplier
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    # Multiplier defaults to 1.0; result is the realized vol of the steady drift.
    assert 0.05 < iv < 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_chains.py -v`
Expected: FAIL on the 6 new tests — `AttributeError: module 'bullbot.v2.chains' has no attribute '_iv_proxy'`.

- [ ] **Step 3: Implement `_iv_proxy`**

Append to `bullbot/v2/chains.py`:

```python
from statistics import median

from bullbot.data.synthetic_chain import realized_vol

IV_PROXY_MIN = 0.05   # 5% — floor; lower than this and BS produces nonsense
IV_PROXY_MAX = 3.00   # 300% — ceiling; higher than this almost always means bad inputs


def _iv_proxy(*, underlying_bars: list, vix_bars: list) -> float:
    """Annualized IV estimate when Yahoo gives no IV for a strike.

    Formula:  realized_vol_30(underlying) * (vix_today / median(vix_last_60))
    Bounded to [IV_PROXY_MIN, IV_PROXY_MAX].

    Bars expected to be ordered oldest-first with a `.close` attribute (matches
    the shape that bullbot.v2.runner._load_bars and the bars table both produce).
    Falls back gracefully when either series is too short for its respective
    sub-computation:
        - underlying < 31 bars → realized_vol returns its 0.30 default
        - vix < 60 bars        → regime multiplier defaults to 1.0
    """
    rv = realized_vol(underlying_bars, window=30)
    if len(vix_bars) < 60:
        multiplier = 1.0
    else:
        vix_today = vix_bars[-1].close
        vix_baseline = median(b.close for b in vix_bars[-60:])
        multiplier = vix_today / vix_baseline if vix_baseline > 0 else 1.0
    iv = rv * multiplier
    return max(IV_PROXY_MIN, min(IV_PROXY_MAX, iv))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_chains.py -v`
Expected: PASS (13 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/chains.py tests/unit/test_v2_chains.py
git commit -m "feat(v2/c1): _iv_proxy — realized-vol x VIX-regime multiplier with [0.05, 3.0] clamp"
```

---

## Task 3: `_price_leg_bs` — Black-Scholes fallback for one leg

**Files:**
- Modify: `bullbot/v2/chains.py` (append `_price_leg_bs`)
- Modify: `tests/unit/test_v2_chains.py` (append BS pricing tests)

Wraps `bullbot.data.synthetic_chain.bs_price` to translate from `OptionLeg` semantics (string kind `'call'|'put'`, string expiry `'YYYY-MM-DD'`, `qty` × 100 contract multiplier) into the BS signature (`kind='C'|'P'`, `t_years` as a float, per-share price). Returns the per-share mid-price (not the dollar value of the position) so the caller multiplies by `qty * 100` itself — matches the convention `bs_price` returns and is consistent with how `risk.py` works in per-share dollar units.

The risk-free rate `r` is sourced from `bullbot.config` (matches existing `bullbot.data.synthetic_chain.generate_synthetic_chain` behavior); the constant is `0.045` (4.5% — the same flat assumption v1 used). Verify in step 0 below before writing the test.

- [ ] **Step 0: Verify the risk-free-rate constant**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -c "from bullbot import config; print(getattr(config, 'RISK_FREE_RATE', 'NOT_SET'))"`
Expected: prints a numeric like `0.045`. If it prints `NOT_SET`, instead use `r=0.045` directly as a module-level `_RISK_FREE_RATE = 0.045` constant inside `chains.py` and adjust the tests below accordingly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_chains.py`:

```python
def _call_leg(strike: float, expiry: str = "2026-12-18", qty: int = 1) -> OptionLeg:
    return OptionLeg(
        action="buy", kind="call", strike=strike,
        expiry=expiry, qty=qty, entry_price=0.0,
    )


def _put_leg(strike: float, expiry: str = "2026-12-18", qty: int = 1) -> OptionLeg:
    return OptionLeg(
        action="buy", kind="put", strike=strike,
        expiry=expiry, qty=qty, entry_price=0.0,
    )


def test_price_leg_bs_atm_call_with_30pct_iv_and_one_year_dte():
    """ATM call, S=K=100, T=1yr, IV=0.30, r=0.045
    -> textbook BS price ≈ 13.84 (per share)."""
    leg = _call_leg(strike=100.0, expiry="2027-05-17")
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=100.0, iv=0.30, today=today)
    assert price == pytest.approx(13.84, abs=0.10)


def test_price_leg_bs_atm_put_with_30pct_iv_and_one_year_dte():
    """ATM put, S=K=100, T=1yr, IV=0.30, r=0.045
    -> textbook BS price ≈ 9.45 (per share, via put-call parity)."""
    leg = _put_leg(strike=100.0, expiry="2027-05-17")
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=100.0, iv=0.30, today=today)
    assert price == pytest.approx(9.45, abs=0.10)


def test_price_leg_bs_itm_call_intrinsic_floor_on_expiry_day():
    """Call deep ITM on expiry day: BS returns max(spot - strike, 0)."""
    leg = _call_leg(strike=90.0, expiry="2026-05-17")
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=100.0, iv=0.30, today=today)
    assert price == pytest.approx(10.0)


def test_price_leg_bs_otm_call_intrinsic_floor_on_expiry_day():
    leg = _call_leg(strike=110.0, expiry="2026-05-17")
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=100.0, iv=0.30, today=today)
    assert price == 0.0


def test_price_leg_bs_otm_put_intrinsic_floor_on_expiry_day():
    leg = _put_leg(strike=90.0, expiry="2026-05-17")
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=100.0, iv=0.30, today=today)
    assert price == 0.0


def test_price_leg_bs_share_leg_returns_spot():
    """Share legs have no time value, no strike — BS doesn't apply.
    The helper returns spot so the caller can sum leg values uniformly."""
    leg = OptionLeg(
        action="buy", kind="share", strike=None, expiry=None,
        qty=100, entry_price=100.0,
    )
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=99.50, iv=0.30, today=today)
    assert price == 99.50


def test_price_leg_bs_negative_dte_returns_intrinsic():
    """Expiry already passed — BS returns intrinsic value."""
    leg = _call_leg(strike=100.0, expiry="2026-04-01")
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=105.0, iv=0.30, today=today)
    assert price == pytest.approx(5.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_chains.py -v`
Expected: FAIL on the 7 new tests — `AttributeError: module 'bullbot.v2.chains' has no attribute '_price_leg_bs'`.

- [ ] **Step 3: Implement `_price_leg_bs`**

If step 0 confirmed `config.RISK_FREE_RATE` exists, import it. Otherwise add `_RISK_FREE_RATE = 0.045` as a module-level constant. Append to `bullbot/v2/chains.py`:

```python
from datetime import date

from bullbot.data.synthetic_chain import bs_price
from bullbot.v2.positions import OptionLeg

try:
    from bullbot.config import RISK_FREE_RATE as _RISK_FREE_RATE  # type: ignore[attr-defined]
except (ImportError, AttributeError):
    _RISK_FREE_RATE = 0.045  # 4.5% — matches v1 synthetic chain default


def _price_leg_bs(
    *,
    leg: OptionLeg,
    spot: float,
    iv: float,
    today: date,
) -> float:
    """Per-share Black-Scholes price for one OptionLeg.

    Returns spot for share legs (no time value), max(intrinsic, 0) for expired
    options, and the standard BS formula otherwise. The returned value is in
    per-share dollars — callers multiply by qty * 100 (for option legs) or
    qty (for share legs) to get position-level dollar value.
    """
    if leg.kind == "share":
        return spot
    expiry_date = date.fromisoformat(leg.expiry)
    t_years = max(0.0, (expiry_date - today).days / 365.0)
    bs_kind = "C" if leg.kind == "call" else "P"
    return bs_price(
        spot=spot, strike=leg.strike, t_years=t_years,
        vol=iv, r=_RISK_FREE_RATE, kind=bs_kind,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_chains.py -v`
Expected: PASS (20 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/chains.py tests/unit/test_v2_chains.py
git commit -m "feat(v2/c1): _price_leg_bs wraps synthetic_chain.bs_price for OptionLeg inputs"
```

---

## Task 4: `fetch_chain` — Yahoo fetch, parse, and persist to `v2_chain_snapshots`

**Files:**
- Modify: `bullbot/v2/chains.py` (append `fetch_chain` + Yahoo client default)
- Modify: `tests/unit/test_v2_chains.py` (append fetch + persistence tests)

Yahoo's `Ticker.option_chain(expiry)` returns a `(calls_df, puts_df)` tuple of pandas DataFrames with columns `strike, bid, ask, lastPrice, impliedVolatility, openInterest`. We iterate every expiry returned by `Ticker.options` (which lists all expiry strings as `'YYYY-MM-DD'`), build one `ChainQuote` per row across both calls and puts, persist each to `v2_chain_snapshots`, and return the assembled `Chain`.

The Yahoo client is injected as a `client` keyword argument that defaults to a `_default_yf_client()` factory function. Production callers omit it; tests pass a stub that returns a hand-built object with the right shape. This mirrors `bullbot/data/daily_refresh.py`'s injection pattern, so tests do not need a real `yfinance` install.

Persistence uses `INSERT OR REPLACE` keyed on the `v2_chain_snapshots` PK `(ticker, asof_ts, expiry, strike, kind)` — so re-fetching the same chain on the same `asof_ts` is idempotent (overwrites the prior row with fresh values).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_chains.py`:

```python
import pandas as pd


class _FakeYFTicker:
    """Mimics yfinance.Ticker minimally — just the two surface attributes
    fetch_chain uses: .options (list[str] expiries) and .option_chain(expiry)
    (returns a namespace with .calls / .puts DataFrames)."""

    def __init__(self, options_by_expiry: dict[str, tuple]):
        # options_by_expiry: {"2026-06-19": (calls_df, puts_df), ...}
        self._chains = options_by_expiry
        self.options = list(options_by_expiry.keys())

    def option_chain(self, expiry: str):
        calls_df, puts_df = self._chains[expiry]
        return SimpleNamespace(calls=calls_df, puts=puts_df)


def _make_calls_df():
    return pd.DataFrame([
        {"strike": 95.0, "bid": 6.10, "ask": 6.30, "lastPrice": 6.20,
         "impliedVolatility": 0.32, "openInterest": 420},
        {"strike": 100.0, "bid": 3.20, "ask": 3.40, "lastPrice": 3.30,
         "impliedVolatility": 0.30, "openInterest": 1850},
        {"strike": 105.0, "bid": 1.40, "ask": 1.55, "lastPrice": 1.47,
         "impliedVolatility": 0.29, "openInterest": 730},
    ])


def _make_puts_df():
    return pd.DataFrame([
        {"strike": 95.0, "bid": 0.80, "ask": 0.95, "lastPrice": 0.87,
         "impliedVolatility": 0.34, "openInterest": 510},
        {"strike": 100.0, "bid": 2.60, "ask": 2.80, "lastPrice": 2.70,
         "impliedVolatility": 0.31, "openInterest": 1240},
    ])


def test_fetch_chain_parses_yahoo_response_into_chain_quotes(conn):
    fake_ticker = _FakeYFTicker({
        "2026-06-19": (_make_calls_df(), _make_puts_df()),
    })
    fake_client = lambda symbol: fake_ticker  # noqa: E731

    result = chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000, client=fake_client,
    )
    assert result is not None
    assert result.ticker == "AAPL"
    assert result.asof_ts == 1_700_000_000
    # 3 calls + 2 puts = 5 quotes
    assert len(result.quotes) == 5

    call_at_100 = result.find_quote(expiry="2026-06-19", strike=100.0, kind="call")
    assert call_at_100 is not None
    assert call_at_100.bid == 3.20
    assert call_at_100.ask == 3.40
    assert call_at_100.last == 3.30
    assert call_at_100.iv == pytest.approx(0.30)
    assert call_at_100.oi == 1850
    assert call_at_100.source == "yahoo"


def test_fetch_chain_persists_quotes_to_v2_chain_snapshots(conn):
    fake_ticker = _FakeYFTicker({
        "2026-06-19": (_make_calls_df(), _make_puts_df()),
    })
    chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        client=lambda symbol: fake_ticker,
    )
    rows = conn.execute(
        "SELECT * FROM v2_chain_snapshots WHERE ticker='AAPL' ORDER BY expiry, strike, kind"
    ).fetchall()
    assert len(rows) == 5
    call_rows = [r for r in rows if r["kind"] == "call"]
    assert {r["strike"] for r in call_rows} == {95.0, 100.0, 105.0}
    assert all(r["source"] == "yahoo" for r in rows)


def test_fetch_chain_multi_expiry_returns_all_quotes(conn):
    fake_ticker = _FakeYFTicker({
        "2026-06-19": (_make_calls_df(), _make_puts_df()),
        "2026-07-17": (_make_calls_df(), _make_puts_df()),
    })
    result = chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        client=lambda symbol: fake_ticker,
    )
    assert len(result.quotes) == 10
    expiries = {q.expiry for q in result.quotes}
    assert expiries == {"2026-06-19", "2026-07-17"}


def test_fetch_chain_idempotent_on_re_fetch_same_asof(conn):
    """Re-fetching same (ticker, asof) overwrites prior rows — does not
    accumulate duplicates."""
    fake_ticker = _FakeYFTicker({
        "2026-06-19": (_make_calls_df(), _make_puts_df()),
    })
    chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        client=lambda symbol: fake_ticker,
    )
    chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        client=lambda symbol: fake_ticker,
    )
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM v2_chain_snapshots WHERE ticker='AAPL'"
    ).fetchone()["n"]
    assert n == 5


def test_fetch_chain_handles_nan_iv_and_zero_oi_as_none(conn):
    """yfinance sometimes returns NaN for impliedVolatility and 0 for
    openInterest on illiquid strikes. NaN → None, 0 OI → 0 (not None)."""
    calls = pd.DataFrame([
        {"strike": 100.0, "bid": 1.0, "ask": 1.2, "lastPrice": 1.1,
         "impliedVolatility": float("nan"), "openInterest": 0},
    ])
    puts = pd.DataFrame([])
    fake_ticker = _FakeYFTicker({"2026-06-19": (calls, puts)})
    result = chains.fetch_chain(
        conn=conn, ticker="XYZ", asof_ts=1_700_000_000,
        client=lambda symbol: fake_ticker,
    )
    q = result.find_quote(expiry="2026-06-19", strike=100.0, kind="call")
    assert q.iv is None
    assert q.oi == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_chains.py -v`
Expected: FAIL on the 5 new tests — `AttributeError: module 'bullbot.v2.chains' has no attribute 'fetch_chain'`.

- [ ] **Step 3: Implement `fetch_chain`**

Append to `bullbot/v2/chains.py`:

```python
import math
import sqlite3
from typing import Callable


def _default_yf_client():
    """Lazy yfinance import — keeps tests independent of yfinance availability.
    Mirrors the pattern at bullbot/data/daily_refresh.py:36."""
    import yfinance as yf
    return lambda symbol: yf.Ticker(symbol)


def _nan_to_none(x):
    """yfinance frequently returns NaN for impliedVolatility on illiquid
    strikes. Map NaN → None so downstream consumers can branch cleanly."""
    if x is None:
        return None
    try:
        if isinstance(x, float) and math.isnan(x):
            return None
    except (TypeError, ValueError):
        return None
    return x


def _row_to_quote(row, *, expiry: str, kind: str) -> ChainQuote:
    """Convert one yfinance DataFrame row to a ChainQuote.

    yfinance column names: strike, bid, ask, lastPrice, impliedVolatility, openInterest.
    """
    return ChainQuote(
        expiry=expiry,
        strike=float(row["strike"]),
        kind=kind,
        bid=_nan_to_none(row.get("bid")),
        ask=_nan_to_none(row.get("ask")),
        last=_nan_to_none(row.get("lastPrice")),
        iv=_nan_to_none(row.get("impliedVolatility")),
        oi=int(row["openInterest"]) if row.get("openInterest") is not None else None,
        source="yahoo",
    )


def _persist_quote(conn: sqlite3.Connection, *, ticker: str, asof_ts: int, quote: ChainQuote) -> None:
    """INSERT OR REPLACE keyed on the PK (ticker, asof_ts, expiry, strike, kind).
    Idempotent — re-fetching the same chain overwrites prior values."""
    conn.execute(
        "INSERT OR REPLACE INTO v2_chain_snapshots "
        "(ticker, asof_ts, expiry, strike, kind, bid, ask, last, iv, oi, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticker, asof_ts, quote.expiry, quote.strike, quote.kind,
            quote.bid, quote.ask, quote.last, quote.iv, quote.oi, quote.source,
        ),
    )


def fetch_chain(
    *,
    conn: sqlite3.Connection,
    ticker: str,
    asof_ts: int,
    client: Callable[[str], object] | None = None,
) -> Chain | None:
    """Pull a full Yahoo option chain for `ticker`, persist into
    v2_chain_snapshots, and return the assembled Chain.

    Returns None if the Yahoo fetch fails or returns no expiries (those
    failure modes are tested in Task 5).

    `client` is a callable `(symbol) -> Ticker-like object` injected for
    testing; defaults to a lazy yfinance.Ticker factory.
    """
    if client is None:
        client = _default_yf_client()

    ticker_obj = client(ticker)
    expiries: list[str] = list(ticker_obj.options)

    quotes: list[ChainQuote] = []
    for expiry in expiries:
        chain_pair = ticker_obj.option_chain(expiry)
        for _, row in chain_pair.calls.iterrows():
            q = _row_to_quote(row, expiry=expiry, kind="call")
            quotes.append(q)
            _persist_quote(conn, ticker=ticker, asof_ts=asof_ts, quote=q)
        for _, row in chain_pair.puts.iterrows():
            q = _row_to_quote(row, expiry=expiry, kind="put")
            quotes.append(q)
            _persist_quote(conn, ticker=ticker, asof_ts=asof_ts, quote=q)

    conn.commit()
    return Chain(ticker=ticker, asof_ts=asof_ts, quotes=quotes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_chains.py -v`
Expected: PASS (25 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/chains.py tests/unit/test_v2_chains.py
git commit -m "feat(v2/c1): fetch_chain — Yahoo parse + persist to v2_chain_snapshots (mocked client injection)"
```

---

## Task 5: `fetch_chain` graceful failure modes

**Files:**
- Modify: `bullbot/v2/chains.py` (wrap Yahoo calls in failure handling)
- Modify: `tests/unit/test_v2_chains.py` (append failure tests)

Yahoo flakes regularly: network timeouts, delisted tickers, illiquid names with no chains, transient 5xx errors. The contract is: `fetch_chain` returns `None` on any of these failures and logs a structured warning. Persistence does not happen on failure (no half-written chain rows). Tests use the same `_FakeYFTicker` injection — substituting a stub that raises specific exceptions.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_chains.py`:

```python
def test_fetch_chain_returns_none_when_ticker_has_no_options(conn):
    """yfinance Ticker.options returns [] for tickers with no options chain."""
    fake_ticker = _FakeYFTicker({})  # no expiries
    result = chains.fetch_chain(
        conn=conn, ticker="XYZ", asof_ts=1_700_000_000,
        client=lambda symbol: fake_ticker,
    )
    assert result is None
    # No rows persisted
    n = conn.execute("SELECT COUNT(*) AS n FROM v2_chain_snapshots").fetchone()["n"]
    assert n == 0


def test_fetch_chain_returns_none_when_yfinance_raises_on_construct(conn):
    """Network timeout or 5xx during yfinance.Ticker(symbol) — returns None
    and does not persist anything."""
    def raising_client(symbol):
        raise ConnectionError("simulated yahoo timeout")
    result = chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000, client=raising_client,
    )
    assert result is None
    n = conn.execute("SELECT COUNT(*) AS n FROM v2_chain_snapshots").fetchone()["n"]
    assert n == 0


def test_fetch_chain_returns_none_when_option_chain_call_raises(conn):
    """yfinance occasionally returns expiries but then raises on the
    follow-up option_chain(expiry) call. Same outcome: None, no persist."""

    class RaisingTicker:
        options = ["2026-06-19"]
        def option_chain(self, expiry):
            raise ValueError("simulated yahoo chain parse error")

    result = chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        client=lambda symbol: RaisingTicker(),
    )
    assert result is None
    n = conn.execute("SELECT COUNT(*) AS n FROM v2_chain_snapshots").fetchone()["n"]
    assert n == 0


def test_fetch_chain_partial_failure_persists_nothing(conn):
    """If the first expiry succeeds but the second raises, the entire fetch
    is treated as failed — no half-written chain in the DB."""

    class PartiallyFailingTicker:
        options = ["2026-06-19", "2026-07-17"]
        def option_chain(self, expiry):
            if expiry == "2026-06-19":
                return SimpleNamespace(calls=_make_calls_df(), puts=_make_puts_df())
            raise RuntimeError("simulated mid-fetch error")

    result = chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        client=lambda symbol: PartiallyFailingTicker(),
    )
    assert result is None
    # First expiry rows must NOT remain in DB after the second one fails.
    n = conn.execute("SELECT COUNT(*) AS n FROM v2_chain_snapshots").fetchone()["n"]
    assert n == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_chains.py -v`
Expected: All 4 new tests fail. The `no options` case fails because the Task 4 implementation returns `Chain(quotes=[])` instead of `None`; the other three fail with unhandled exceptions propagating out of the unguarded Yahoo calls.

- [ ] **Step 3: Add failure handling + transaction wrap**

Replace the `fetch_chain` function in `bullbot/v2/chains.py` with this version (the change is: wrap the entire Yahoo call sequence in a try/except, accumulate quotes in memory first then bulk-persist in a transaction, return `None` on any exception or empty expiries):

```python
import logging

_log = logging.getLogger(__name__)


def fetch_chain(
    *,
    conn: sqlite3.Connection,
    ticker: str,
    asof_ts: int,
    client: Callable[[str], object] | None = None,
) -> Chain | None:
    """Pull a full Yahoo option chain for `ticker`, persist into
    v2_chain_snapshots, and return the assembled Chain.

    Returns None if:
      - the Yahoo client constructor raises (network error, bad ticker)
      - the ticker has no listed options (Ticker.options is empty)
      - any option_chain(expiry) call raises mid-fetch

    On failure, NO rows are persisted (transaction is rolled back if any
    persistence happened). On success, all rows are persisted atomically.

    `client` is a callable `(symbol) -> Ticker-like object` injected for
    testing; defaults to a lazy yfinance.Ticker factory.
    """
    if client is None:
        client = _default_yf_client()

    try:
        ticker_obj = client(ticker)
        expiries: list[str] = list(ticker_obj.options)
    except Exception as exc:  # noqa: BLE001 — Yahoo can raise anything
        _log.warning("fetch_chain: client construct failed for %s: %s", ticker, exc)
        return None

    if not expiries:
        _log.info("fetch_chain: %s has no listed options", ticker)
        return None

    quotes: list[ChainQuote] = []
    try:
        for expiry in expiries:
            chain_pair = ticker_obj.option_chain(expiry)
            for _, row in chain_pair.calls.iterrows():
                quotes.append(_row_to_quote(row, expiry=expiry, kind="call"))
            for _, row in chain_pair.puts.iterrows():
                quotes.append(_row_to_quote(row, expiry=expiry, kind="put"))
    except Exception as exc:  # noqa: BLE001
        _log.warning("fetch_chain: parse failed for %s: %s", ticker, exc)
        return None

    # All expiries parsed cleanly — persist atomically.
    #
    # Grok review Tier 1 Finding A: SQLite's default isolation behavior auto-
    # begins a transaction on the first write but the semantics are fragile
    # across Python versions. Use explicit BEGIN / COMMIT / ROLLBACK so the
    # partial-failure test is a real guarantee, not a coincidence of
    # autocommit timing.
    try:
        conn.execute("BEGIN")
        for q in quotes:
            _persist_quote(conn, ticker=ticker, asof_ts=asof_ts, quote=q)
        conn.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001
        conn.execute("ROLLBACK")
        _log.warning("fetch_chain: persist failed for %s: %s", ticker, exc)
        return None

    return Chain(ticker=ticker, asof_ts=asof_ts, quotes=quotes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_chains.py -v`
Expected: PASS (29 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/chains.py tests/unit/test_v2_chains.py
git commit -m "feat(v2/c1): fetch_chain — graceful failure handling + atomic persistence on success"
```

---

## Task 6: `price_leg` — public dispatcher (Yahoo snapshot → fresh fetch → BS fallback)

**Files:**
- Modify: `bullbot/v2/chains.py` (append `price_leg`)
- Modify: `tests/unit/test_v2_chains.py` (append dispatcher tests)

The public `price_leg(leg, spot, today, conn, ...)` returns `(mid_price_per_share, source)` where `source ∈ {'yahoo', 'bs'}`. Resolution order:

1. **Cached Yahoo snapshot:** look in `v2_chain_snapshots` for a row matching `(leg's underlying ticker, today's asof_ts, leg.expiry, leg.strike, leg.kind)`. If found and `mid_price()` returns a non-None value, return `(mid, 'yahoo')`. (Cached lookup, no network.)
2. **Black-Scholes fallback:** compute the per-share BS price with `_price_leg_bs`, using either the IV from a snapshot at that strike if one was cached but had no mid, or `_iv_proxy` over the underlying + VIX bars. Return `(bs_price, 'bs')`.

Share legs always return `(spot, 'bs')` (BS doesn't apply but share legs have a deterministic "price" = spot; tagged `'bs'` because nothing is sourced from a chain).

The dispatcher takes `ticker` explicitly (rather than inferring it from the leg, since `OptionLeg` doesn't carry ticker by design — it's a per-leg primitive). Forward-mode callers (runner_c in C.5) pass the position's ticker. Backtest callers (C.4 synth_chain layer) bypass this dispatcher entirely and price legs directly off synthesized chains — `price_leg` is the live-mode entry point only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_chains.py`:

```python
def _insert_snapshot(
    conn, *, ticker, asof_ts, expiry, strike, kind,
    bid=None, ask=None, last=None, iv=None, oi=None,
):
    conn.execute(
        "INSERT INTO v2_chain_snapshots "
        "(ticker, asof_ts, expiry, strike, kind, bid, ask, last, iv, oi, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'yahoo')",
        (ticker, asof_ts, expiry, strike, kind, bid, ask, last, iv, oi),
    )
    conn.commit()


def _insert_bar(conn, *, ticker, ts, close, timeframe="1d"):
    conn.execute(
        "INSERT OR REPLACE INTO bars "
        "(ticker, timeframe, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1000000)",
        (ticker, timeframe, ts, close, close, close, close),
    )
    conn.commit()


def test_price_leg_uses_cached_yahoo_snapshot_when_bid_ask_present(conn):
    """Snapshot at the exact (ticker, asof, expiry, strike, kind) exists with
    bid+ask → price_leg returns the midpoint, source='yahoo'. No BS pricing
    is invoked."""
    _insert_snapshot(
        conn, ticker="AAPL", asof_ts=1_700_000_000,
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=3.20, ask=3.40, iv=0.30,
    )
    leg = OptionLeg(
        action="buy", kind="call", strike=100.0,
        expiry="2026-06-19", qty=1, entry_price=0.0,
    )
    price, source = chains.price_leg(
        conn=conn, ticker="AAPL", leg=leg, spot=100.0,
        today=date(2026, 5, 17), asof_ts=1_700_000_000,
    )
    assert price == pytest.approx(3.30)
    assert source == "yahoo"


def test_price_leg_falls_back_to_bs_when_no_snapshot_exists(conn):
    """No snapshot row → BS fallback using IV proxy over bars. Returns source='bs'."""
    # Seed 60 bars of underlying and VIX so _iv_proxy has data
    for i in range(60):
        _insert_bar(conn, ticker="AAPL", ts=1_700_000_000 - (60 - i) * 86400, close=100.0)
        _insert_bar(conn, ticker="VIX", ts=1_700_000_000 - (60 - i) * 86400, close=18.0)

    leg = OptionLeg(
        action="buy", kind="call", strike=100.0,
        expiry="2027-05-17", qty=1, entry_price=0.0,
    )
    price, source = chains.price_leg(
        conn=conn, ticker="AAPL", leg=leg, spot=100.0,
        today=date(2026, 5, 17), asof_ts=1_700_000_000,
    )
    assert source == "bs"
    # Realized vol on flat closes hits the 0.30 floor; ATM 1y call ~= 13.84
    assert 8.0 < price < 20.0


def test_price_leg_falls_back_to_bs_when_snapshot_has_no_mid(conn):
    """Snapshot exists but bid=ask=last=None — mid_price() returns None,
    dispatcher falls back to BS using the snapshot's IV (if non-None)."""
    _insert_snapshot(
        conn, ticker="AAPL", asof_ts=1_700_000_000,
        expiry="2027-05-17", strike=100.0, kind="call",
        bid=None, ask=None, last=None, iv=0.30,
    )
    leg = OptionLeg(
        action="buy", kind="call", strike=100.0,
        expiry="2027-05-17", qty=1, entry_price=0.0,
    )
    price, source = chains.price_leg(
        conn=conn, ticker="AAPL", leg=leg, spot=100.0,
        today=date(2026, 5, 17), asof_ts=1_700_000_000,
    )
    assert source == "bs"
    assert price == pytest.approx(13.84, abs=0.20)


def test_price_leg_share_leg_returns_spot_tagged_bs(conn):
    leg = OptionLeg(
        action="buy", kind="share", strike=None, expiry=None,
        qty=100, entry_price=100.0,
    )
    price, source = chains.price_leg(
        conn=conn, ticker="AAPL", leg=leg, spot=99.50,
        today=date(2026, 5, 17), asof_ts=1_700_000_000,
    )
    assert price == 99.50
    assert source == "bs"


def test_price_leg_bs_path_uses_snapshot_iv_when_present(conn):
    """If the snapshot at this strike has an IV but no usable mid, the BS
    fallback should use THAT iv (not the proxy). High snapshot IV → expensive
    price; low snapshot IV → cheap price; assertions cover both."""
    _insert_snapshot(
        conn, ticker="AAPL", asof_ts=1_700_000_000,
        expiry="2027-05-17", strike=100.0, kind="call",
        bid=None, ask=None, last=None, iv=0.80,
    )
    leg = OptionLeg(
        action="buy", kind="call", strike=100.0,
        expiry="2027-05-17", qty=1, entry_price=0.0,
    )
    high_iv_price, _ = chains.price_leg(
        conn=conn, ticker="AAPL", leg=leg, spot=100.0,
        today=date(2026, 5, 17), asof_ts=1_700_000_000,
    )
    # ATM 1y call at IV=0.80, r=0.045 → ~33 per share
    assert 30.0 < high_iv_price < 40.0


def test_price_leg_falls_back_to_bs_when_snapshot_is_stale(conn):
    """Grok review Tier 1 Finding B: a snapshot older than
    SNAPSHOT_FRESHNESS_SECONDS (24h) is treated as stale — price_leg
    returns 'bs' not 'yahoo' even when the snapshot has usable bid/ask.
    The stale snapshot's IV is still consulted by the BS fallback."""
    asof = 1_700_000_000
    stale_asof = asof - 2 * 86400  # 2 days old
    _insert_snapshot(
        conn, ticker="AAPL", asof_ts=stale_asof,
        expiry="2027-05-17", strike=100.0, kind="call",
        bid=3.20, ask=3.40, iv=0.30,
    )
    leg = OptionLeg(
        action="buy", kind="call", strike=100.0,
        expiry="2027-05-17", qty=1, entry_price=0.0,
    )
    price, source = chains.price_leg(
        conn=conn, ticker="AAPL", leg=leg, spot=100.0,
        today=date(2026, 5, 17), asof_ts=asof,
    )
    assert source == "bs"
    # BS with the stale snapshot's IV=0.30 should land near 13.84
    assert 12.0 < price < 16.0


def test_price_leg_uses_snapshot_within_freshness_window(conn):
    """Snapshot from 12h before asof_ts is still considered fresh →
    returns 'yahoo' with the snapshot's mid."""
    asof = 1_700_000_000
    fresh_asof = asof - 12 * 3600  # 12 hours old
    _insert_snapshot(
        conn, ticker="AAPL", asof_ts=fresh_asof,
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=3.20, ask=3.40, iv=0.30,
    )
    leg = OptionLeg(
        action="buy", kind="call", strike=100.0,
        expiry="2026-06-19", qty=1, entry_price=0.0,
    )
    price, source = chains.price_leg(
        conn=conn, ticker="AAPL", leg=leg, spot=100.0,
        today=date(2026, 5, 17), asof_ts=asof,
    )
    assert source == "yahoo"
    assert price == pytest.approx(3.30)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_chains.py -v`
Expected: FAIL on the 5 new tests — `AttributeError: module 'bullbot.v2.chains' has no attribute 'price_leg'`.

- [ ] **Step 3: Implement `price_leg` + private bars loader**

Append to `bullbot/v2/chains.py`:

```python
from types import SimpleNamespace


def _load_bars(conn: sqlite3.Connection, ticker: str, asof_ts: int, limit: int = 100):
    """Load daily bars for `ticker` with ts <= asof_ts, oldest-first. Same
    shape as bullbot.v2.runner._load_bars (intentionally duplicated to keep
    this module self-contained — runner._load_bars is a private symbol)."""
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM bars "
        "WHERE ticker=? AND timeframe='1d' AND ts<=? "
        "ORDER BY ts DESC LIMIT ?",
        (ticker, asof_ts, limit),
    ).fetchall()
    bars = [
        SimpleNamespace(
            ts=r["ts"], open=r["open"], high=r["high"],
            low=r["low"], close=r["close"], volume=r["volume"],
        )
        for r in rows
    ]
    bars.reverse()
    return bars


SNAPSHOT_FRESHNESS_SECONDS = 86_400  # 24h — Grok review Tier 1 Finding B


def _snapshot_at(
    conn: sqlite3.Connection, *, ticker: str, asof_ts: int,
    expiry: str, strike: float, kind: str,
) -> tuple[ChainQuote, int] | None:
    """Look up the most recent snapshot for (ticker, expiry, strike, kind)
    with asof_ts <= the requested asof_ts. Returns (ChainQuote, snapshot_asof)
    or None if no row exists.

    Returning the snapshot's own asof_ts (rather than checking freshness
    inside this helper) lets the caller decide what 'fresh' means in context
    (forward runner = strict 24h, backtest replay = might want longer)."""
    row = conn.execute(
        "SELECT asof_ts, bid, ask, last, iv, oi, source FROM v2_chain_snapshots "
        "WHERE ticker=? AND asof_ts<=? AND expiry=? AND strike=? AND kind=? "
        "ORDER BY asof_ts DESC LIMIT 1",
        (ticker, asof_ts, expiry, strike, kind),
    ).fetchone()
    if row is None:
        return None
    quote = ChainQuote(
        expiry=expiry, strike=strike, kind=kind,
        bid=row["bid"], ask=row["ask"], last=row["last"],
        iv=row["iv"], oi=row["oi"], source=row["source"],
    )
    return (quote, row["asof_ts"])


def price_leg(
    *,
    conn: sqlite3.Connection,
    ticker: str,
    leg: OptionLeg,
    spot: float,
    today: date,
    asof_ts: int,
) -> tuple[float, str]:
    """Return (per-share mid price, source) for one leg.

    Resolution order:
      1. Cached Yahoo snapshot at this (ticker, expiry, strike, kind) whose
         own asof_ts is within SNAPSHOT_FRESHNESS_SECONDS (24h) of the caller's
         asof_ts AND has a usable mid → return (mid, 'yahoo').
      2. Black-Scholes fallback using:
         - the snapshot's IV if a row exists with non-None IV (fresh OR stale
           — a stale IV is still a better hint than no hint)
         - else IV proxy over (ticker bars, VIX bars)
         Return (bs_price, 'bs').

    Share legs short-circuit: return (spot, 'bs') — shares have no chain.

    Grok review Tier 1 Finding B: stale snapshots fall back to BS to prevent
    weekend / market-closed re-runs from returning prices from days ago tagged
    as 'yahoo'. The forward runner (C.5) only calls with current-day asof_ts;
    the freshness window is a guardrail for unusual cases.
    """
    if leg.kind == "share":
        return (spot, "bs")

    snap_pair = _snapshot_at(
        conn, ticker=ticker, asof_ts=asof_ts,
        expiry=leg.expiry, strike=leg.strike, kind=leg.kind,
    )
    snap = None
    snap_age = None
    if snap_pair is not None:
        snap, snap_age_asof = snap_pair
        snap_age = asof_ts - snap_age_asof

    if snap is not None and snap_age <= SNAPSHOT_FRESHNESS_SECONDS:
        mid = snap.mid_price()
        if mid is not None:
            return (mid, "yahoo")

    if snap is not None and snap_age > SNAPSHOT_FRESHNESS_SECONDS:
        _log.info(
            "price_leg: snapshot for %s %s %s %s is stale (%ds old), falling back to BS",
            ticker, leg.expiry, leg.strike, leg.kind, snap_age,
        )

    # BS fallback — prefer snapshot IV if present (even stale), else IV proxy.
    if snap is not None and snap.iv is not None:
        iv = snap.iv
    else:
        underlying_bars = _load_bars(conn, ticker, asof_ts)
        vix_bars = _load_bars(conn, "VIX", asof_ts, limit=60)
        iv = _iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)

    bs = _price_leg_bs(leg=leg, spot=spot, iv=iv, today=today)
    return (bs, "bs")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_chains.py -v`
Expected: PASS (36 tests total — 5 dispatcher tests + 2 freshness tests added in this task).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/chains.py tests/unit/test_v2_chains.py
git commit -m "feat(v2/c1): price_leg dispatcher with 24h snapshot freshness guardrail (Grok Tier 1 Finding B)"
```

---

## Task 7: End-to-end integration test

**Files:**
- Create: `tests/integration/test_v2_chains_end_to_end.py`

Exercises the full sequence a daily forward-mode caller would run: fetch a chain → persist into snapshots → call `price_leg` on a leg that hits the cache → call `price_leg` on a leg that misses the cache → verify the source tag on each. Single test method, single fixture, end-to-end coverage of the Task 1–6 surface.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_v2_chains_end_to_end.py`:

```python
"""End-to-end integration test for v2 chains module.

Wires together fetch_chain → v2_chain_snapshots → price_leg to confirm the
full forward-mode flow works as a unit. Uses mocked yfinance (no network).
"""
from __future__ import annotations

import sqlite3
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from bullbot.db.migrations import apply_schema
from bullbot.v2 import chains
from bullbot.v2.positions import OptionLeg


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def _seed_bars(conn, ticker, ts, close):
    conn.execute(
        "INSERT OR REPLACE INTO bars "
        "(ticker, timeframe, ts, open, high, low, close, volume) "
        "VALUES (?, '1d', ?, ?, ?, ?, ?, 1_000_000)",
        (ticker, ts, close, close, close, close),
    )


def test_full_flow_fetch_then_price_cache_hit_and_cache_miss(conn):
    """1. Seed 60 bars for AAPL and VIX so the IV proxy has data.
    2. fetch_chain pulls a stubbed Yahoo chain at strikes 95/100/105.
    3. price_leg on the cached 100-call returns yahoo mid.
    4. price_leg on a 200-call (not in the chain) falls back to BS."""

    asof = 1_700_000_000
    for i in range(60):
        _seed_bars(conn, "AAPL", asof - (60 - i) * 86400, 100.0)
        _seed_bars(conn, "VIX", asof - (60 - i) * 86400, 18.0)
    conn.commit()

    calls = pd.DataFrame([
        {"strike": 95.0, "bid": 6.10, "ask": 6.30, "lastPrice": 6.20,
         "impliedVolatility": 0.32, "openInterest": 420},
        {"strike": 100.0, "bid": 3.20, "ask": 3.40, "lastPrice": 3.30,
         "impliedVolatility": 0.30, "openInterest": 1850},
        {"strike": 105.0, "bid": 1.40, "ask": 1.55, "lastPrice": 1.47,
         "impliedVolatility": 0.29, "openInterest": 730},
    ])
    puts = pd.DataFrame([])

    class FakeTicker:
        options = ["2026-06-19"]
        def option_chain(self, expiry):
            return SimpleNamespace(calls=calls, puts=puts)

    result = chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=asof,
        client=lambda symbol: FakeTicker(),
    )
    assert result is not None
    assert len(result.quotes) == 3

    # 1. Cache hit at 100 call.
    leg_cached = OptionLeg(
        action="buy", kind="call", strike=100.0,
        expiry="2026-06-19", qty=1, entry_price=0.0,
    )
    price_cached, source_cached = chains.price_leg(
        conn=conn, ticker="AAPL", leg=leg_cached, spot=100.0,
        today=date(2026, 5, 17), asof_ts=asof,
    )
    assert source_cached == "yahoo"
    assert price_cached == pytest.approx(3.30)

    # 2. Cache miss at 200 call — BS fallback.
    leg_uncached = OptionLeg(
        action="buy", kind="call", strike=200.0,
        expiry="2026-06-19", qty=1, entry_price=0.0,
    )
    price_uncached, source_uncached = chains.price_leg(
        conn=conn, ticker="AAPL", leg=leg_uncached, spot=100.0,
        today=date(2026, 5, 17), asof_ts=asof,
    )
    assert source_uncached == "bs"
    # 200 strike on a 100-spot, ~1mo DTE, low vol → deep OTM, near-zero price
    assert price_uncached < 0.10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration/test_v2_chains_end_to_end.py -v`
Expected: PASS immediately, since the implementation is complete from Tasks 1–6. (If it fails, that signals a real integration gap to fix before continuing.)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_v2_chains_end_to_end.py
git commit -m "test(v2/c1): integration test for fetch_chain -> snapshot -> price_leg full flow"
```

---

## Task 8: Full regression check

**Files:** none (test-only verification step)

- [ ] **Step 1: Run the full unit suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit -q`
Expected: All previously-passing tests still pass; the new `test_v2_chains.py` adds 36 tests, bringing unit total from 547 → 583.

- [ ] **Step 2: Run the integration suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration -q`
Expected: All pass, including the new `test_v2_chains_end_to_end.py` (1 test).

- [ ] **Step 3: Static-import sanity check**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -c "from bullbot.v2 import chains; print(chains.fetch_chain, chains.price_leg, chains.ChainQuote, chains.Chain)"`
Expected: prints the four public symbols without ImportError.

- [ ] **Step 4: Optional marker commit**

```bash
git commit --allow-empty -m "chore(v2/c1): Phase C.1 complete — chains.py (Yahoo + BS) landed"
```

---

## Acceptance criteria

C.1 is complete when ALL of the following hold:

1. `bullbot/v2/chains.py` exists and exports exactly four public symbols: `ChainQuote`, `Chain`, `fetch_chain`, `price_leg`. (Plus the public IV-bound constants `IV_PROXY_MIN`, `IV_PROXY_MAX`, and the freshness constant `SNAPSHOT_FRESHNESS_SECONDS` for use by downstream callers that want to tune.)
2. `tests/unit/test_v2_chains.py` contains the 36 tests listed in Tasks 1–6 and they all pass.
3. `tests/integration/test_v2_chains_end_to_end.py` exists and its single test passes.
4. Full unit + integration test suite is green (no regressions vs the C.0 baseline of 547 unit + 79 integration tests).
5. `chains.py` is under 250 LOC.
6. No new third-party dependencies are introduced (yfinance and pandas are already in `requirements.txt`).
7. No changes to `bullbot/db/migrations.py` (schema for `v2_chain_snapshots` was already laid down by C.0 Task 1).
8. `price_leg` returns explicit `source` tags so downstream `v2_position_mtm` rows in C.5 can persist whether the value came from Yahoo, BS, or a mix.

## What this unblocks (next plans)

- **C.4 (`backtest/synth_chain.py`):** the BS pricing wrapper (`_price_leg_bs`) and the IV proxy (`_iv_proxy`) are reusable inside `synth_chain.synthesize`. The Tier 1 Finding 3 event-day IV bump lives there, layered on top of the same proxy logic — synth_chain will import `_iv_proxy` and post-process its output.
- **C.5 (`runner_c.py` + dashboard tabs):** the daily forward MtM loop calls `price_leg` once per held leg per day. Source tag flows directly into `v2_position_mtm.source` (which the C.0 schema already supports). The dashboard's "V2 Positions" tab reads from `v2_position_mtm` and can color-code Yahoo vs BS rows.

## Notes for the implementer

- **`tests/conftest.py` already adds repo root to `sys.path`** — no `PYTHONPATH` munging needed in the new test files.
- **Use `/Users/danield.runion/Projects/bull-bot/.venv/bin/python` as the pytest runner** — `.venv` lives at the main repo, not in the worktree. The C.0 plan documented this; the same applies here.
- **Bars-table writes from tests** need `INSERT OR REPLACE` because the `(ticker, timeframe, ts)` UNIQUE constraint will reject duplicate inserts; the seed helpers in Tasks 6 and 7 use this idiom.
- **pandas DataFrame `.iterrows()` returns `(index, row)` tuples** where `row` is a `Series` supporting `row.get(col)` and `row[col]` — both used in `_row_to_quote`.
- **Yahoo's `lastPrice` field is sometimes a stale tick from days ago**; that's why `mid_price()` prefers `(bid+ask)/2` and only falls back to `last` when bid or ask is missing. Don't change this fallback order.
- **The IV proxy intentionally does NOT include the event-day bump from Grok review Tier 1 Finding 3.** That bump lives in `backtest/synth_chain.py` (C.4) because it's a backtest-only correction for the BS pricing being too clean on jump days. Forward mode uses raw realized-vol × VIX regime — Yahoo's own IV (when present) is the real-time check on jump-day pricing.
