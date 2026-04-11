# Market Regime Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-stage market regime agent (quantitative signals + Sonnet synthesis) that provides the evolver proposer with rich market and per-ticker context before each discovery cycle.

**Architecture:** A daily regime refresh step runs before the evolver loop. It fetches VIX + sector ETF bars, computes quantitative signals (pure Python), synthesizes natural-language briefs via Sonnet, and caches them in a new `regime_briefs` table. The proposer prompt is extended with `=== Market Regime Analysis ===` and `=== Ticker Analysis ===` blocks. IV rank computation replaces the hardcoded 50.0.

**Tech Stack:** Python 3.11+, SQLite (WAL mode, STRICT tables), Anthropic SDK (Sonnet 4.6), pytest

**Spec:** `docs/superpowers/specs/2026-04-10-market-regime-agent-design.md`

---

## File Structure

### New files
| File | Purpose |
|------|---------|
| `bullbot/features/regime_signals.py` | Pure-function quantitative signal computation (MarketSignals, TickerSignals dataclasses + compute functions) |
| `bullbot/features/regime_agent.py` | LLM synthesis of signals into briefs, caching in regime_briefs table, cost tracking |
| `tests/unit/test_regime_signals.py` | Unit tests for all signal computations |
| `tests/unit/test_regime_agent.py` | Unit tests for synthesis prompts, caching, fallback logic |
| `tests/integration/test_regime_integration.py` | End-to-end regime refresh + evolver iteration with regime context |

### Modified files
| File | Change |
|------|--------|
| `bullbot/config.py` | Add REGIME_DATA_TICKERS, REGIME_SYNTHESIS_MODEL, TICKER_SECTOR_MAP, max token constants |
| `bullbot/db/schema.sql` | Add `regime_briefs` table |
| `bullbot/strategies/base.py` | Add `market_brief` and `ticker_brief` fields (default `""`) to StrategySnapshot |
| `bullbot/engine/step.py` | Compute real IV rank; attach briefs to snapshot |
| `bullbot/evolver/proposer.py` | Add regime context blocks to `build_user_prompt()` |
| `bullbot/scheduler.py` | Call regime refresh before evolver loop |
| `bullbot/evolver/iteration.py` | Pass briefs through to snapshot (minor wiring) |

---

### Task 1: Config — add regime agent constants

**Files:**
- Modify: `bullbot/config.py:76-86`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_config.py — append to existing file

def test_regime_config_constants_exist():
    from bullbot import config
    assert isinstance(config.REGIME_DATA_TICKERS, list)
    assert "VIX" in config.REGIME_DATA_TICKERS
    assert len(config.REGIME_DATA_TICKERS) == 14
    assert config.REGIME_SYNTHESIS_MODEL == "claude-sonnet-4-6"
    assert config.REGIME_MARKET_BRIEF_MAX_TOKENS == 300
    assert config.REGIME_TICKER_BRIEF_MAX_TOKENS == 200
    assert isinstance(config.TICKER_SECTOR_MAP, dict)
    assert config.TICKER_SECTOR_MAP["AAPL"] == "XLK"
    assert config.TICKER_SECTOR_MAP["SPY"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py::test_regime_config_constants_exist -v`
Expected: FAIL with `AttributeError: module 'bullbot.config' has no attribute 'REGIME_DATA_TICKERS'`

- [ ] **Step 3: Add regime constants to config.py**

Add after line 86 (`RISK_FREE_RATE = 0.045`) in `bullbot/config.py`:

```python
# --- Regime agent ---

REGIME_DATA_TICKERS: list[str] = [
    "VIX",   # Volatility index (use UVXY as fallback if UW doesn't serve VIX)
    "XLK",   # Technology
    "XLF",   # Financials
    "XLE",   # Energy
    "XLV",   # Healthcare
    "XLI",   # Industrials
    "XLC",   # Communication services
    "XLY",   # Consumer discretionary
    "XLP",   # Consumer staples
    "XLU",   # Utilities
    "XLRE",  # Real estate
    "XLB",   # Materials
    "TLT",   # Treasury bonds (rate/risk proxy)
    "HYG",   # High-yield credit (risk appetite proxy)
]

REGIME_SYNTHESIS_MODEL = "claude-sonnet-4-6"
REGIME_MARKET_BRIEF_MAX_TOKENS = 300
REGIME_TICKER_BRIEF_MAX_TOKENS = 200

TICKER_SECTOR_MAP: dict[str, str | None] = {
    "SPY": None,    # Index — uses breadth_score instead
    "QQQ": "XLK",
    "IWM": None,    # Index
    "AAPL": "XLK",
    "MSFT": "XLK",
    "NVDA": "XLK",
    "TSLA": "XLY",
    "AMD": "XLK",
    "META": "XLC",
    "GOOGL": "XLC",
}

# Sector ETFs used for breadth calculation (all 11 GICS sectors)
SECTOR_ETFS: list[str] = [
    "XLK", "XLF", "XLE", "XLV", "XLI",
    "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_config.py::test_regime_config_constants_exist -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bullbot/config.py tests/unit/test_config.py
git commit -m "stage2(T1): add regime agent config constants"
```

---

### Task 2: Schema — add `regime_briefs` table

**Files:**
- Modify: `bullbot/db/schema.sql`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_regime_schema.py — new file

import sqlite3
from bullbot.db import migrations


def _fresh_conn():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)
    return conn


def test_regime_briefs_table_exists():
    conn = _fresh_conn()
    # Should not raise
    conn.execute("SELECT * FROM regime_briefs LIMIT 0")


def test_regime_briefs_insert_and_unique():
    conn = _fresh_conn()
    conn.execute(
        "INSERT INTO regime_briefs (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("market", 1744243200, '{"vix": 15}', "Low vol regime.", "claude-sonnet-4-6", 0.003, "llm", 1744243200),
    )
    # Duplicate (scope, ts) should fail
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO regime_briefs (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("market", 1744243200, '{"vix": 16}', "Different.", "claude-sonnet-4-6", 0.003, "llm", 1744243200),
        )


def test_regime_briefs_different_scope_same_ts():
    conn = _fresh_conn()
    conn.execute(
        "INSERT INTO regime_briefs (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("market", 1744243200, '{}', "Market brief.", "claude-sonnet-4-6", 0.003, "llm", 1744243200),
    )
    conn.execute(
        "INSERT INTO regime_briefs (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("AAPL", 1744243200, '{}', "AAPL brief.", "claude-sonnet-4-6", 0.003, "llm", 1744243200),
    )
    rows = conn.execute("SELECT COUNT(*) FROM regime_briefs").fetchone()[0]
    assert rows == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_regime_schema.py -v`
Expected: FAIL with `sqlite3.OperationalError: no such table: regime_briefs`

- [ ] **Step 3: Add regime_briefs table to schema.sql**

Append before the final `iteration_failures` table in `bullbot/db/schema.sql`:

```sql
-- ---------------------------------------------------------------------------
-- regime_briefs: cached market and per-ticker regime analysis
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS regime_briefs (
    id              INTEGER PRIMARY KEY,
    scope           TEXT NOT NULL,       -- 'market' or ticker symbol (e.g. 'AAPL')
    ts              INTEGER NOT NULL,    -- trading day as midnight UTC epoch
    signals_json    TEXT NOT NULL,       -- raw quantitative signals (JSON)
    brief_text      TEXT NOT NULL,       -- LLM-synthesized brief
    model           TEXT NOT NULL,       -- model used (e.g. 'claude-sonnet-4-6')
    cost_usd        REAL NOT NULL,       -- LLM cost for this synthesis
    source          TEXT NOT NULL DEFAULT 'llm',  -- 'llm' or 'fallback'
    created_at      INTEGER NOT NULL,
    UNIQUE(scope, ts)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_regime_briefs_scope_ts ON regime_briefs (scope, ts DESC);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_regime_schema.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `pytest -x -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add bullbot/db/schema.sql tests/unit/test_regime_schema.py
git commit -m "stage2(T2): add regime_briefs table to schema"
```

---

### Task 3: StrategySnapshot — add `market_brief` and `ticker_brief` fields

**Files:**
- Modify: `bullbot/strategies/base.py:19-30`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_strategies_base.py — append to existing file

def test_snapshot_has_brief_fields_with_defaults():
    from bullbot.strategies.base import StrategySnapshot
    snap = StrategySnapshot(
        ticker="SPY",
        asof_ts=1000000,
        spot=500.0,
        bars_1d=[],
        indicators={},
        atm_greeks={},
        iv_rank=50.0,
        regime="bull",
        chain=[],
    )
    # New fields should default to empty string
    assert snap.market_brief == ""
    assert snap.ticker_brief == ""


def test_snapshot_accepts_brief_fields():
    from bullbot.strategies.base import StrategySnapshot
    snap = StrategySnapshot(
        ticker="SPY",
        asof_ts=1000000,
        spot=500.0,
        bars_1d=[],
        indicators={},
        atm_greeks={},
        iv_rank=50.0,
        regime="bull",
        chain=[],
        market_brief="Low vol regime.",
        ticker_brief="SPY trending up.",
    )
    assert snap.market_brief == "Low vol regime."
    assert snap.ticker_brief == "SPY trending up."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_strategies_base.py::test_snapshot_has_brief_fields_with_defaults -v`
Expected: FAIL with `TypeError: StrategySnapshot.__init__() got an unexpected keyword argument` or missing attribute

- [ ] **Step 3: Add fields to StrategySnapshot**

In `bullbot/strategies/base.py`, modify the `StrategySnapshot` dataclass to add the two new fields with defaults at the end:

```python
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
    chain: list[OptionContract]
    market_brief: str = ""   # Daily market regime brief (empty during backtesting)
    ticker_brief: str = ""   # Daily per-ticker brief (empty during backtesting)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_strategies_base.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to verify backward compatibility**

Run: `pytest -x -q`
Expected: All tests pass (existing code that constructs StrategySnapshot without the new fields still works due to defaults)

- [ ] **Step 6: Commit**

```bash
git add bullbot/strategies/base.py tests/unit/test_strategies_base.py
git commit -m "stage2(T3): add market_brief and ticker_brief to StrategySnapshot"
```

---

### Task 4: Regime signals — `MarketSignals` computation

**Files:**
- Create: `bullbot/features/regime_signals.py`
- Create: `tests/unit/test_regime_signals.py`

- [ ] **Step 1: Write failing tests for MarketSignals**

```python
# tests/unit/test_regime_signals.py

import math
from bullbot.features.regime_signals import (
    MarketSignals,
    TickerSignals,
    compute_market_signals,
    compute_ticker_signals,
)


def _make_bars_rows(closes, ticker="VIX"):
    """Build list of dicts mimicking sqlite3.Row for bars table.
    Starts at ts=1000 and increments by 86400 (1 day) per bar.
    """
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            "ticker": ticker,
            "timeframe": "1d",
            "ts": 1000 + i * 86400,
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "volume": 1000000,
        })
    return rows


def test_market_signals_vix_percentile():
    """VIX at 20 with 252 days of history between 10 and 30 → percentile ~50%."""
    vix_closes = [10.0 + (20.0 * i / 251) for i in range(252)]  # 10 to 30 linearly
    signals = compute_market_signals(
        vix_bars=_make_bars_rows(vix_closes, "VIX"),
        spy_bars=_make_bars_rows([400.0 + i * 0.5 for i in range(252)], "SPY"),
        sector_bars={etf: _make_bars_rows([100.0 + i * 0.1 for i in range(252)], etf)
                     for etf in ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB"]},
        hyg_bars=_make_bars_rows([80.0 + i * 0.05 for i in range(252)], "HYG"),
        tlt_bars=_make_bars_rows([100.0 - i * 0.02 for i in range(252)], "TLT"),
    )
    assert isinstance(signals, MarketSignals)
    assert 10.0 <= signals.vix_level <= 30.0
    assert 0.0 <= signals.vix_percentile <= 100.0


def test_market_signals_breadth_all_above_sma50():
    """All 11 sectors trending up → breadth_score = 100."""
    # Each sector rises steadily so current close is above SMA(50)
    sector_bars = {}
    for etf in ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB"]:
        sector_bars[etf] = _make_bars_rows([100.0 + i * 0.5 for i in range(252)], etf)
    signals = compute_market_signals(
        vix_bars=_make_bars_rows([20.0] * 252, "VIX"),
        spy_bars=_make_bars_rows([400.0 + i * 0.5 for i in range(252)], "SPY"),
        sector_bars=sector_bars,
        hyg_bars=_make_bars_rows([80.0] * 252, "HYG"),
        tlt_bars=_make_bars_rows([100.0] * 252, "TLT"),
    )
    assert signals.breadth_score == 100.0


def test_market_signals_spy_trend_up():
    """SPY rising above SMA50 and SMA200 → 'up'."""
    spy_closes = [300.0 + i * 0.8 for i in range(252)]
    signals = compute_market_signals(
        vix_bars=_make_bars_rows([20.0] * 252, "VIX"),
        spy_bars=_make_bars_rows(spy_closes, "SPY"),
        sector_bars={etf: _make_bars_rows([100.0] * 252, etf)
                     for etf in ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB"]},
        hyg_bars=_make_bars_rows([80.0] * 252, "HYG"),
        tlt_bars=_make_bars_rows([100.0] * 252, "TLT"),
    )
    assert signals.spy_trend == "up"


def test_market_signals_risk_appetite():
    """HYG rising + TLT falling → risk_on."""
    signals = compute_market_signals(
        vix_bars=_make_bars_rows([20.0] * 252, "VIX"),
        spy_bars=_make_bars_rows([400.0] * 252, "SPY"),
        sector_bars={etf: _make_bars_rows([100.0] * 252, etf)
                     for etf in ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB"]},
        hyg_bars=_make_bars_rows([80.0 + i * 0.1 for i in range(252)], "HYG"),
        tlt_bars=_make_bars_rows([120.0 - i * 0.1 for i in range(252)], "TLT"),
    )
    assert signals.risk_appetite == "risk_on"


def test_market_signals_insufficient_data_returns_none():
    """Less than 60 bars → returns None."""
    result = compute_market_signals(
        vix_bars=_make_bars_rows([20.0] * 30, "VIX"),
        spy_bars=_make_bars_rows([400.0] * 30, "SPY"),
        sector_bars={etf: _make_bars_rows([100.0] * 30, etf)
                     for etf in ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB"]},
        hyg_bars=_make_bars_rows([80.0] * 30, "HYG"),
        tlt_bars=_make_bars_rows([100.0] * 30, "TLT"),
    )
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_regime_signals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bullbot.features.regime_signals'`

- [ ] **Step 3: Implement MarketSignals and compute_market_signals**

Create `bullbot/features/regime_signals.py`:

```python
"""
Quantitative regime signal computation.

All functions are pure — they take bar data as lists of dicts and return
typed dataclasses. No I/O, no DB access, no LLM calls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from bullbot import config
from bullbot.features import indicators


@dataclass(frozen=True)
class MarketSignals:
    """Market-wide regime signals computed from VIX, SPY, sector ETFs, HYG, TLT."""
    vix_level: float
    vix_percentile: float
    vix_term_slope: float      # 5d SMA / 20d SMA (>1 = contango proxy)
    spy_trend: str             # 'up' | 'down' | 'flat'
    spy_momentum: float        # 20-day rate of change (%)
    breadth_score: float       # % of 11 sectors above 50d SMA (0-100)
    sector_momentum: dict      # {etf: 20d_return} sorted descending
    risk_appetite: str         # 'risk_on' | 'neutral' | 'risk_off'
    realized_vs_implied: float # SPY 20d realized vol (ann.) minus VIX


@dataclass(frozen=True)
class TickerSignals:
    """Per-ticker regime signals."""
    ticker: str
    iv_rank: float             # 0-100, from iv_surface or VIX fallback
    iv_percentile: float       # 0-100
    sector_relative: float     # ticker 20d return minus sector ETF 20d return
    vol_regime: str            # 'low' | 'moderate' | 'high'
    sector_etf: str | None     # mapped sector ETF or None for indices


def _closes(bars: list[dict]) -> list[float]:
    """Extract close prices from bar dicts, oldest first."""
    return [b["close"] for b in bars]


def _rate_of_change(closes: list[float], period: int) -> float:
    """Percentage change over the last `period` bars."""
    if len(closes) < period + 1:
        return 0.0
    return (closes[-1] - closes[-1 - period]) / closes[-1 - period] * 100.0


def _realized_vol_annualized(closes: list[float], period: int = 20) -> float:
    """Annualized realized volatility from daily returns over `period` days."""
    if len(closes) < period + 1:
        return 0.0
    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(-period, 0)
    ]
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100.0  # as percentage


def compute_market_signals(
    vix_bars: list[dict],
    spy_bars: list[dict],
    sector_bars: dict[str, list[dict]],
    hyg_bars: list[dict],
    tlt_bars: list[dict],
) -> MarketSignals | None:
    """Compute market-wide regime signals from bar data.

    Returns None if insufficient data (need at least 60 bars for any input).
    """
    vix_closes = _closes(vix_bars)
    spy_closes = _closes(spy_bars)
    hyg_closes = _closes(hyg_bars)
    tlt_closes = _closes(tlt_bars)

    if len(vix_closes) < 60 or len(spy_closes) < 60:
        return None

    # VIX
    vix_level = vix_closes[-1]
    lookback = min(len(vix_closes), 252)
    vix_history = vix_closes[-lookback:]
    vix_percentile = indicators.iv_percentile(vix_level, vix_history[:-1]) if len(vix_history) > 1 else 50.0
    vix_sma5 = indicators.sma(vix_closes, 5) or vix_level
    vix_sma20 = indicators.sma(vix_closes, 20) or vix_level
    vix_term_slope = vix_sma5 / vix_sma20 if vix_sma20 != 0 else 1.0

    # SPY trend
    spy_sma50 = indicators.sma(spy_closes, 50)
    spy_sma200 = indicators.sma(spy_closes, 200) if len(spy_closes) >= 200 else None
    spy_current = spy_closes[-1]
    if spy_sma50 and spy_current > spy_sma50:
        if spy_sma200 is None or spy_current > spy_sma200:
            spy_trend = "up"
        else:
            spy_trend = "flat"
    elif spy_sma50 and spy_current < spy_sma50:
        spy_trend = "down"
    else:
        spy_trend = "flat"

    spy_momentum = _rate_of_change(spy_closes, 20)

    # Breadth — % of sector ETFs above their own 50d SMA
    sectors_above = 0
    sector_count = 0
    sector_mom: dict[str, float] = {}
    for etf in config.SECTOR_ETFS:
        etf_bars = sector_bars.get(etf)
        if not etf_bars or len(etf_bars) < 50:
            continue
        etf_closes = _closes(etf_bars)
        sector_count += 1
        sma50 = indicators.sma(etf_closes, 50)
        if sma50 and etf_closes[-1] > sma50:
            sectors_above += 1
        sector_mom[etf] = _rate_of_change(etf_closes, 20)

    breadth_score = (sectors_above / sector_count * 100.0) if sector_count > 0 else 50.0
    sector_momentum = dict(sorted(sector_mom.items(), key=lambda x: x[1], reverse=True))

    # Risk appetite — HYG/TLT ratio trend
    if len(hyg_closes) >= 20 and len(tlt_closes) >= 20:
        ratio_now = hyg_closes[-1] / tlt_closes[-1] if tlt_closes[-1] != 0 else 1.0
        ratio_20d = hyg_closes[-20] / tlt_closes[-20] if tlt_closes[-20] != 0 else 1.0
        ratio_change = (ratio_now - ratio_20d) / ratio_20d if ratio_20d != 0 else 0.0
        if ratio_change > 0.01:
            risk_appetite = "risk_on"
        elif ratio_change < -0.01:
            risk_appetite = "risk_off"
        else:
            risk_appetite = "neutral"
    else:
        risk_appetite = "neutral"

    # Realized vs implied
    realized = _realized_vol_annualized(spy_closes, 20)
    realized_vs_implied = realized - vix_level

    return MarketSignals(
        vix_level=vix_level,
        vix_percentile=vix_percentile,
        vix_term_slope=round(vix_term_slope, 3),
        spy_trend=spy_trend,
        spy_momentum=round(spy_momentum, 2),
        breadth_score=round(breadth_score, 1),
        sector_momentum=sector_momentum,
        risk_appetite=risk_appetite,
        realized_vs_implied=round(realized_vs_implied, 2),
    )


def compute_ticker_signals(
    ticker: str,
    ticker_bars: list[dict],
    iv_history: list[float],
    current_iv: float | None,
    sector_etf_bars: list[dict] | None,
) -> TickerSignals | None:
    """Compute per-ticker regime signals.

    Args:
        ticker: The ticker symbol.
        ticker_bars: Daily bars for this ticker.
        iv_history: List of historical ATM IV values (up to 252).
        current_iv: Current ATM 30d IV, or None if unavailable.
        sector_etf_bars: Bars for the mapped sector ETF, or None.

    Returns None if insufficient data.
    """
    if len(ticker_bars) < 20:
        return None

    ticker_closes = _closes(ticker_bars)
    sector_etf = config.TICKER_SECTOR_MAP.get(ticker)

    # IV rank and percentile
    if current_iv is not None and len(iv_history) >= 20:
        iv_r = indicators.iv_rank(current_iv, iv_history)
        iv_p = indicators.iv_percentile(current_iv, iv_history)
    else:
        iv_r = 50.0
        iv_p = 50.0

    # Sector relative
    sector_relative = 0.0
    if sector_etf and sector_etf_bars and len(sector_etf_bars) >= 20:
        etf_closes = _closes(sector_etf_bars)
        ticker_roc = _rate_of_change(ticker_closes, 20)
        etf_roc = _rate_of_change(etf_closes, 20)
        sector_relative = round(ticker_roc - etf_roc, 2)

    # Vol regime — 20d realized vol percentile vs 252-day history
    if len(ticker_closes) >= 60:
        current_rvol = _realized_vol_annualized(ticker_closes, 20)
        # Compute rolling 20d rvol for past year to establish percentile
        rvol_history = []
        for i in range(60, len(ticker_closes)):
            rv = _realized_vol_annualized(ticker_closes[:i + 1], 20)
            if rv > 0:
                rvol_history.append(rv)
        if rvol_history:
            pct = sum(1 for h in rvol_history if h <= current_rvol) / len(rvol_history) * 100
            if pct < 33:
                vol_regime = "low"
            elif pct < 67:
                vol_regime = "moderate"
            else:
                vol_regime = "high"
        else:
            vol_regime = "moderate"
    else:
        vol_regime = "moderate"

    return TickerSignals(
        ticker=ticker,
        iv_rank=round(iv_r, 1),
        iv_percentile=round(iv_p, 1),
        sector_relative=sector_relative,
        vol_regime=vol_regime,
        sector_etf=sector_etf,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_regime_signals.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add bullbot/features/regime_signals.py tests/unit/test_regime_signals.py
git commit -m "stage2(T4): implement MarketSignals and TickerSignals computation"
```

---

### Task 5: Regime signals — `TickerSignals` tests

**Files:**
- Modify: `tests/unit/test_regime_signals.py`

- [ ] **Step 1: Write tests for TickerSignals**

Append to `tests/unit/test_regime_signals.py`:

```python
def test_ticker_signals_basic():
    """Basic ticker signals with known IV data."""
    ticker_bars = _make_bars_rows([100.0 + i * 0.2 for i in range(252)], "AAPL")
    # IV history: 20 to 40 linearly; current at 35 → rank should be high
    iv_history = [20.0 + i * (20.0 / 251) for i in range(252)]
    current_iv = 35.0
    sector_bars = _make_bars_rows([150.0 + i * 0.1 for i in range(252)], "XLK")

    signals = compute_ticker_signals(
        ticker="AAPL",
        ticker_bars=ticker_bars,
        iv_history=iv_history,
        current_iv=current_iv,
        sector_etf_bars=sector_bars,
    )
    assert signals is not None
    assert signals.ticker == "AAPL"
    assert signals.iv_rank > 50.0  # 35 is in the upper half of 20-40
    assert signals.sector_etf == "XLK"
    assert signals.vol_regime in ("low", "moderate", "high")


def test_ticker_signals_no_iv_defaults_to_50():
    """Missing IV data → iv_rank and iv_percentile default to 50."""
    ticker_bars = _make_bars_rows([100.0] * 60, "AAPL")
    signals = compute_ticker_signals(
        ticker="AAPL",
        ticker_bars=ticker_bars,
        iv_history=[],
        current_iv=None,
        sector_etf_bars=None,
    )
    assert signals is not None
    assert signals.iv_rank == 50.0
    assert signals.iv_percentile == 50.0


def test_ticker_signals_insufficient_data():
    """Less than 20 bars → returns None."""
    ticker_bars = _make_bars_rows([100.0] * 10, "AAPL")
    result = compute_ticker_signals(
        ticker="AAPL",
        ticker_bars=ticker_bars,
        iv_history=[],
        current_iv=None,
        sector_etf_bars=None,
    )
    assert result is None


def test_ticker_signals_index_has_no_sector():
    """SPY maps to None sector → sector_relative = 0.0."""
    ticker_bars = _make_bars_rows([400.0 + i * 0.3 for i in range(252)], "SPY")
    signals = compute_ticker_signals(
        ticker="SPY",
        ticker_bars=ticker_bars,
        iv_history=[20.0] * 252,
        current_iv=20.0,
        sector_etf_bars=None,
    )
    assert signals is not None
    assert signals.sector_etf is None
    assert signals.sector_relative == 0.0
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/unit/test_regime_signals.py -v`
Expected: All PASS (implementation from Task 4 handles these cases)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_regime_signals.py
git commit -m "stage2(T5): add TickerSignals unit tests"
```

---

### Task 6: Regime agent — LLM synthesis + caching

**Files:**
- Create: `bullbot/features/regime_agent.py`
- Create: `tests/unit/test_regime_agent.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_regime_agent.py

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from bullbot.db import migrations
from bullbot.features.regime_signals import MarketSignals, TickerSignals


def _fresh_conn():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)
    return conn


def _sample_market_signals():
    return MarketSignals(
        vix_level=18.0,
        vix_percentile=35.0,
        vix_term_slope=1.02,
        spy_trend="up",
        spy_momentum=3.5,
        breadth_score=72.7,
        sector_momentum={"XLK": 5.2, "XLC": 3.1, "XLF": -1.0},
        risk_appetite="risk_on",
        realized_vs_implied=-4.5,
    )


def _sample_ticker_signals():
    return TickerSignals(
        ticker="AAPL",
        iv_rank=68.0,
        iv_percentile=72.0,
        sector_relative=2.1,
        vol_regime="moderate",
        sector_etf="XLK",
    )


def test_synthesize_market_brief_calls_llm(fake_anthropic):
    from bullbot.features.regime_agent import synthesize_market_brief
    fake_anthropic.queue_response("Low vol trending bull. Favors short puts.")
    signals = _sample_market_signals()
    brief, cost = synthesize_market_brief(fake_anthropic, signals)
    assert "Low vol" in brief
    assert cost >= 0
    assert len(fake_anthropic.call_log) == 1
    # Verify model used
    assert fake_anthropic.call_log[0]["model"] == "claude-sonnet-4-6"


def test_synthesize_ticker_brief_calls_llm(fake_anthropic):
    from bullbot.features.regime_agent import synthesize_ticker_brief
    fake_anthropic.queue_response("AAPL IV elevated. Consider credit spreads.")
    signals = _sample_ticker_signals()
    brief, cost = synthesize_ticker_brief(fake_anthropic, signals, "Market is bullish.")
    assert "AAPL" in brief
    assert len(fake_anthropic.call_log) == 1


def test_refresh_market_brief_caches(fake_anthropic):
    from bullbot.features.regime_agent import refresh_market_brief, get_brief
    conn = _fresh_conn()
    ts = 1744243200
    fake_anthropic.queue_response("Bull regime.")
    signals = _sample_market_signals()

    # First call → LLM
    brief = refresh_market_brief(conn, fake_anthropic, signals, ts)
    assert brief == "Bull regime."
    assert len(fake_anthropic.call_log) == 1

    # Second call same day → cache hit, no additional LLM call
    brief2 = refresh_market_brief(conn, fake_anthropic, signals, ts)
    assert brief2 == "Bull regime."
    assert len(fake_anthropic.call_log) == 1  # Still 1, no new call


def test_refresh_market_brief_stores_in_db(fake_anthropic):
    from bullbot.features.regime_agent import refresh_market_brief
    conn = _fresh_conn()
    ts = 1744243200
    fake_anthropic.queue_response("Stored brief.")
    signals = _sample_market_signals()

    refresh_market_brief(conn, fake_anthropic, signals, ts)

    row = conn.execute("SELECT * FROM regime_briefs WHERE scope='market' AND ts=?", (ts,)).fetchone()
    assert row is not None
    assert row["brief_text"] == "Stored brief."
    assert row["source"] == "llm"
    assert json.loads(row["signals_json"])["vix_level"] == 18.0


def test_fallback_on_llm_failure():
    """When LLM fails twice, fall back to template string."""
    from bullbot.features.regime_agent import synthesize_market_brief

    class FailingClient:
        def __init__(self):
            self.messages = self
        def create(self, **kwargs):
            raise RuntimeError("API down")

    signals = _sample_market_signals()
    brief, cost = synthesize_market_brief(FailingClient(), signals)
    assert "VIX" in brief  # Template should mention VIX
    assert cost == 0.0


@pytest.fixture
def fake_anthropic():
    """Import from conftest — same FakeAnthropicClient used across tests."""
    from tests.conftest import FakeAnthropicClient
    return FakeAnthropicClient()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_regime_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bullbot.features.regime_agent'`

- [ ] **Step 3: Implement regime_agent.py**

Create `bullbot/features/regime_agent.py`:

```python
"""
Regime agent — synthesizes quantitative signals into natural-language
strategy briefs via Sonnet, with caching and cost tracking.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import asdict
from typing import Any

from bullbot import config
from bullbot.features.regime_signals import MarketSignals, TickerSignals
from bullbot.risk import cost_ledger
from bullbot.strategies import registry

log = logging.getLogger("bullbot.features.regime_agent")

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_MARKET_SYSTEM_PROMPT = """\
You are a quantitative market regime analyst for an automated options trading system.
Given market signals, produce a concise regime assessment and strategy recommendations.
Output 3-5 sentences. Be specific about which options strategy families are favored
or disfavored in current conditions. Do not hedge — state your assessment directly.

The trading system can implement these registered strategies: {strategy_names}
Only recommend strategy types from this list."""

_TICKER_SYSTEM_PROMPT = """\
You are a quantitative analyst advising an automated options strategy proposer.
Given market context and ticker-specific signals, recommend strategy approaches
for this specific ticker. Output 2-3 sentences. Be specific about strategy types
and why they suit this ticker's current conditions.

The trading system can implement these registered strategies: {strategy_names}
Only recommend strategy types from this list."""


def _format_market_signals(signals: MarketSignals) -> str:
    """Format market signals into a human-readable user prompt."""
    top_sectors = list(signals.sector_momentum.items())[:5]
    sector_str = ", ".join(f"{etf} ({ret:+.1f}%)" for etf, ret in top_sectors)
    return f"""\
VIX: {signals.vix_level:.1f} ({signals.vix_percentile:.0f}th percentile)
VIX Term Slope (5d/20d SMA): {signals.vix_term_slope:.3f} ({'contango' if signals.vix_term_slope > 1 else 'backwardation'})
SPY Trend: {signals.spy_trend}
SPY 20d Momentum: {signals.spy_momentum:+.1f}%
Breadth (sectors above 50d SMA): {signals.breadth_score:.0f}%
Top Sectors (20d return): {sector_str}
Risk Appetite: {signals.risk_appetite}
Realized vs Implied Vol: {signals.realized_vs_implied:+.1f} (negative = vol premium exists)"""


def _format_ticker_signals(signals: TickerSignals, market_brief: str) -> str:
    """Format ticker signals + market context into user prompt."""
    return f"""\
=== Market Context ===
{market_brief}

=== {signals.ticker} Specific ===
IV Rank: {signals.iv_rank:.0f}/100
IV Percentile: {signals.iv_percentile:.0f}/100
Sector: {signals.sector_etf or 'Index (no sector)'}
Sector-Relative 20d Return: {signals.sector_relative:+.1f}%
Volatility Regime: {signals.vol_regime}"""


def _fallback_market_brief(signals: MarketSignals) -> str:
    """Template-only brief when LLM is unavailable."""
    return (
        f"VIX at {signals.vix_level:.1f} ({signals.vix_percentile:.0f}th percentile). "
        f"SPY trend: {signals.spy_trend}, 20d momentum {signals.spy_momentum:+.1f}%. "
        f"Breadth: {signals.breadth_score:.0f}% of sectors above 50d SMA. "
        f"Risk appetite: {signals.risk_appetite}. "
        f"Realized vs implied: {signals.realized_vs_implied:+.1f}."
    )


def _fallback_ticker_brief(signals: TickerSignals) -> str:
    """Template-only brief when LLM is unavailable."""
    return (
        f"{signals.ticker} IV rank {signals.iv_rank:.0f}/100, "
        f"vol regime: {signals.vol_regime}, "
        f"sector-relative: {signals.sector_relative:+.1f}%."
    )


# ---------------------------------------------------------------------------
# LLM synthesis
# ---------------------------------------------------------------------------


def synthesize_market_brief(
    client: Any,
    signals: MarketSignals,
) -> tuple[str, float]:
    """Synthesize a market-wide brief via Sonnet.

    Returns (brief_text, cost_usd). Falls back to template on LLM failure.
    """
    strategy_names = ", ".join(registry.list_all_names())
    system = _MARKET_SYSTEM_PROMPT.format(strategy_names=strategy_names)
    user = _format_market_signals(signals)

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=config.REGIME_SYNTHESIS_MODEL,
                max_tokens=config.REGIME_MARKET_BRIEF_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            usage = response.usage
            cost = (usage.input_tokens * 3.0 + usage.output_tokens * 15.0) / 1_000_000
            cost = max(cost, 0.001)
            text = ""
            for block in response.content:
                t = getattr(block, "text", None)
                if t:
                    text = t
                    break
            if text:
                return text, cost
        except Exception as exc:
            log.warning("Market brief synthesis attempt %d failed: %s", attempt + 1, exc)

    log.warning("Market brief synthesis failed after 2 attempts; using fallback template")
    return _fallback_market_brief(signals), 0.0


def synthesize_ticker_brief(
    client: Any,
    signals: TickerSignals,
    market_brief: str,
) -> tuple[str, float]:
    """Synthesize a per-ticker brief via Sonnet.

    Returns (brief_text, cost_usd). Falls back to template on LLM failure.
    """
    strategy_names = ", ".join(registry.list_all_names())
    system = _TICKER_SYSTEM_PROMPT.format(strategy_names=strategy_names)
    user = _format_ticker_signals(signals, market_brief)

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=config.REGIME_SYNTHESIS_MODEL,
                max_tokens=config.REGIME_TICKER_BRIEF_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            usage = response.usage
            cost = (usage.input_tokens * 3.0 + usage.output_tokens * 15.0) / 1_000_000
            cost = max(cost, 0.001)
            text = ""
            for block in response.content:
                t = getattr(block, "text", None)
                if t:
                    text = t
                    break
            if text:
                return text, cost
        except Exception as exc:
            log.warning("Ticker brief synthesis attempt %d failed: %s", attempt + 1, exc)

    log.warning("Ticker brief synthesis failed after 2 attempts; using fallback template")
    return _fallback_ticker_brief(signals), 0.0


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------


def get_brief(conn: sqlite3.Connection, scope: str, ts: int) -> str | None:
    """Return cached brief_text for scope+ts, or None on miss."""
    row = conn.execute(
        "SELECT brief_text FROM regime_briefs WHERE scope=? AND ts=?",
        (scope, ts),
    ).fetchone()
    return row["brief_text"] if row else None


def refresh_market_brief(
    conn: sqlite3.Connection,
    client: Any,
    signals: MarketSignals,
    ts: int,
) -> str:
    """Return the market brief for today, synthesizing on cache miss."""
    cached = get_brief(conn, "market", ts)
    if cached is not None:
        return cached

    brief, cost = synthesize_market_brief(client, signals)
    source = "llm" if cost > 0 else "fallback"
    now = int(time.time())

    conn.execute(
        "INSERT INTO regime_briefs (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("market", ts, json.dumps(asdict(signals)), brief,
         config.REGIME_SYNTHESIS_MODEL, cost, source, now),
    )

    if cost > 0:
        cost_ledger.append(
            conn, ts=now, category="llm", ticker=None,
            amount_usd=cost,
            details={"source": "regime_agent", "scope": "market"},
        )

    return brief


def refresh_ticker_brief(
    conn: sqlite3.Connection,
    client: Any,
    signals: TickerSignals,
    market_brief: str,
    ts: int,
) -> str:
    """Return the ticker brief for today, synthesizing on cache miss."""
    cached = get_brief(conn, signals.ticker, ts)
    if cached is not None:
        return cached

    brief, cost = synthesize_ticker_brief(client, signals, market_brief)
    source = "llm" if cost > 0 else "fallback"
    now = int(time.time())

    conn.execute(
        "INSERT INTO regime_briefs (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (signals.ticker, ts, json.dumps(asdict(signals)), brief,
         config.REGIME_SYNTHESIS_MODEL, cost, source, now),
    )

    if cost > 0:
        cost_ledger.append(
            conn, ts=now, category="llm", ticker=signals.ticker,
            amount_usd=cost,
            details={"source": "regime_agent", "scope": signals.ticker},
        )

    return brief
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_regime_agent.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add bullbot/features/regime_agent.py tests/unit/test_regime_agent.py
git commit -m "stage2(T6): implement regime agent synthesis + caching"
```

---

### Task 7: IV rank — replace hardcoded 50.0 in `_build_snapshot`

**Files:**
- Modify: `bullbot/engine/step.py:107-125`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_engine_step.py`:

```python
def test_build_snapshot_computes_iv_rank(db_conn):
    """iv_rank should be computed from iv_surface, not hardcoded to 50.0."""
    from bullbot.engine.step import _build_snapshot

    ticker = "SPY"
    base_ts = 1700000000

    # Insert 252 daily bars
    for i in range(252):
        ts = base_ts + i * 86400
        db_conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, 400.0, 401.0, 399.0, 400.0, 1000000)",
            (ticker, ts),
        )

    # Insert 252 days of IV surface data: IV ranges from 15 to 35
    for i in range(252):
        ts = base_ts + i * 86400
        iv = 15.0 + (20.0 * i / 251)
        db_conn.execute(
            "INSERT INTO iv_surface (ticker, ts, expiry, strike, iv) "
            "VALUES (?, ?, '2026-06-20', 400.0, ?)",
            (ticker, ts, iv),
        )

    cursor = base_ts + 251 * 86400
    snap = _build_snapshot(db_conn, ticker, cursor)
    assert snap is not None
    # IV at day 251 is 35.0. Range is 15-35. Rank should be ~100.
    assert snap.iv_rank > 80.0  # Not hardcoded 50.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_engine_step.py::test_build_snapshot_computes_iv_rank -v`
Expected: FAIL with `assert 50.0 > 80.0`

- [ ] **Step 3: Implement IV rank computation in _build_snapshot**

In `bullbot/engine/step.py`, replace the hardcoded iv_rank line and add a helper:

Add this function before `_build_snapshot`:

```python
def _compute_iv_rank(conn: sqlite3.Connection, ticker: str, cursor: int) -> float:
    """Compute IV rank from iv_surface table.

    Uses the most recent ATM IV observation at each day as the daily IV.
    Falls back to 50.0 if insufficient data.
    """
    rows = conn.execute(
        "SELECT ts, iv FROM iv_surface "
        "WHERE ticker=? AND ts<=? "
        "ORDER BY ts DESC LIMIT 252",
        (ticker, cursor),
    ).fetchall()

    if len(rows) < 20:
        return 50.0  # Insufficient history

    ivs = [float(r["iv"]) for r in rows if r["iv"] is not None]
    if len(ivs) < 20:
        return 50.0

    current_iv = ivs[0]  # Most recent
    return indicators.iv_rank(current_iv, ivs[1:])
```

Then in `_build_snapshot`, replace `iv_rank = 50.0  # v1 simplification` with:

```python
    iv_rank = _compute_iv_rank(conn, ticker, cursor)
```

Also add the import at the top of the file (it's already imported as `indicators` via `from bullbot.features import indicators`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_engine_step.py::test_build_snapshot_computes_iv_rank -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest -x -q`
Expected: All pass (existing tests that don't populate iv_surface still get 50.0 fallback)

- [ ] **Step 6: Commit**

```bash
git add bullbot/engine/step.py tests/integration/test_engine_step.py
git commit -m "stage2(T7): compute real IV rank from iv_surface, replacing hardcoded 50.0"
```

---

### Task 8: Proposer prompt — add regime context blocks

**Files:**
- Modify: `bullbot/evolver/proposer.py:119-148`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_proposer.py`:

```python
def test_build_user_prompt_includes_regime_briefs():
    from bullbot.evolver.proposer import build_user_prompt
    from bullbot.strategies.base import StrategySnapshot

    snap = StrategySnapshot(
        ticker="AAPL",
        asof_ts=1000000,
        spot=180.0,
        bars_1d=[],
        indicators={"sma_20": 178.0},
        atm_greeks={},
        iv_rank=65.0,
        regime="bull",
        chain=[],
        market_brief="Low vol bull regime. Favors short puts.",
        ticker_brief="AAPL IV elevated at 72nd pct. Consider credit spreads.",
    )

    prompt = build_user_prompt(snap, [], None)
    assert "=== Market Regime Analysis ===" in prompt
    assert "Low vol bull regime" in prompt
    assert "=== Ticker Analysis (AAPL) ===" in prompt
    assert "AAPL IV elevated" in prompt


def test_build_user_prompt_omits_regime_when_empty():
    from bullbot.evolver.proposer import build_user_prompt
    from bullbot.strategies.base import StrategySnapshot

    snap = StrategySnapshot(
        ticker="AAPL",
        asof_ts=1000000,
        spot=180.0,
        bars_1d=[],
        indicators={},
        atm_greeks={},
        iv_rank=50.0,
        regime="bull",
        chain=[],
    )

    prompt = build_user_prompt(snap, [], None)
    # Should not include regime blocks when briefs are empty
    assert "=== Market Regime Analysis ===" not in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_proposer.py::test_build_user_prompt_includes_regime_briefs -v`
Expected: FAIL with `AssertionError: assert '=== Market Regime Analysis ===' in ...`

- [ ] **Step 3: Modify build_user_prompt to include regime blocks**

In `bullbot/evolver/proposer.py`, replace the `build_user_prompt` function:

```python
def build_user_prompt(
    snapshot: StrategySnapshot,
    history: list[dict],
    best_strategy_id: str | None,
) -> str:
    """Compose the full user-turn prompt."""
    history_block = build_history_block(history)
    best_note = (
        f"Current best strategy ID: {best_strategy_id}"
        if best_strategy_id
        else "No best strategy identified yet."
    )

    # Regime context — only include if briefs are non-empty
    regime_block = ""
    if snapshot.market_brief:
        regime_block += f"\n=== Market Regime Analysis ===\n{snapshot.market_brief}\n"
    if snapshot.ticker_brief:
        regime_block += f"\n=== Ticker Analysis ({snapshot.ticker}) ===\n{snapshot.ticker_brief}\n"

    return f"""=== Market Snapshot ===
Ticker:     {snapshot.ticker}
As-of Unix: {snapshot.asof_ts}
Spot:       {snapshot.spot}
Regime:     {snapshot.regime}
IV Rank:    {snapshot.iv_rank}
Indicators: {json.dumps(snapshot.indicators)}
ATM Greeks: {json.dumps(snapshot.atm_greeks)}
{regime_block}
=== Evolver History ===
{history_block}

=== Context ===
{best_note}

Propose the next strategy variant. Output only the JSON object described in your instructions.
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_proposer.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add bullbot/evolver/proposer.py tests/integration/test_proposer.py
git commit -m "stage2(T8): add regime context blocks to proposer prompt"
```

---

### Task 9: Scheduler integration — regime refresh before evolver loop

**Files:**
- Modify: `bullbot/scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_regime_scheduler.py — new file

import json
import sqlite3
import time
from unittest.mock import patch

from bullbot.db import migrations
from tests.conftest import FakeAnthropicClient, FakeUWClient


def _fresh_conn():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)
    return conn


def _seed_bars(conn, ticker, n=252, base_ts=1700000000):
    """Insert n daily bars for a ticker."""
    for i in range(n):
        ts = base_ts + i * 86400
        conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, 100.0, 101.0, 99.0, ?, 1000000)",
            (ticker, ts, 100.0 + i * 0.1),
        )


def test_scheduler_tick_calls_regime_refresh(fake_anthropic, fake_uw):
    """Scheduler tick should refresh regime briefs before running evolver."""
    conn = _fresh_conn()

    # Seed bars for regime data tickers + one universe ticker
    for ticker in ["VIX", "SPY", "XLK", "XLF", "XLE", "XLV", "XLI",
                    "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB", "TLT", "HYG"]:
        _seed_bars(conn, ticker)

    # Queue LLM responses: 1 market brief + 1 ticker brief + 1 proposer response
    fake_anthropic.queue_response("Bull regime. Favors PutCreditSpread.")  # market brief
    fake_anthropic.queue_response("SPY: short puts favorable.")  # ticker brief
    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 30, "short_delta": 0.25, "width": 5},
        "rationale": "test",
    }))

    from bullbot import scheduler
    scheduler.tick(conn, fake_anthropic, fake_uw, universe=["SPY"])

    # Verify regime_briefs were created
    rows = conn.execute("SELECT * FROM regime_briefs").fetchall()
    assert len(rows) >= 1  # At minimum market brief
    scopes = {r["scope"] for r in rows}
    assert "market" in scopes


def test_scheduler_tick_skips_regime_on_insufficient_data(fake_anthropic, fake_uw):
    """If no bars exist for regime tickers, scheduler should still run evolver."""
    conn = _fresh_conn()

    # Only seed SPY bars (no regime data tickers)
    _seed_bars(conn, "SPY")

    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 30, "short_delta": 0.25, "width": 5},
        "rationale": "test",
    }))

    from bullbot import scheduler
    # Should not crash
    scheduler.tick(conn, fake_anthropic, fake_uw, universe=["SPY"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_regime_scheduler.py -v`
Expected: FAIL (scheduler doesn't call regime refresh yet)

- [ ] **Step 3: Modify scheduler.py to call regime refresh**

Replace `bullbot/scheduler.py` with:

```python
"""Scheduler — the outer loop."""
from __future__ import annotations
import logging, sqlite3, time, traceback
from typing import Any
from bullbot import clock, config, nightly
from bullbot.evolver import iteration as evolver_iteration
from bullbot.features import regime_agent, regime_signals
from bullbot.risk import kill_switch

log = logging.getLogger("bullbot.scheduler")


def _record_iteration_failure(conn, ticker, phase, exc):
    conn.execute(
        "INSERT INTO iteration_failures (ts, ticker, phase, exc_type, exc_message, traceback) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (int(time.time()), ticker, phase, type(exc).__name__, str(exc), traceback.format_exc()),
    )


def _load_bars_for_ticker(conn, ticker, limit=252):
    """Load recent bars as list of dicts."""
    rows = conn.execute(
        "SELECT * FROM bars WHERE ticker=? AND timeframe='1d' ORDER BY ts DESC LIMIT ?",
        (ticker, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _today_ts():
    """Return midnight UTC epoch for today."""
    now = time.time()
    return int(now - (now % 86400))


def _refresh_regime(conn, anthropic_client):
    """Run daily regime refresh: compute signals, synthesize briefs."""
    ts = _today_ts()

    # Check if market brief already cached
    if regime_agent.get_brief(conn, "market", ts) is not None:
        return

    # Load bars for all regime data tickers
    vix_bars = _load_bars_for_ticker(conn, "VIX")
    spy_bars = _load_bars_for_ticker(conn, "SPY")
    sector_bars = {}
    for etf in config.SECTOR_ETFS:
        bars = _load_bars_for_ticker(conn, etf)
        if bars:
            sector_bars[etf] = bars
    hyg_bars = _load_bars_for_ticker(conn, "HYG")
    tlt_bars = _load_bars_for_ticker(conn, "TLT")

    market_signals = regime_signals.compute_market_signals(
        vix_bars=vix_bars,
        spy_bars=spy_bars,
        sector_bars=sector_bars,
        hyg_bars=hyg_bars,
        tlt_bars=tlt_bars,
    )

    if market_signals is None:
        log.warning("Insufficient data for market regime signals; skipping regime refresh")
        return

    market_brief = regime_agent.refresh_market_brief(conn, anthropic_client, market_signals, ts)

    # Per-ticker briefs
    for ticker in config.UNIVERSE:
        try:
            ticker_bars = _load_bars_for_ticker(conn, ticker)
            sector_etf = config.TICKER_SECTOR_MAP.get(ticker)
            sector_etf_bars = _load_bars_for_ticker(conn, sector_etf) if sector_etf else None

            # Load IV history from iv_surface
            iv_rows = conn.execute(
                "SELECT iv FROM iv_surface WHERE ticker=? ORDER BY ts DESC LIMIT 252",
                (ticker,),
            ).fetchall()
            iv_history = [float(r["iv"]) for r in iv_rows if r["iv"] is not None]
            current_iv = iv_history[0] if iv_history else None

            ticker_signals = regime_signals.compute_ticker_signals(
                ticker=ticker,
                ticker_bars=ticker_bars,
                iv_history=iv_history,
                current_iv=current_iv,
                sector_etf_bars=sector_etf_bars,
            )
            if ticker_signals is not None:
                regime_agent.refresh_ticker_brief(conn, anthropic_client, ticker_signals, market_brief, ts)
        except Exception as exc:
            log.warning("Regime refresh failed for %s: %s", ticker, exc)
            continue


def _dispatch_ticker(conn, ticker, anthropic_client, data_client):
    row = conn.execute("SELECT * FROM ticker_state WHERE ticker=?", (ticker,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES (?, 'discovering', ?)",
            (ticker, int(time.time())),
        )
        row = conn.execute("SELECT * FROM ticker_state WHERE ticker=?", (ticker,)).fetchone()
    phase = row["phase"]
    if row["retired"]:
        return
    if phase == "discovering":
        evolver_iteration.run(conn, anthropic_client, data_client, ticker)
        return
    # paper_trial/live: dispatch to engine.step (skipped in v1 scheduler tests)


def tick(conn, anthropic_client, data_client, universe=None):
    if kill_switch.is_tripped(conn):
        return
    if kill_switch.should_trip_now(conn):
        kill_switch.trip(conn, reason="pre_tick_check")
        return

    # Daily regime refresh (cache-aware — only calls LLM once per day)
    try:
        _refresh_regime(conn, anthropic_client)
    except Exception as exc:
        log.warning("Regime refresh failed: %s; continuing without briefs", exc)

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_regime_scheduler.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add bullbot/scheduler.py tests/integration/test_regime_scheduler.py
git commit -m "stage2(T9): integrate regime refresh into scheduler tick"
```

---

### Task 10: Wire briefs into snapshot for evolver iterations

**Files:**
- Modify: `bullbot/engine/step.py:107-125`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_engine_step.py`:

```python
def test_build_snapshot_includes_briefs_when_available(db_conn):
    """Snapshot should include regime briefs from regime_briefs table."""
    from bullbot.engine.step import _build_snapshot

    ticker = "SPY"
    base_ts = 1700000000

    # Insert bars
    for i in range(100):
        ts = base_ts + i * 86400
        db_conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, 400.0, 401.0, 399.0, 400.0, 1000000)",
            (ticker, ts),
        )

    # Insert regime briefs for today
    today_ts = base_ts - (base_ts % 86400)  # midnight UTC
    db_conn.execute(
        "INSERT INTO regime_briefs (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at) "
        "VALUES ('market', ?, '{}', 'Bull regime.', 'claude-sonnet-4-6', 0.003, 'llm', ?)",
        (today_ts, base_ts),
    )
    db_conn.execute(
        "INSERT INTO regime_briefs (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at) "
        "VALUES ('SPY', ?, '{}', 'SPY trending up.', 'claude-sonnet-4-6', 0.003, 'llm', ?)",
        (today_ts, base_ts),
    )

    cursor = base_ts + 99 * 86400
    snap = _build_snapshot(db_conn, ticker, cursor)
    assert snap is not None
    assert snap.market_brief == "Bull regime."
    assert snap.ticker_brief == "SPY trending up."


def test_build_snapshot_empty_briefs_when_no_regime_data(db_conn):
    """Snapshot should have empty briefs when no regime_briefs exist."""
    from bullbot.engine.step import _build_snapshot

    ticker = "SPY"
    base_ts = 1700000000

    for i in range(100):
        ts = base_ts + i * 86400
        db_conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, 400.0, 401.0, 399.0, 400.0, 1000000)",
            (ticker, ts),
        )

    cursor = base_ts + 99 * 86400
    snap = _build_snapshot(db_conn, ticker, cursor)
    assert snap is not None
    assert snap.market_brief == ""
    assert snap.ticker_brief == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_engine_step.py::test_build_snapshot_includes_briefs_when_available -v`
Expected: FAIL with `assert '' == 'Bull regime.'`

- [ ] **Step 3: Modify _build_snapshot to attach briefs**

In `bullbot/engine/step.py`, update `_build_snapshot` to load and attach briefs:

```python
def _load_brief(conn: sqlite3.Connection, scope: str, cursor: int) -> str:
    """Load the most recent regime brief for scope on or before cursor's day."""
    day_ts = cursor - (cursor % 86400)
    row = conn.execute(
        "SELECT brief_text FROM regime_briefs WHERE scope=? AND ts<=? ORDER BY ts DESC LIMIT 1",
        (scope, day_ts),
    ).fetchone()
    return row["brief_text"] if row else ""


def _build_snapshot(conn: sqlite3.Connection, ticker: str, cursor: int) -> StrategySnapshot | None:
    bars = _load_bars_at_cursor(conn, ticker, cursor, limit=400)
    if len(bars) < 60:
        return None
    chain = _load_chain_at_cursor(conn, ticker, cursor)
    ind = _compute_indicators(bars)
    regime = regime_mod.classify([b.close for b in bars[-60:]])
    iv_rank = _compute_iv_rank(conn, ticker, cursor)
    market_brief = _load_brief(conn, "market", cursor)
    ticker_brief = _load_brief(conn, ticker, cursor)
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
        market_brief=market_brief,
        ticker_brief=ticker_brief,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_engine_step.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add bullbot/engine/step.py tests/integration/test_engine_step.py
git commit -m "stage2(T10): wire regime briefs into snapshot for evolver iterations"
```

---

### Task 11: Integration test — full regime refresh + evolver cycle

**Files:**
- Create: `tests/integration/test_regime_integration.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/integration/test_regime_integration.py

"""
End-to-end test: regime refresh populates briefs → evolver iteration
sees them in the proposer prompt.
"""

import json
import sqlite3
import time

import pytest

from bullbot.db import migrations
from bullbot.features import regime_agent, regime_signals
from bullbot import config
from tests.conftest import FakeAnthropicClient


def _fresh_conn():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)
    return conn


def _seed_bars(conn, ticker, n=252, base_ts=1700000000, start_price=100.0):
    for i in range(n):
        ts = base_ts + i * 86400
        p = start_price + i * 0.1
        conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, 1000000)",
            (ticker, ts, p, p + 1, p - 1, p),
        )


def test_full_regime_refresh_and_cache_dedup():
    """Regime refresh produces briefs; second call reuses cache."""
    conn = _fresh_conn()
    client = FakeAnthropicClient()
    ts = 1700000000 - (1700000000 % 86400)

    # Seed all regime data tickers
    for ticker in config.REGIME_DATA_TICKERS:
        _seed_bars(conn, ticker)
    _seed_bars(conn, "SPY", start_price=400.0)

    # Market signals
    vix_bars = [dict(r) for r in reversed(conn.execute(
        "SELECT * FROM bars WHERE ticker='VIX' ORDER BY ts DESC LIMIT 252"
    ).fetchall())]
    spy_bars = [dict(r) for r in reversed(conn.execute(
        "SELECT * FROM bars WHERE ticker='SPY' ORDER BY ts DESC LIMIT 252"
    ).fetchall())]
    sector_bars = {}
    for etf in config.SECTOR_ETFS:
        rows = conn.execute(
            "SELECT * FROM bars WHERE ticker=? ORDER BY ts DESC LIMIT 252", (etf,)
        ).fetchall()
        sector_bars[etf] = [dict(r) for r in reversed(rows)]
    hyg_bars = [dict(r) for r in reversed(conn.execute(
        "SELECT * FROM bars WHERE ticker='HYG' ORDER BY ts DESC LIMIT 252"
    ).fetchall())]
    tlt_bars = [dict(r) for r in reversed(conn.execute(
        "SELECT * FROM bars WHERE ticker='TLT' ORDER BY ts DESC LIMIT 252"
    ).fetchall())]

    signals = regime_signals.compute_market_signals(
        vix_bars=vix_bars, spy_bars=spy_bars, sector_bars=sector_bars,
        hyg_bars=hyg_bars, tlt_bars=tlt_bars,
    )
    assert signals is not None

    # First refresh → LLM call
    client.queue_response("Bull regime. PutCreditSpread favored.")
    brief1 = regime_agent.refresh_market_brief(conn, client, signals, ts)
    assert brief1 == "Bull regime. PutCreditSpread favored."
    assert len(client.call_log) == 1

    # Second refresh → cache hit
    brief2 = regime_agent.refresh_market_brief(conn, client, signals, ts)
    assert brief2 == brief1
    assert len(client.call_log) == 1  # No new LLM call

    # Verify DB row
    row = conn.execute("SELECT * FROM regime_briefs WHERE scope='market'").fetchone()
    assert row is not None
    assert row["source"] == "llm"
    assert json.loads(row["signals_json"])["vix_level"] == signals.vix_level


def test_regime_cost_tracked_in_cost_ledger():
    """Regime agent LLM calls should be logged in cost_ledger."""
    conn = _fresh_conn()
    client = FakeAnthropicClient()
    ts = 1700000000

    signals = regime_signals.MarketSignals(
        vix_level=20.0, vix_percentile=50.0, vix_term_slope=1.0,
        spy_trend="flat", spy_momentum=0.0, breadth_score=50.0,
        sector_momentum={}, risk_appetite="neutral", realized_vs_implied=0.0,
    )

    client.queue_response("Neutral regime.")
    regime_agent.refresh_market_brief(conn, client, signals, ts)

    rows = conn.execute(
        "SELECT * FROM cost_ledger WHERE category='llm'"
    ).fetchall()
    assert len(rows) >= 1
    details = json.loads(rows[-1]["details"])
    assert details["source"] == "regime_agent"
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/integration/test_regime_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_regime_integration.py
git commit -m "stage2(T11): add end-to-end regime integration tests"
```

---

### Task 12: Run full test suite + final verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `pytest -x -v`
Expected: All tests pass including new regime tests

- [ ] **Step 2: Verify regime_briefs table works with schema**

Run: `python -c "from bullbot.db import migrations; import sqlite3; conn = sqlite3.connect(':memory:'); migrations.apply_schema(conn); print('Schema OK'); conn.execute('SELECT * FROM regime_briefs LIMIT 0'); print('regime_briefs OK')"`
Expected: Both prints succeed

- [ ] **Step 3: Verify no import errors**

Run: `python -c "from bullbot.features import regime_signals, regime_agent; print('Imports OK')"`
Expected: `Imports OK`

- [ ] **Step 4: Count tests**

Run: `pytest --co -q | tail -1`
Expected: Test count should be ~200+ (185 original + ~15 new)

- [ ] **Step 5: Commit any remaining changes and tag**

```bash
git add -A
git status  # verify nothing unexpected
git commit -m "stage2(T12): regime agent complete — all tests passing"
git push origin main
```

---

## Task Dependency Summary

```
T1 (config) ─────────────┐
T2 (schema) ─────────────┤
T3 (StrategySnapshot) ───┤
                          ├─ T4 (MarketSignals) ─── T5 (TickerSignals tests)
                          │                               │
                          ├─ T6 (regime agent) ───────────┤
                          │                               │
                          ├─ T7 (IV rank) ────────────────┤
                          │                               │
                          ├─ T8 (proposer prompt) ────────┤
                          │                               │
                          └─ T9 (scheduler) ──── T10 (wire briefs) ──── T11 (integration) ──── T12 (verify)
```

Tasks T1-T3 are independent and can be done in parallel.
Tasks T4-T8 depend on T1-T3 but are independent of each other.
Tasks T9-T12 must be sequential.
