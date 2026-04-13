# Growth Strategy Design Spec

**Date:** 2026-04-13
**Status:** Draft

## Goal

Add a growth strategy layer to Bull-Bot alongside the existing income (theta decay) layer. Growth strategies are bidirectional — long positions to capture upside in bull markets, short positions to capture drawdowns in bear markets. All growth trades go through the same evolver pipeline as income trades: walk-forward backtested, gate-checked, paper-trialed before promotion.

Starting ticker: TSLA.

## Architecture

### Strategy Categories

Every ticker in the universe gets a `category` assignment:

| Category | Meaning | Example |
|----------|---------|---------|
| `income` | Short premium, theta decay | SPY |
| `growth` | Directional, longer duration | TSLA |
| `both` | Either category eligible | QQQ (future) |

The category determines which capital pool a strategy sizes against and which evaluation metrics the evolver uses. It does not restrict which strategy classes the evolver can propose — a growth ticker could end up with a credit spread if the evolver finds edge there.

### New Strategy Classes

Added to the existing registry alongside `PutCreditSpread`, `CallCreditSpread`, `IronCondor`, `CashSecuredPut`, `LongCall`, `LongPut`:

- **`GrowthLEAPS`** — Buy long-dated calls. Params: `target_delta`, `min_dte`, `max_dte`, `iv_rank_max` (avoid overpaying for vol), `regime_filter` (optional: only enter in specified regimes).
- **`BearPutSpread`** — Defined-risk bearish debit spread. Params: `dte`, `long_delta`, `width`, `iv_rank_min`, `regime_filter`.
- **`GrowthEquity`** — Buy shares. Simplest growth vehicle. Params: `regime_filter`, `position_size_pct` (fraction of growth capital per entry). Unlike options strategies, this uses a share-based fill path (quantity of shares rather than options legs). The fill model needs a `simulate_equity_buy/sell` method alongside the existing multi-leg options fills.

`regime_filter` on all growth strategies is an optional list of regime strings (e.g., `["bull", "chop"]`). When set, the strategy only evaluates in those regimes. When null/empty, no regime filtering is applied — the evolver discovers whether filtering helps.

Existing classes (`LongCall`, `LongPut`) are also available to the growth evolver. The proposer's strategy menu expands based on the ticker's category — growth tickers see directional strategy classes emphasized, but income classes aren't hard-blocked.

### Capital Separation

A new config block controls the growth capital pool:

```
GROWTH_FRAC_BULL  = 0.40   # 40% of equity allocated to growth in bull regime
GROWTH_FRAC_CHOP  = 0.20   # 20% in chop
GROWTH_FRAC_BEAR  = 0.10   # 10% in bear
```

The position sizer reads the current regime and the ticker's category to determine the available capital:

- Income strategies size against `equity * (1 - growth_frac)`
- Growth strategies size against `equity * growth_frac`

Where `growth_frac` is looked up from the regime-to-fraction mapping above. This naturally dampens new long entries in downturns and expands them in uptrends without a hard override.

### Net Exposure Tracking

The position sizer tracks net exposure per ticker across both income and growth pools. When sizing a new position, it knows the total delta exposure on that ticker (long LEAPS + any short positions + any income positions). This prevents accidental over-concentration and ensures that a bearish trade alongside existing LEAPS is sized as a partial hedge, not a full directional bet.

Implementation: a `net_exposure(conn, ticker)` helper that sums `contracts * delta * direction` across all open positions for a ticker, regardless of run_id. This requires storing `entry_delta` on positions at open time (similar to how `exit_rules` is stored as JSON). The delta at entry is available from the option chain's greeks when the position is opened.

### Per-trade risk limits

The existing 2% per-trade and 6% per-sector limits (from the risk management framework) apply to growth trades identically. Growth trades are not exempt from risk discipline — they just size against a different capital pool.

## Walk-Forward Adaptation

Growth strategies need different evaluation windows and metrics than income strategies.

### Window and Fold Parameters

| Parameter | Income | Growth |
|-----------|--------|--------|
| `WF_WINDOW_MONTHS` | 24 | 60 |
| `WF_STEP_DAYS` | 30 | 90 |
| `WF_MAX_FOLDS` | 8 | 8 |
| `WF_MIN_FOLDS` | 3 | 3 |
| `WF_TRAIN_FRAC` | 0.70 | 0.70 |

The 5-year window ensures growth strategies are validated across multiple market regimes (bull, bear, and chop cycles). 90-day OOS folds give long-duration positions time to open, breathe, and close within a single fold.

### Gate Metrics

Income strategies use profit factor. Growth strategies use risk-adjusted return metrics:

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| `GROWTH_EDGE_CAGR_MIN` | 0.20 (20%) | Must beat passive SPY returns to justify active management |
| `GROWTH_EDGE_SORTINO_MIN` | 1.0 | Downside-risk-adjusted return must be acceptable |
| `GROWTH_EDGE_MAX_DD_PCT` | 0.35 (35%) | Max drawdown on growth capital pool |
| `GROWTH_EDGE_TRADE_COUNT_MIN` | 5 | Lower bar since positions are longer-lived |

The `plateau.classify()` function checks the ticker's category and applies the appropriate metric set. The plateau detection logic (improvement tracking, counter) works identically — only the gate thresholds differ.

### Metric Computation

New functions in the backtest metrics module:

- `cagr(equity_curve, days)` — compound annual growth rate from an equity curve
- `sortino(returns, risk_free_rate)` — Sortino ratio using downside deviation
- `max_drawdown_pct(equity_curve)` — already exists, reused

The walk-forward aggregation builds an equity curve across OOS folds (chronologically ordered) and computes CAGR and Sortino on the combined curve, rather than averaging per-fold metrics.

## Data Requirements

### Daily Bars

TSLA needs 5 years of daily bars (~1,260 bars) for the growth walk-forward window. Available from Unusual Whales or Yahoo Finance. No API rate limit concerns — bars are cheap.

### Options Data

Two approaches, used in sequence:

1. **Synthetic pricing (initial discovery):** For the evolver's first pass, estimate historical LEAPS prices using Black-Scholes with historical IV and known spot prices from bars. Historical IV for TSLA can be sourced from Yahoo Finance (same fallback used for VIX bars) or computed from any available options data. This avoids the UW API bottleneck and provides enough signal for the evolver to discover whether directional strategies have edge on TSLA.

2. **Real quotes (validation):** Once the evolver finds a candidate strategy, backfill real historical options data for TSLA to validate with actual bid/ask spreads and liquidity. This is the same UW backfill pipeline used for SPY, but only needed for the specific expiries and strikes the strategy uses.

This phased approach means we can start running the growth evolver immediately without waiting 2+ days for options backfill.

## Proposer Changes

The LLM proposer's system prompt expands to include the ticker's category and the full menu of growth strategy classes. The proposer already handles multiple strategy types — it just gets a larger menu and category-specific guidance:

- For growth tickers: emphasize directional strategies, longer holding periods, regime awareness
- For income tickers: current behavior unchanged
- For "both" tickers: full menu available

The proposer's history block already shows past iterations with class names and params, so it naturally learns which direction to explore based on what has and hasn't worked.

## Implementation Sequence

1. **Growth strategy classes** — `GrowthLEAPS`, `BearPutSpread`, `GrowthEquity` added to registry
2. **Category system** — `category` field on universe config and ticker_state, position sizer reads it
3. **Growth WF metrics** — CAGR, Sortino computation; adapted `compute_folds` and `aggregate` for growth params
4. **Growth gate in plateau.py** — `classify()` checks category and applies growth thresholds
5. **Capital pool separation** — position sizer sizes against regime-driven growth/income split
6. **Net exposure tracking** — `net_exposure()` helper, integrated into position sizer
7. **Proposer adaptation** — category-aware strategy menu and prompt guidance
8. **TSLA data backfill** — 5 years of daily bars, synthetic options pricing
9. **Evolver run on TSLA** — discover growth strategies with the new pipeline
10. **LLM advisory layer** — proposer uses regime briefs + term structure to suggest specific strikes/expiries (enhancement after core pipeline works)
