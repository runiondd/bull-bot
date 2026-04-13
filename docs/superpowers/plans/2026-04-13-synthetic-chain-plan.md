# Synthetic Options Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate synthetic option chains from realized volatility so the evolver can discover growth strategies without real options data.

**Architecture:** A pure-function module (`synthetic_chain.py`) implements Black-Scholes pricing with realized vol from daily bars. A one-line fallback in `step.py:_load_chain_at_cursor()` calls it when the DB has no options data. Everything downstream works unchanged.

**Tech Stack:** Python 3.11, `math` stdlib (for Black-Scholes), existing `OptionContract` Pydantic model

**Spec:** `docs/superpowers/specs/2026-04-13-synthetic-chain-design.md`

---

## File Structure

### New Files
- `bullbot/data/synthetic_chain.py` — Black-Scholes pricing, realized vol, chain generation
- `tests/unit/test_synthetic_chain.py` — unit tests for pricing, vol, and chain shape

### Modified Files
- `bullbot/engine/step.py:61-89` — fallback to synthetic chain when DB returns empty

---

### Task 1: Black-Scholes Pricing and Realized Vol

**Files:**
- Create: `bullbot/data/synthetic_chain.py`
- Create: `tests/unit/test_synthetic_chain.py`

- [ ] **Step 1: Write failing tests for Black-Scholes and realized vol**

```python
# tests/unit/test_synthetic_chain.py
import math
import pytest
from bullbot.data.schemas import Bar


def _make_bars(closes: list[float], base_ts: int = 86400 * 100) -> list[Bar]:
    return [
        Bar(ticker="TSLA", timeframe="1d", ts=base_ts + 86400 * i,
            open=c, high=c + 1, low=c - 1, close=c, volume=1000000, source="yahoo")
        for i, c in enumerate(closes)
    ]


def test_realized_vol_constant_prices():
    from bullbot.data.synthetic_chain import realized_vol
    bars = _make_bars([100.0] * 31)
    vol = realized_vol(bars)
    assert vol == 0.0


def test_realized_vol_trending():
    from bullbot.data.synthetic_chain import realized_vol
    closes = [100.0 * (1.001 ** i) for i in range(31)]
    bars = _make_bars(closes)
    vol = realized_vol(bars)
    assert 0.0 < vol < 0.10


def test_realized_vol_fallback_short_bars():
    from bullbot.data.synthetic_chain import realized_vol
    bars = _make_bars([100.0] * 10)
    vol = realized_vol(bars)
    assert vol == 0.30


def test_bs_call_atm():
    from bullbot.data.synthetic_chain import bs_price
    price = bs_price(spot=100.0, strike=100.0, t_years=1.0, vol=0.30, r=0.045, kind="C")
    assert 12.0 < price < 15.0


def test_bs_put_atm():
    from bullbot.data.synthetic_chain import bs_price
    price = bs_price(spot=100.0, strike=100.0, t_years=1.0, vol=0.30, r=0.045, kind="P")
    assert 7.0 < price < 11.0


def test_bs_call_deep_itm():
    from bullbot.data.synthetic_chain import bs_price
    price = bs_price(spot=100.0, strike=50.0, t_years=1.0, vol=0.30, r=0.045, kind="C")
    assert price > 48.0


def test_bs_put_deep_otm():
    from bullbot.data.synthetic_chain import bs_price
    price = bs_price(spot=100.0, strike=50.0, t_years=1.0, vol=0.30, r=0.045, kind="P")
    assert price < 1.0


def test_bs_zero_time():
    from bullbot.data.synthetic_chain import bs_price
    call_itm = bs_price(spot=100.0, strike=90.0, t_years=0.0, vol=0.30, r=0.045, kind="C")
    assert abs(call_itm - 10.0) < 0.01
    call_otm = bs_price(spot=100.0, strike=110.0, t_years=0.0, vol=0.30, r=0.045, kind="P")
    assert abs(call_otm - 10.0) < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_synthetic_chain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bullbot.data.synthetic_chain'`

- [ ] **Step 3: Implement Black-Scholes and realized vol**

```python
# bullbot/data/synthetic_chain.py
"""Synthetic option chain generator using Black-Scholes and realized volatility."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from bullbot import config
from bullbot.data.schemas import Bar, OptionContract


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def realized_vol(bars: list[Bar], window: int = 30) -> float:
    """Annualized realized volatility from daily log returns.

    Returns 0.30 as a default if fewer than window+1 bars are available.
    """
    if len(bars) < window + 1:
        return 0.30
    closes = [b.close for b in bars[-(window + 1):]]
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(log_returns) < 2:
        return 0.30
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def bs_price(
    spot: float, strike: float, t_years: float, vol: float, r: float, kind: str,
) -> float:
    """Black-Scholes European option price.

    kind: "C" for call, "P" for put.
    Returns intrinsic value when t_years <= 0.
    """
    if t_years <= 0:
        if kind == "C":
            return max(0.0, spot - strike)
        return max(0.0, strike - spot)
    if vol <= 0:
        df = math.exp(-r * t_years)
        if kind == "C":
            return max(0.0, spot - strike * df)
        return max(0.0, strike * df - spot)

    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * t_years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t

    if kind == "C":
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    return strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_synthetic_chain.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add bullbot/data/synthetic_chain.py tests/unit/test_synthetic_chain.py
git commit -m "add Black-Scholes pricing and realized vol computation"
```

---

### Task 2: Chain Generation

**Files:**
- Modify: `bullbot/data/synthetic_chain.py`
- Modify: `tests/unit/test_synthetic_chain.py`

- [ ] **Step 1: Write failing tests for chain generation**

Add to `tests/unit/test_synthetic_chain.py`:

```python
def test_generate_chain_produces_contracts():
    from bullbot.data.synthetic_chain import generate_synthetic_chain
    closes = [250.0 + i * 0.5 for i in range(31)]
    bars = _make_bars(closes)
    chain = generate_synthetic_chain(
        ticker="TSLA", spot=265.0, cursor=bars[-1].ts, bars=bars,
    )
    assert len(chain) > 0
    assert all(isinstance(c, OptionContract) for c in chain)


def test_generate_chain_has_calls_and_puts():
    from bullbot.data.synthetic_chain import generate_synthetic_chain
    closes = [250.0 + i * 0.5 for i in range(31)]
    bars = _make_bars(closes)
    chain = generate_synthetic_chain(
        ticker="TSLA", spot=265.0, cursor=bars[-1].ts, bars=bars,
    )
    kinds = {c.kind for c in chain}
    assert "C" in kinds
    assert "P" in kinds


def test_generate_chain_has_multiple_expiries():
    from bullbot.data.synthetic_chain import generate_synthetic_chain
    closes = [250.0 + i * 0.5 for i in range(31)]
    bars = _make_bars(closes)
    chain = generate_synthetic_chain(
        ticker="TSLA", spot=265.0, cursor=bars[-1].ts, bars=bars,
    )
    expiries = {c.expiry for c in chain}
    assert len(expiries) >= 4


def test_generate_chain_bid_ask_valid():
    from bullbot.data.synthetic_chain import generate_synthetic_chain
    closes = [250.0 + i * 0.5 for i in range(31)]
    bars = _make_bars(closes)
    chain = generate_synthetic_chain(
        ticker="TSLA", spot=265.0, cursor=bars[-1].ts, bars=bars,
    )
    for c in chain:
        assert c.nbbo_bid >= 0.01
        assert c.nbbo_ask > c.nbbo_bid
        assert c.iv is not None and c.iv > 0


def test_generate_chain_empty_on_short_bars():
    from bullbot.data.synthetic_chain import generate_synthetic_chain
    bars = _make_bars([100.0] * 5)
    chain = generate_synthetic_chain(
        ticker="TSLA", spot=100.0, cursor=bars[-1].ts, bars=bars,
    )
    assert len(chain) > 0  # still works using fallback vol
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_synthetic_chain.py -v`
Expected: FAIL — `ImportError: cannot import name 'generate_synthetic_chain'`

- [ ] **Step 3: Implement generate_synthetic_chain()**

Add to `bullbot/data/synthetic_chain.py`:

```python
_DTE_TARGETS = [30, 60, 90, 180, 270, 365]


def _strike_step(spot: float) -> float:
    if spot < 50:
        return 2.50
    if spot <= 200:
        return 5.0
    return 10.0


def generate_synthetic_chain(
    ticker: str,
    spot: float,
    cursor: int,
    bars: list[Bar],
    risk_free_rate: float = config.RISK_FREE_RATE,
) -> list[OptionContract]:
    """Generate a synthetic option chain using Black-Scholes and realized vol."""
    vol = realized_vol(bars)
    step = _strike_step(spot)

    low_strike = math.floor(spot * 0.80 / step) * step
    high_strike = math.ceil(spot * 1.20 / step) * step
    strikes = []
    s = low_strike
    while s <= high_strike:
        strikes.append(round(s, 2))
        s += step

    cursor_dt = datetime.fromtimestamp(cursor, tz=timezone.utc)
    expiries: list[tuple[str, float]] = []
    for dte in _DTE_TARGETS:
        exp_ts = cursor + dte * 86400
        exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
        expiry_str = exp_dt.strftime("%Y-%m-%d")
        t_years = dte / 365.0
        expiries.append((expiry_str, t_years))

    contracts: list[OptionContract] = []
    for expiry_str, t_years in expiries:
        for strike in strikes:
            for kind in ("C", "P"):
                price = bs_price(spot, strike, t_years, vol, risk_free_rate, kind)
                bid = max(0.01, round(price * 0.95, 2))
                ask = round(max(price * 1.05, bid + 0.01), 2)
                contracts.append(OptionContract(
                    ticker=ticker,
                    expiry=expiry_str,
                    strike=strike,
                    kind=kind,
                    ts=cursor,
                    nbbo_bid=bid,
                    nbbo_ask=ask,
                    volume=100,
                    open_interest=1000,
                    iv=round(vol, 4),
                ))
    return contracts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_synthetic_chain.py -v`
Expected: 13 passed

- [ ] **Step 5: Run full suite**

Run: `pytest tests/ -x -q`
Expected: All pass (275+13 = 288)

- [ ] **Step 6: Commit**

```bash
git add bullbot/data/synthetic_chain.py tests/unit/test_synthetic_chain.py
git commit -m "add synthetic chain generator: strikes, expiries, Black-Scholes pricing"
```

---

### Task 3: Integration — Fallback in step.py

**Files:**
- Modify: `bullbot/engine/step.py:61-89`

- [ ] **Step 1: Add synthetic fallback to _load_chain_at_cursor()**

In `bullbot/engine/step.py`, modify `_load_chain_at_cursor()` to fall back to synthetic chain when DB returns empty:

Replace the existing function (lines 61-89):

```python
def _load_chain_at_cursor(conn: sqlite3.Connection, ticker: str, cursor: int) -> list[OptionContract]:
    """Load option chain as it looked on/before cursor.

    Falls back to synthetic chain (Black-Scholes + realized vol) when
    no real options data exists in the database.
    """
    rows = conn.execute("""
        SELECT oc.*
        FROM option_contracts oc
        INNER JOIN (
            SELECT ticker, expiry, strike, kind, MAX(ts) AS max_ts
            FROM option_contracts
            WHERE ticker=? AND ts<=?
            GROUP BY ticker, expiry, strike, kind
        ) m ON oc.ticker=m.ticker AND oc.expiry=m.expiry
            AND oc.strike=m.strike AND oc.kind=m.kind AND oc.ts=m.max_ts
    """, (ticker, cursor)).fetchall()

    if rows:
        return [
            OptionContract(
                ticker=r["ticker"],
                expiry=r["expiry"],
                strike=r["strike"],
                kind=_DB_KIND_TO_MODEL.get(r["kind"], r["kind"]),
                ts=r["ts"],
                nbbo_bid=r["bid"],
                nbbo_ask=r["ask"],
                last=None,
                volume=int(r["volume"]) if r["volume"] is not None else None,
                open_interest=int(r["open_interest"]) if r["open_interest"] is not None else None,
                iv=r["iv"],
            )
            for r in rows
        ]

    bars = _load_bars_at_cursor(conn, ticker, cursor, limit=60)
    if len(bars) < 2:
        return []
    from bullbot.data.synthetic_chain import generate_synthetic_chain
    return generate_synthetic_chain(
        ticker=ticker, spot=bars[-1].close, cursor=cursor, bars=bars,
    )
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass. Existing tests that use real options data (SPY) are unaffected — the fallback only triggers when the DB query returns empty.

- [ ] **Step 3: Commit**

```bash
git add bullbot/engine/step.py
git commit -m "wire synthetic chain fallback into step.py for tickers without options data"
```
