# Growth Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bidirectional growth strategies to Bull-Bot, starting with TSLA, using regime-driven capital allocation and evolver-validated entries.

**Architecture:** New strategy classes (GrowthLEAPS, BearPutSpread, GrowthEquity) plug into the existing evolver pipeline with adapted walk-forward metrics (CAGR, Sortino instead of profit factor) and longer evaluation windows (5 years, 90-day folds). A category system separates income and growth capital pools, with regime-driven allocation fractions.

**Tech Stack:** Python 3.11, Pydantic v2, SQLite, pytest, Anthropic API

**Spec:** `docs/superpowers/specs/2026-04-13-growth-strategy-design.md`

---

## File Structure

### New Files
- `bullbot/strategies/growth_leaps.py` — GrowthLEAPS strategy class
- `bullbot/strategies/bear_put_spread.py` — BearPutSpread strategy class
- `bullbot/strategies/growth_equity.py` — GrowthEquity strategy class
- `tests/unit/test_growth_leaps.py` — GrowthLEAPS tests
- `tests/unit/test_bear_put_spread.py` — BearPutSpread tests
- `tests/unit/test_growth_equity.py` — GrowthEquity tests
- `tests/unit/test_growth_metrics.py` — CAGR, Sortino tests
- `tests/unit/test_category_sizer.py` — Category-aware position sizer tests

### Modified Files
- `bullbot/config.py` — growth config constants, category map, growth WF params, growth gate thresholds
- `bullbot/features/indicators.py` — add `cagr()`, `sortino()` functions
- `bullbot/strategies/registry.py` — register new strategy classes
- `bullbot/backtest/walkforward.py` — growth-aware `BacktestMetrics`, `compute_folds`, `aggregate`
- `bullbot/evolver/plateau.py` — growth gate in `classify()`
- `bullbot/engine/position_sizer.py` — category-aware sizing with regime-driven capital pool
- `bullbot/engine/fill_model.py` — add `simulate_equity_buy()`, `simulate_equity_sell()`
- `bullbot/engine/step.py` — store `entry_delta` on positions at open time
- `bullbot/evolver/proposer.py` — category-aware system prompt and strategy menu
- `bullbot/db/schema.sql` — add `category` to `ticker_state`, `entry_delta` to `positions`
- `tests/unit/test_config.py` — assertions for new constants
- `tests/unit/test_plateau.py` — growth gate tests

---

## Task 1: Add CAGR and Sortino to Indicators

**Files:**
- Modify: `bullbot/features/indicators.py`
- Create: `tests/unit/test_growth_metrics.py`

- [ ] **Step 1: Write failing tests for cagr()**

```python
# tests/unit/test_growth_metrics.py
import math
import pytest
from bullbot.features import indicators


def test_cagr_positive_return():
    # $100 -> $200 over 365 days = 100% CAGR
    curve = [100.0, 200.0]
    result = indicators.cagr(curve, days=365)
    assert abs(result - 1.0) < 0.01


def test_cagr_negative_return():
    # $100 -> $50 over 365 days = -50% CAGR
    curve = [100.0, 50.0]
    result = indicators.cagr(curve, days=365)
    assert abs(result - (-0.50)) < 0.01


def test_cagr_multi_year():
    # $100 -> $200 over 730 days (2 years) = ~41.4% CAGR
    curve = [100.0, 200.0]
    result = indicators.cagr(curve, days=730)
    assert abs(result - 0.414) < 0.01


def test_cagr_flat():
    curve = [100.0, 100.0]
    result = indicators.cagr(curve, days=365)
    assert result == 0.0


def test_cagr_too_short():
    curve = [100.0]
    result = indicators.cagr(curve, days=365)
    assert result == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_growth_metrics.py -v`
Expected: FAIL — `AttributeError: module 'bullbot.features.indicators' has no attribute 'cagr'`

- [ ] **Step 3: Implement cagr()**

Add to `bullbot/features/indicators.py`:

```python
def cagr(equity_curve: list[float], days: int) -> float:
    """Compound annual growth rate from an equity curve.

    Returns 0.0 if the curve has fewer than 2 points or days <= 0.
    """
    if len(equity_curve) < 2 or days <= 0:
        return 0.0
    start, end = equity_curve[0], equity_curve[-1]
    if start <= 0:
        return 0.0
    years = days / 365.0
    return (end / start) ** (1.0 / years) - 1.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_growth_metrics.py -v`
Expected: 5 passed

- [ ] **Step 5: Write failing tests for sortino()**

Add to `tests/unit/test_growth_metrics.py`:

```python
def test_sortino_all_positive():
    # All positive returns -> infinite sortino (no downside deviation)
    returns = [0.05, 0.03, 0.04, 0.02, 0.06]
    result = indicators.sortino(returns, risk_free_rate=0.0)
    assert math.isinf(result)


def test_sortino_mixed_returns():
    returns = [0.10, -0.05, 0.08, -0.02, 0.06, -0.01, 0.04]
    result = indicators.sortino(returns, risk_free_rate=0.0)
    assert result > 0


def test_sortino_all_negative():
    returns = [-0.05, -0.03, -0.04]
    result = indicators.sortino(returns, risk_free_rate=0.0)
    assert result < 0


def test_sortino_empty():
    result = indicators.sortino([], risk_free_rate=0.0)
    assert result == 0.0


def test_sortino_with_risk_free():
    returns = [0.10, -0.05, 0.08, -0.02, 0.06]
    r0 = indicators.sortino(returns, risk_free_rate=0.0)
    r1 = indicators.sortino(returns, risk_free_rate=0.04)
    assert r1 < r0  # Higher hurdle -> lower ratio
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/unit/test_growth_metrics.py -v`
Expected: 5 new tests FAIL — `AttributeError: no attribute 'sortino'`

- [ ] **Step 7: Implement sortino()**

Add to `bullbot/features/indicators.py`:

```python
def sortino(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """Sortino ratio: excess return over downside deviation.

    Returns 0.0 for empty input. Returns inf if no downside deviation.
    """
    if len(returns) < 2:
        return 0.0
    excess = [r - risk_free_rate for r in returns]
    mean_excess = sum(excess) / len(excess)
    downside = [min(0.0, e) ** 2 for e in excess]
    downside_dev = (sum(downside) / len(downside)) ** 0.5
    if downside_dev == 0:
        return float("inf") if mean_excess > 0 else 0.0
    return mean_excess / downside_dev
```

- [ ] **Step 8: Run all tests to verify they pass**

Run: `pytest tests/unit/test_growth_metrics.py -v`
Expected: 10 passed

- [ ] **Step 9: Commit**

```bash
git add bullbot/features/indicators.py tests/unit/test_growth_metrics.py
git commit -m "add cagr() and sortino() to indicators for growth strategy evaluation"
```

---

## Task 2: Add Category System and Growth Config

**Files:**
- Modify: `bullbot/config.py`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Add growth constants and category map to config**

Add to `bullbot/config.py` after the existing `PLATEAU_*` block (~line 52):

```python
# --- Growth strategy ---

TICKER_CATEGORY: dict[str, str] = {
    "SPY": "income",
    "QQQ": "income",
    "IWM": "income",
    "AAPL": "income",
    "MSFT": "income",
    "NVDA": "growth",
    "TSLA": "growth",
    "AMD": "income",
    "META": "income",
    "GOOGL": "income",
}

GROWTH_FRAC_BULL = 0.40
GROWTH_FRAC_CHOP = 0.20
GROWTH_FRAC_BEAR = 0.10

GROWTH_WF_WINDOW_MONTHS = 60
GROWTH_WF_STEP_DAYS = 90

GROWTH_EDGE_CAGR_MIN = 0.20
GROWTH_EDGE_SORTINO_MIN = 1.0
GROWTH_EDGE_MAX_DD_PCT = 0.35
GROWTH_EDGE_TRADE_COUNT_MIN = 5
```

- [ ] **Step 2: Add config test assertions**

Add to `tests/unit/test_config.py`:

```python
def test_growth_config():
    assert config.TICKER_CATEGORY["TSLA"] == "growth"
    assert config.TICKER_CATEGORY["SPY"] == "income"
    assert config.GROWTH_FRAC_BULL == 0.40
    assert config.GROWTH_FRAC_CHOP == 0.20
    assert config.GROWTH_FRAC_BEAR == 0.10
    assert config.GROWTH_WF_WINDOW_MONTHS == 60
    assert config.GROWTH_WF_STEP_DAYS == 90
    assert config.GROWTH_EDGE_CAGR_MIN == 0.20
    assert config.GROWTH_EDGE_SORTINO_MIN == 1.0
    assert config.GROWTH_EDGE_MAX_DD_PCT == 0.35
    assert config.GROWTH_EDGE_TRADE_COUNT_MIN == 5
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/unit/test_config.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add bullbot/config.py tests/unit/test_config.py
git commit -m "add growth config: category map, regime capital fractions, growth WF and gate thresholds"
```

---

## Task 3: GrowthLEAPS Strategy Class

**Files:**
- Create: `bullbot/strategies/growth_leaps.py`
- Modify: `bullbot/strategies/registry.py`
- Create: `tests/unit/test_growth_leaps.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_growth_leaps.py
import pytest
from bullbot.strategies.growth_leaps import GrowthLEAPS
from bullbot.strategies.base import StrategySnapshot
from bullbot.data.schemas import Bar, OptionContract


def _make_bars(n=60, base_close=250.0):
    return [
        Bar(ticker="TSLA", timeframe="1d", ts=86400 * i,
            open=base_close, high=base_close + 5, low=base_close - 5,
            close=base_close, volume=1000000, source="uw")
        for i in range(n)
    ]


def _make_chain(spot=250.0, expiry="2027-01-15", ts=86400 * 60):
    """Build a minimal chain with calls at various strikes."""
    contracts = []
    for strike in range(200, 350, 10):
        delta = max(0.01, min(0.99, 1.0 - (strike - spot) / (2 * spot)))
        mid = max(1.0, (spot - strike) + 30.0) if strike < spot else max(0.50, 30.0 - (strike - spot) * 0.15)
        contracts.append(OptionContract(
            ticker="TSLA", expiry=expiry, strike=float(strike), kind="C",
            ts=ts, nbbo_bid=mid - 0.50, nbbo_ask=mid + 0.50,
            volume=100, open_interest=500, iv=0.45,
        ))
    return contracts


def _make_snapshot(regime="bull", iv_rank=40.0):
    bars = _make_bars()
    chain = _make_chain(ts=bars[-1].ts)
    return StrategySnapshot(
        ticker="TSLA", asof_ts=bars[-1].ts, spot=250.0,
        bars_1d=bars, indicators={"rsi_14": 55.0}, atm_greeks={},
        iv_rank=iv_rank, regime=regime, chain=chain,
    )


def test_growth_leaps_opens_in_bull():
    strat = GrowthLEAPS(params={
        "target_delta": 0.70, "min_dte": 180, "max_dte": 365,
        "iv_rank_max": 60, "profit_target_pct": 1.0,
        "stop_loss_mult": 0.50, "min_dte_close": 30,
    })
    snap = _make_snapshot(regime="bull", iv_rank=40.0)
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is not None
    assert signal.intent == "open"
    assert len(signal.legs) == 1
    assert signal.legs[0].kind == "C"
    assert signal.legs[0].side == "long"


def test_growth_leaps_skips_in_bear_with_regime_filter():
    strat = GrowthLEAPS(params={
        "target_delta": 0.70, "min_dte": 180, "max_dte": 365,
        "iv_rank_max": 60, "regime_filter": ["bull", "chop"],
        "profit_target_pct": 1.0, "stop_loss_mult": 0.50,
        "min_dte_close": 30,
    })
    snap = _make_snapshot(regime="bear")
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is None


def test_growth_leaps_skips_high_iv():
    strat = GrowthLEAPS(params={
        "target_delta": 0.70, "min_dte": 180, "max_dte": 365,
        "iv_rank_max": 30, "profit_target_pct": 1.0,
        "stop_loss_mult": 0.50, "min_dte_close": 30,
    })
    snap = _make_snapshot(iv_rank=50.0)
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is None


def test_growth_leaps_skips_when_position_open():
    strat = GrowthLEAPS(params={
        "target_delta": 0.70, "min_dte": 180, "max_dte": 365,
        "iv_rank_max": 60, "profit_target_pct": 1.0,
        "stop_loss_mult": 0.50, "min_dte_close": 30,
    })
    snap = _make_snapshot()
    signal = strat.evaluate(snap, open_positions=[{"id": 1}])
    assert signal is None


def test_growth_leaps_max_loss():
    strat = GrowthLEAPS(params={
        "target_delta": 0.70, "min_dte": 180, "max_dte": 365,
        "iv_rank_max": 60,
    })
    assert strat.max_loss_per_contract() > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_growth_leaps.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement GrowthLEAPS**

```python
# bullbot/strategies/growth_leaps.py
"""GrowthLEAPS — buy long-dated calls for directional growth exposure."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bullbot import config
from bullbot.data.schemas import Leg, Signal
from bullbot.strategies.base import Strategy, StrategySnapshot


class GrowthLEAPS(Strategy):
    CLASS_NAME = "GrowthLEAPS"
    CLASS_VERSION = 1

    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
    ) -> Signal | None:
        if open_positions:
            return None

        regime_filter = self.params.get("regime_filter")
        if regime_filter and snapshot.regime not in regime_filter:
            return None

        iv_rank_max = self.params.get("iv_rank_max", 100)
        if snapshot.iv_rank > iv_rank_max:
            return None

        target_delta = self.params.get("target_delta", 0.70)
        min_dte = self.params.get("min_dte", 180)
        max_dte = self.params.get("max_dte", 365)
        now_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc)

        best = None
        best_delta_diff = float("inf")

        for c in snapshot.chain:
            if c.kind != "C":
                continue
            exp_dt = datetime.strptime(c.expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            dte = (exp_dt - now_dt).days
            if dte < min_dte or dte > max_dte:
                continue
            if c.nbbo_bid <= 0 or c.nbbo_ask <= 0:
                continue

            est_delta = max(0.01, min(0.99, 1.0 - (c.strike - snapshot.spot) / (2 * snapshot.spot)))
            delta_diff = abs(est_delta - target_delta)
            if delta_diff < best_delta_diff:
                best_delta_diff = delta_diff
                best = c

        if best is None:
            return None

        exp_d = datetime.strptime(best.expiry, "%Y-%m-%d").date()
        osi = f"{best.ticker}{exp_d:%y%m%d}C{int(round(best.strike * 1000)):08d}"

        leg = Leg(
            option_symbol=osi, side="long", quantity=1,
            strike=best.strike, expiry=best.expiry, kind="C",
        )

        mid = (best.nbbo_bid + best.nbbo_ask) / 2.0
        max_loss = mid * 100

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[leg],
            max_loss_per_contract=max_loss,
            rationale=f"LEAPS call {best.strike}C exp {best.expiry}, est delta ~{target_delta:.2f}",
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE),
        )

    def max_loss_per_contract(self) -> float:
        return 5000.0
```

- [ ] **Step 4: Register the strategy**

Add to `bullbot/strategies/registry.py`:

```python
from bullbot.strategies.growth_leaps import GrowthLEAPS

# Add to _REGISTRY:
"GrowthLEAPS": GrowthLEAPS,
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_growth_leaps.py -v`
Expected: 5 passed

- [ ] **Step 6: Run full suite**

Run: `pytest tests/ -x -q`
Expected: All pass (including registry tests that check list_all_names)

- [ ] **Step 7: Commit**

```bash
git add bullbot/strategies/growth_leaps.py bullbot/strategies/registry.py tests/unit/test_growth_leaps.py
git commit -m "add GrowthLEAPS strategy: long-dated calls with delta/DTE/IV/regime filters"
```

---

## Task 4: BearPutSpread Strategy Class

**Files:**
- Create: `bullbot/strategies/bear_put_spread.py`
- Modify: `bullbot/strategies/registry.py`
- Create: `tests/unit/test_bear_put_spread.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_bear_put_spread.py
import pytest
from bullbot.strategies.bear_put_spread import BearPutSpread
from bullbot.strategies.base import StrategySnapshot
from bullbot.data.schemas import Bar, OptionContract


def _make_bars(n=60, base_close=250.0):
    return [
        Bar(ticker="TSLA", timeframe="1d", ts=86400 * i,
            open=base_close, high=base_close + 5, low=base_close - 5,
            close=base_close, volume=1000000, source="uw")
        for i in range(n)
    ]


def _make_chain(spot=250.0, expiry="2026-05-16", ts=86400 * 60):
    contracts = []
    for strike in range(200, 300, 5):
        mid = max(0.50, (spot - strike) * 0.4 + 5.0) if strike < spot else max(0.50, 5.0 - (strike - spot) * 0.3)
        contracts.append(OptionContract(
            ticker="TSLA", expiry=expiry, strike=float(strike), kind="P",
            ts=ts, nbbo_bid=max(0.10, mid - 0.30), nbbo_ask=mid + 0.30,
            volume=200, open_interest=1000, iv=0.50,
        ))
    return contracts


def _make_snapshot(regime="bear", iv_rank=60.0):
    bars = _make_bars()
    chain = _make_chain(ts=bars[-1].ts)
    return StrategySnapshot(
        ticker="TSLA", asof_ts=bars[-1].ts, spot=250.0,
        bars_1d=bars, indicators={"rsi_14": 35.0}, atm_greeks={},
        iv_rank=iv_rank, regime=regime, chain=chain,
    )


def test_bear_put_spread_opens_in_bear():
    strat = BearPutSpread(params={
        "dte": 30, "long_delta": 0.40, "width": 10,
        "iv_rank_min": 30, "profit_target_pct": 0.50,
        "stop_loss_mult": 2.0, "min_dte_close": 7,
    })
    snap = _make_snapshot(regime="bear", iv_rank=60.0)
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is not None
    assert signal.intent == "open"
    assert len(signal.legs) == 2
    long_leg = [l for l in signal.legs if l.side == "long"][0]
    short_leg = [l for l in signal.legs if l.side == "short"][0]
    assert long_leg.kind == "P"
    assert short_leg.kind == "P"
    assert long_leg.strike > short_leg.strike


def test_bear_put_spread_skips_low_iv():
    strat = BearPutSpread(params={
        "dte": 30, "long_delta": 0.40, "width": 10,
        "iv_rank_min": 70, "profit_target_pct": 0.50,
        "stop_loss_mult": 2.0, "min_dte_close": 7,
    })
    snap = _make_snapshot(iv_rank=50.0)
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is None


def test_bear_put_spread_respects_regime_filter():
    strat = BearPutSpread(params={
        "dte": 30, "long_delta": 0.40, "width": 10,
        "iv_rank_min": 30, "regime_filter": ["bear"],
        "profit_target_pct": 0.50, "stop_loss_mult": 2.0,
        "min_dte_close": 7,
    })
    snap = _make_snapshot(regime="bull")
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is None


def test_bear_put_spread_max_loss():
    strat = BearPutSpread(params={
        "dte": 30, "long_delta": 0.40, "width": 10,
    })
    assert strat.max_loss_per_contract() > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_bear_put_spread.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement BearPutSpread**

```python
# bullbot/strategies/bear_put_spread.py
"""BearPutSpread — defined-risk bearish debit spread."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bullbot import config
from bullbot.data.schemas import Leg, Signal
from bullbot.strategies.base import Strategy, StrategySnapshot


class BearPutSpread(Strategy):
    CLASS_NAME = "BearPutSpread"
    CLASS_VERSION = 1

    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
    ) -> Signal | None:
        if open_positions:
            return None

        regime_filter = self.params.get("regime_filter")
        if regime_filter and snapshot.regime not in regime_filter:
            return None

        iv_rank_min = self.params.get("iv_rank_min", 0)
        if snapshot.iv_rank < iv_rank_min:
            return None

        target_dte = self.params.get("dte", 30)
        long_delta = self.params.get("long_delta", 0.40)
        width = self.params.get("width", 10)
        now_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc)

        puts = [c for c in snapshot.chain if c.kind == "P" and c.nbbo_bid > 0 and c.nbbo_ask > 0]
        if not puts:
            return None

        best_expiry = None
        best_dte_diff = float("inf")
        for c in puts:
            exp_dt = datetime.strptime(c.expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            dte = (exp_dt - now_dt).days
            if dte < 7:
                continue
            diff = abs(dte - target_dte)
            if diff < best_dte_diff:
                best_dte_diff = diff
                best_expiry = c.expiry

        if best_expiry is None:
            return None

        expiry_puts = sorted(
            [c for c in puts if c.expiry == best_expiry],
            key=lambda c: c.strike,
        )
        if len(expiry_puts) < 2:
            return None

        best_long = None
        best_diff = float("inf")
        for c in expiry_puts:
            est_delta = max(0.01, min(0.99, (snapshot.spot - c.strike) / (2 * snapshot.spot)))
            diff = abs(est_delta - long_delta)
            if diff < best_diff:
                best_diff = diff
                best_long = c

        if best_long is None:
            return None

        short_strike = best_long.strike - width
        short_put = None
        for c in expiry_puts:
            if abs(c.strike - short_strike) < 1.0:
                short_put = c
                break

        if short_put is None:
            return None

        exp_d = datetime.strptime(best_expiry, "%Y-%m-%d").date()

        def osi(strike, kind):
            return f"TSLA{exp_d:%y%m%d}{kind}{int(round(strike * 1000)):08d}"

        long_leg = Leg(
            option_symbol=osi(best_long.strike, "P"), side="long", quantity=1,
            strike=best_long.strike, expiry=best_expiry, kind="P",
        )
        short_leg = Leg(
            option_symbol=osi(short_put.strike, "P"), side="short", quantity=1,
            strike=short_put.strike, expiry=best_expiry, kind="P",
        )

        net_debit = ((best_long.nbbo_ask + best_long.nbbo_bid) / 2
                     - (short_put.nbbo_bid + short_put.nbbo_ask) / 2)
        max_loss = net_debit * 100

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[long_leg, short_leg],
            max_loss_per_contract=max(max_loss, width * 100),
            rationale=f"Bear put spread {best_long.strike}/{short_put.strike}P exp {best_expiry}",
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE),
        )

    def max_loss_per_contract(self) -> float:
        return self.params.get("width", 10) * 100
```

- [ ] **Step 4: Register the strategy**

Add to `bullbot/strategies/registry.py`:

```python
from bullbot.strategies.bear_put_spread import BearPutSpread

# Add to _REGISTRY:
"BearPutSpread": BearPutSpread,
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_bear_put_spread.py -v`
Expected: 4 passed

- [ ] **Step 6: Run full suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add bullbot/strategies/bear_put_spread.py bullbot/strategies/registry.py tests/unit/test_bear_put_spread.py
git commit -m "add BearPutSpread strategy: defined-risk bearish debit spread with regime/IV filters"
```

---

## Task 5: GrowthEquity Strategy Class

**Files:**
- Create: `bullbot/strategies/growth_equity.py`
- Modify: `bullbot/strategies/registry.py`
- Create: `tests/unit/test_growth_equity.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_growth_equity.py
import pytest
from bullbot.strategies.growth_equity import GrowthEquity
from bullbot.strategies.base import StrategySnapshot
from bullbot.data.schemas import Bar


def _make_bars(n=60, base_close=250.0):
    return [
        Bar(ticker="TSLA", timeframe="1d", ts=86400 * i,
            open=base_close, high=base_close + 5, low=base_close - 5,
            close=base_close, volume=1000000, source="uw")
        for i in range(n)
    ]


def _make_snapshot(regime="bull"):
    bars = _make_bars()
    return StrategySnapshot(
        ticker="TSLA", asof_ts=bars[-1].ts, spot=250.0,
        bars_1d=bars, indicators={"rsi_14": 55.0}, atm_greeks={},
        iv_rank=40.0, regime=regime, chain=[],
    )


def test_growth_equity_opens_in_bull():
    strat = GrowthEquity(params={"regime_filter": ["bull", "chop"]})
    snap = _make_snapshot(regime="bull")
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is not None
    assert signal.intent == "open"
    assert signal.strategy_class == "GrowthEquity"


def test_growth_equity_skips_in_bear_with_filter():
    strat = GrowthEquity(params={"regime_filter": ["bull"]})
    snap = _make_snapshot(regime="bear")
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is None


def test_growth_equity_no_filter_opens_any_regime():
    strat = GrowthEquity(params={})
    snap = _make_snapshot(regime="bear")
    signal = strat.evaluate(snap, open_positions=[])
    assert signal is not None


def test_growth_equity_skips_when_position_open():
    strat = GrowthEquity(params={})
    snap = _make_snapshot()
    signal = strat.evaluate(snap, open_positions=[{"id": 1}])
    assert signal is None


def test_growth_equity_max_loss_uses_stop():
    strat = GrowthEquity(params={"stop_loss_pct": 0.15})
    assert strat.max_loss_per_contract() > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_growth_equity.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement GrowthEquity**

```python
# bullbot/strategies/growth_equity.py
"""GrowthEquity — buy shares for long-term growth."""
from __future__ import annotations

from typing import Any

from bullbot.data.schemas import Leg, Signal
from bullbot.strategies.base import Strategy, StrategySnapshot


class GrowthEquity(Strategy):
    CLASS_NAME = "GrowthEquity"
    CLASS_VERSION = 1

    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
    ) -> Signal | None:
        if open_positions:
            return None

        regime_filter = self.params.get("regime_filter")
        if regime_filter and snapshot.regime not in regime_filter:
            return None

        stop_loss_pct = self.params.get("stop_loss_pct", 0.10)
        max_loss = snapshot.spot * stop_loss_pct

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[],
            max_loss_per_contract=max_loss,
            rationale=f"Buy {snapshot.ticker} shares at {snapshot.spot:.2f}, stop {stop_loss_pct:.0%}",
            profit_target_pct=self.params.get("profit_target_pct"),
            stop_loss_mult=self.params.get("stop_loss_mult"),
            min_dte_close=None,
        )

    def max_loss_per_contract(self) -> float:
        return self.params.get("stop_loss_pct", 0.10) * 250.0 * 100
```

Note: GrowthEquity uses an empty `legs` list. The engine step will need to handle equity positions differently — this is deferred to Task 9 (engine adaptation). For now, the strategy class produces valid Signals that the evolver can evaluate.

- [ ] **Step 4: Register the strategy**

Add to `bullbot/strategies/registry.py`:

```python
from bullbot.strategies.growth_equity import GrowthEquity

# Add to _REGISTRY:
"GrowthEquity": GrowthEquity,
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_growth_equity.py -v`
Expected: 5 passed

- [ ] **Step 6: Run full suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add bullbot/strategies/growth_equity.py bullbot/strategies/registry.py tests/unit/test_growth_equity.py
git commit -m "add GrowthEquity strategy: regime-filtered share buying for growth"
```

---

## Task 6: Extend Walk-Forward for Growth Metrics

**Files:**
- Modify: `bullbot/backtest/walkforward.py`
- Modify: `tests/integration/test_walkforward.py`

- [ ] **Step 1: Extend BacktestMetrics with growth fields**

In `bullbot/backtest/walkforward.py`, update the `BacktestMetrics` dataclass:

```python
@dataclass
class BacktestMetrics:
    pf_is: float
    pf_oos: float
    sharpe_is: float
    max_dd_pct: float
    trade_count: int
    regime_breakdown: dict[str, float] = field(default_factory=dict)
    fold_metrics: list[FoldMetrics] = field(default_factory=list)
    # Growth metrics (populated only for growth tickers)
    cagr_oos: float | None = None
    sortino_oos: float | None = None
```

- [ ] **Step 2: Make compute_folds accept category-aware params**

Update `run_walkforward()` in `bullbot/backtest/walkforward.py` to read the ticker's category from config and use growth WF params when appropriate:

```python
def run_walkforward(
    conn: sqlite3.Connection,
    strategy: Strategy,
    strategy_id: int,
    ticker: str,
) -> BacktestMetrics:
    category = config.TICKER_CATEGORY.get(ticker, "income")
    if category == "growth":
        window_months = config.GROWTH_WF_WINDOW_MONTHS
        step_days = config.GROWTH_WF_STEP_DAYS
    else:
        window_months = config.WF_WINDOW_MONTHS
        step_days = config.WF_STEP_DAYS

    total_days = window_months * 30
    folds = compute_folds(
        total_days=total_days,
        train_frac=config.WF_TRAIN_FRAC,
        step_days=step_days,
        min_folds=config.WF_MIN_FOLDS,
        max_folds=config.WF_MAX_FOLDS,
    )
    # ... rest of existing logic ...
```

- [ ] **Step 3: Compute CAGR and Sortino for growth tickers in aggregate()**

After the existing `aggregate()` logic, add growth metric computation:

```python
def aggregate(fold_metrics: list[FoldMetrics], category: str = "income") -> BacktestMetrics:
    # ... existing pf_oos, trade_count, max_dd logic ...

    metrics = BacktestMetrics(
        pf_is=pf_is, pf_oos=pf_oos, sharpe_is=0.0,
        max_dd_pct=max_dd, trade_count=total_oos,
        fold_metrics=fold_metrics,
    )

    if category == "growth" and all_oos_pnls:
        from bullbot.features.indicators import cagr as calc_cagr, sortino as calc_sortino
        starting = 10000.0
        equity_curve = [starting]
        for pnl in all_oos_pnls:
            equity_curve.append(equity_curve[-1] + pnl)
        total_oos_days = sum(
            (fm.test_end - fm.test_start) / 86400
            for fm in fold_metrics
            if hasattr(fm, 'test_end')
        ) or len(all_oos_pnls) * 30
        metrics.cagr_oos = calc_cagr(equity_curve, days=int(total_oos_days))
        returns = [pnl / max(eq, 1.0) for pnl, eq in zip(all_oos_pnls, equity_curve[:-1])]
        metrics.sortino_oos = calc_sortino(returns, risk_free_rate=config.RISK_FREE_RATE / 252)

    return metrics
```

Note: `all_oos_pnls` needs to be collected during aggregation. This list is the chronological sequence of all OOS PnL values across folds. Add it to the aggregation loop where `total_oos` is computed.

- [ ] **Step 4: Write tests for growth metric aggregation**

Add to `tests/integration/test_walkforward.py`:

```python
def test_aggregate_growth_computes_cagr_and_sortino():
    from bullbot.backtest.walkforward import FoldMetrics, aggregate
    folds = [
        FoldMetrics(pf_is=2.0, pf_oos=1.8, trade_count_is=10, trade_count_oos=5, max_dd_pct=0.05),
        FoldMetrics(pf_is=1.5, pf_oos=1.3, trade_count_is=8, trade_count_oos=4, max_dd_pct=0.08),
    ]
    result = aggregate(folds, category="growth")
    assert result.cagr_oos is not None
    assert result.sortino_oos is not None


def test_aggregate_income_skips_growth_metrics():
    from bullbot.backtest.walkforward import FoldMetrics, aggregate
    folds = [
        FoldMetrics(pf_is=2.0, pf_oos=1.8, trade_count_is=10, trade_count_oos=5, max_dd_pct=0.05),
    ]
    result = aggregate(folds, category="income")
    assert result.cagr_oos is None
    assert result.sortino_oos is None
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/integration/test_walkforward.py -v`
Expected: All pass

- [ ] **Step 6: Run full suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add bullbot/backtest/walkforward.py tests/integration/test_walkforward.py
git commit -m "extend walk-forward for growth: category-aware folds, CAGR and Sortino metrics"
```

---

## Task 7: Growth Gate in Plateau Classifier

**Files:**
- Modify: `bullbot/evolver/plateau.py`
- Modify: `tests/unit/test_plateau.py`

- [ ] **Step 1: Extend _MetricsLike protocol for growth fields**

In `bullbot/evolver/plateau.py`:

```python
class _MetricsLike(Protocol):
    pf_is: float
    pf_oos: float
    trade_count: int
    cagr_oos: float | None
    sortino_oos: float | None
    max_dd_pct: float
```

- [ ] **Step 2: Add category parameter to classify()**

```python
def classify(state: _StateLike, metrics: _MetricsLike, category: str = "income") -> ClassifyResult:
    if category == "growth":
        return _classify_growth(state, metrics)
    # ... existing income logic unchanged ...
```

- [ ] **Step 3: Implement _classify_growth()**

```python
def _classify_growth(state: _StateLike, metrics: _MetricsLike) -> ClassifyResult:
    """Growth gate: CAGR, Sortino, max drawdown, trade count."""
    cagr = metrics.cagr_oos if metrics.cagr_oos is not None else 0.0
    sortino = metrics.sortino_oos if metrics.sortino_oos is not None else 0.0
    dd = metrics.max_dd_pct

    passed_gate = (
        cagr >= config.GROWTH_EDGE_CAGR_MIN
        and sortino >= config.GROWTH_EDGE_SORTINO_MIN
        and dd <= config.GROWTH_EDGE_MAX_DD_PCT
        and metrics.trade_count >= config.GROWTH_EDGE_TRADE_COUNT_MIN
    )

    if passed_gate:
        return ClassifyResult(
            verdict="edge_found",
            improved=cagr > state.best_pf_oos + config.PLATEAU_IMPROVEMENT_MIN,
            new_plateau_counter=0,
            new_best_pf_oos=max(state.best_pf_oos, cagr),
        )

    improved = cagr > state.best_pf_oos + config.PLATEAU_IMPROVEMENT_MIN
    new_best = max(state.best_pf_oos, cagr)

    if improved:
        new_plateau = 0
    else:
        new_plateau = state.plateau_counter + 1

    if state.iteration_count + 1 >= config.ITERATION_CAP:
        return ClassifyResult(
            verdict="no_edge", improved=improved,
            new_plateau_counter=new_plateau, new_best_pf_oos=new_best,
        )

    if new_plateau >= config.PLATEAU_COUNTER_MAX:
        return ClassifyResult(
            verdict="no_edge", improved=improved,
            new_plateau_counter=new_plateau, new_best_pf_oos=new_best,
        )

    return ClassifyResult(
        verdict="continue", improved=improved,
        new_plateau_counter=new_plateau, new_best_pf_oos=new_best,
    )
```

- [ ] **Step 4: Write growth gate tests**

Add to `tests/unit/test_plateau.py`:

```python
@dataclass
class FakeGrowthMetrics:
    pf_is: float = 0.0
    pf_oos: float = 0.0
    trade_count: int = 10
    cagr_oos: float | None = 0.25
    sortino_oos: float | None = 1.5
    max_dd_pct: float = 0.20


def test_growth_edge_found_when_all_gates_pass():
    state = FakeState(iteration_count=3, plateau_counter=1, best_pf_oos=0.10)
    metrics = FakeGrowthMetrics(cagr_oos=0.25, sortino_oos=1.5, max_dd_pct=0.20, trade_count=8)
    result = plateau.classify(state, metrics, category="growth")
    assert result.verdict == "edge_found"


def test_growth_no_edge_low_cagr():
    state = FakeState(iteration_count=3, plateau_counter=2, best_pf_oos=0.15)
    metrics = FakeGrowthMetrics(cagr_oos=0.10, sortino_oos=1.5, max_dd_pct=0.20, trade_count=8)
    result = plateau.classify(state, metrics, category="growth")
    assert result.verdict != "edge_found"


def test_growth_no_edge_high_drawdown():
    state = FakeState(iteration_count=3, plateau_counter=2, best_pf_oos=0.15)
    metrics = FakeGrowthMetrics(cagr_oos=0.30, sortino_oos=2.0, max_dd_pct=0.40, trade_count=8)
    result = plateau.classify(state, metrics, category="growth")
    assert result.verdict != "edge_found"


def test_growth_uses_cagr_for_plateau_tracking():
    state = FakeState(iteration_count=3, plateau_counter=0, best_pf_oos=0.15)
    metrics = FakeGrowthMetrics(cagr_oos=0.30, sortino_oos=0.5, trade_count=3)
    result = plateau.classify(state, metrics, category="growth")
    assert result.new_best_pf_oos == 0.30
    assert result.improved is True
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_plateau.py -v`
Expected: All pass

- [ ] **Step 6: Run full suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add bullbot/evolver/plateau.py tests/unit/test_plateau.py
git commit -m "add growth gate to plateau classifier: CAGR, Sortino, max drawdown thresholds"
```

---

## Task 8: Category-Aware Position Sizer

**Files:**
- Modify: `bullbot/engine/position_sizer.py`
- Create: `tests/unit/test_category_sizer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_category_sizer.py
import pytest
from bullbot.engine import position_sizer


def test_income_sizes_against_income_pool():
    # Bull regime: growth_frac=0.40, income pool = 60% of 50k = 30k
    # 2% of 30k = $600 risk budget, max_loss=500 -> 1 contract
    result = position_sizer.size_position(
        equity=50_000, max_loss_per_contract=500, category="income", regime="bull",
    )
    assert result == 1


def test_growth_sizes_against_growth_pool():
    # Bull regime: growth_frac=0.40, growth pool = 40% of 50k = 20k
    # 2% of 20k = $400 risk budget, max_loss=300 -> 1 contract
    result = position_sizer.size_position(
        equity=50_000, max_loss_per_contract=300, category="growth", regime="bull",
    )
    assert result == 1


def test_growth_pool_shrinks_in_bear():
    # Bear regime: growth_frac=0.10, growth pool = 10% of 50k = 5k
    # 2% of 5k = $100 risk budget, max_loss=300 -> 0 contracts
    result = position_sizer.size_position(
        equity=50_000, max_loss_per_contract=300, category="growth", regime="bear",
    )
    assert result == 0


def test_default_category_is_income():
    r1 = position_sizer.size_position(equity=50_000, max_loss_per_contract=500)
    r2 = position_sizer.size_position(equity=50_000, max_loss_per_contract=500, category="income", regime="bull")
    assert r1 == r2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_category_sizer.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'category'`

- [ ] **Step 3: Update position_sizer**

Replace `bullbot/engine/position_sizer.py`:

```python
"""Position sizer — contract count from equity, risk budget, and capital pool."""
from __future__ import annotations

from bullbot import config

_GROWTH_FRAC = {
    "bull": config.GROWTH_FRAC_BULL,
    "chop": config.GROWTH_FRAC_CHOP,
    "bear": config.GROWTH_FRAC_BEAR,
}


def size_position(
    equity: float,
    max_loss_per_contract: float,
    category: str = "income",
    regime: str = "bull",
) -> int:
    """Return the contract count for this position, or 0 if it can't be sized."""
    if max_loss_per_contract <= 0:
        return 0

    growth_frac = _GROWTH_FRAC.get(regime, config.GROWTH_FRAC_CHOP)
    if category == "growth":
        pool = equity * growth_frac
    else:
        pool = equity * (1.0 - growth_frac)

    risk_budget = config.POSITION_RISK_FRAC * pool
    raw = int(risk_budget // max_loss_per_contract)
    return max(0, min(raw, config.MAX_POSITIONS_PER_TICKER))
```

- [ ] **Step 4: Update callers of size_position in step.py**

In `bullbot/engine/step.py`, the `step()` function calls `position_sizer.size_position()`. Update it to pass category and regime:

```python
# In step(), around line 222, after signal is generated:
category = config.TICKER_CATEGORY.get(ticker, "income")
contracts = position_sizer.size_position(
    equity=equity,
    max_loss_per_contract=signal.max_loss_per_contract,
    category=category,
    regime=snap.regime,
)
```

- [ ] **Step 5: Update existing position_sizer tests**

In `tests/unit/test_position_sizer.py`, existing tests call `size_position(equity, max_loss)` which still works via defaults. Verify with:

Run: `pytest tests/unit/test_position_sizer.py -v`
Expected: All pass (defaults maintain backward compatibility)

- [ ] **Step 6: Run all tests**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add bullbot/engine/position_sizer.py bullbot/engine/step.py tests/unit/test_category_sizer.py
git commit -m "category-aware position sizer: regime-driven growth/income capital pool split"
```

---

## Task 9: Proposer Category-Aware Menu

**Files:**
- Modify: `bullbot/evolver/proposer.py`

- [ ] **Step 1: Add category parameter to system prompt builder**

In `bullbot/evolver/proposer.py`, update the system prompt to include category guidance:

```python
_GROWTH_GUIDANCE = """
This ticker is categorized as GROWTH. Emphasize directional strategies with longer
holding periods. Consider both bullish (GrowthLEAPS, LongCall) and bearish
(BearPutSpread, LongPut) strategies depending on the regime context.
Growth strategies can use regime_filter (list of regimes to trade in, e.g. ["bull", "chop"]).
"""

_INCOME_GUIDANCE = """
This ticker is categorized as INCOME. Focus on premium-selling strategies
(PutCreditSpread, CallCreditSpread, IronCondor, CashSecuredPut) that profit from
time decay.
"""
```

- [ ] **Step 2: Pass category into propose() and system prompt**

Update `propose()` to accept `category` parameter and inject guidance:

```python
def propose(
    client: Any,
    snapshot: StrategySnapshot,
    history: list[dict],
    best_strategy_id: str | None,
    category: str = "income",
) -> Proposal:
    guidance = _GROWTH_GUIDANCE if category == "growth" else _INCOME_GUIDANCE
    system_prompt = _SYSTEM_PROMPT.format(
        strategy_names=", ".join(registry.list_all_names())
    ) + guidance
    # ... rest unchanged ...
```

- [ ] **Step 3: Update iteration.py to pass category**

In `bullbot/evolver/iteration.py`, update the `propose()` call to pass category:

```python
category = config.TICKER_CATEGORY.get(ticker, "income")
proposal = proposer.propose(client, snapshot, history, best_id, category=category)
```

Also pass category to `plateau.classify()` and `walkforward.run_walkforward()` calls.

- [ ] **Step 4: Run full suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add bullbot/evolver/proposer.py bullbot/evolver/iteration.py
git commit -m "category-aware proposer: growth/income guidance in LLM prompt"
```

---

## Task 10: TSLA Data Backfill (Bars)

**Files:**
- Uses existing: `scripts/backfill_and_run.py`

- [ ] **Step 1: Backfill 5 years of TSLA daily bars**

Growth strategies need 5 years (~1,260 bars) for the walk-forward window. The existing backfill script supports `--bars-only`:

```bash
python scripts/backfill_and_run.py --bars-only --ticker TSLA
```

If the script doesn't support a `--ticker` flag for individual tickers, add TSLA bars via the UW fetcher directly or extend the script. Verify:

```python
python3 -c "
import sqlite3
conn = sqlite3.connect('cache/bullbot.db')
rows = conn.execute('SELECT COUNT(*), MIN(ts), MAX(ts) FROM bars WHERE ticker=\"TSLA\"').fetchone()
print(f'TSLA bars: {rows[0]}, range: {rows[1]} - {rows[2]}')
"
```

Expected: 1,200+ bars

- [ ] **Step 2: Commit if any script changes were needed**

```bash
git add scripts/backfill_and_run.py
git commit -m "backfill TSLA daily bars for growth strategy evaluation"
```

---

## Task 11: Wire Category Through Evolver Pipeline

**Files:**
- Modify: `bullbot/evolver/iteration.py`
- Modify: `bullbot/backtest/walkforward.py`

This task ensures the full pipeline reads category and passes it through:
`iteration.run()` → `walkforward.run_walkforward()` → `aggregate()` → `plateau.classify()`

- [ ] **Step 1: Update iteration.py to pass category everywhere**

In `bullbot/evolver/iteration.py`, in the `run()` function:

```python
category = config.TICKER_CATEGORY.get(ticker, "income")

# Pass to walkforward
metrics = walkforward.run_walkforward(conn, strategy, strategy_id, ticker)
# (walkforward already reads category internally from config)

# Pass to plateau
result = plateau.classify(state_obj, metrics, category=category)

# Pass to proposer
proposal = proposer.propose(client, snapshot, history, best_id, category=category)
```

- [ ] **Step 2: Ensure walkforward passes category to aggregate**

In `bullbot/backtest/walkforward.py`, `run_walkforward()`:

```python
category = config.TICKER_CATEGORY.get(ticker, "income")
# ... fold computation uses category-aware params (already done in Task 6) ...
metrics = aggregate(fold_results, category=category)
```

- [ ] **Step 3: Run full suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add bullbot/evolver/iteration.py bullbot/backtest/walkforward.py
git commit -m "wire category through evolver pipeline: iteration -> walkforward -> plateau"
```

---

## Task 12: Run TSLA Growth Evolver

**Files:**
- Uses existing pipeline

- [ ] **Step 1: Reset TSLA state and run evolver**

```bash
python3 -c "
import sqlite3; conn = sqlite3.connect('cache/bullbot.db')
conn.execute(\"DELETE FROM ticker_state WHERE ticker='TSLA'\")
conn.commit(); conn.close()
print('TSLA state reset')
"

python scripts/backfill_and_run.py --evolver-only --ticker TSLA --iterations 20
```

- [ ] **Step 2: Check results**

```python
python3 -c "
import sqlite3, json
conn = sqlite3.connect('cache/bullbot.db')
conn.row_factory = sqlite3.Row
row = conn.execute('SELECT * FROM ticker_state WHERE ticker=\"TSLA\"').fetchone()
if row:
    print(f'phase={row[\"phase\"]} iterations={row[\"iteration_count\"]} best_pf_oos={row[\"best_pf_oos\"]}')
proposals = conn.execute('''
    SELECT ep.iteration, s.class_name, ep.pf_oos, ep.trade_count, ep.passed_gate
    FROM evolver_proposals ep JOIN strategies s ON ep.strategy_id = s.id
    WHERE ep.ticker=\"TSLA\" ORDER BY ep.iteration
''').fetchall()
for p in proposals:
    print(f'  iter={p[0]} {p[1]} pf_oos={p[2]} trades={p[3]} gate={\"PASS\" if p[4] else \"FAIL\"}')
"
```

- [ ] **Step 3: Document results in commit message**

```bash
git add -A
git commit -m "TSLA growth evolver: initial run results [document what happened]"
```
