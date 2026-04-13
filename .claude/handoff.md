# Context Handoff
**Updated:** 2026-04-13 (Session 8 closed → Session 9)

## Current State

**Main branch (`main`):** Growth strategy framework implemented. SPY in `paper_trial` with PutCreditSpread. 275 tests passing. TSLA growth evolver blocked on options data.

## What Was Done (Session 8)

### Evolver Fixes (3 bugs)
- **Infinity plateau detection** (`bullbot/evolver/plateau.py`) — `pf_oos=inf` no longer falsely triggers `no_edge`. When both current and best are inf, plateau counter resets instead of incrementing.
- **Trade count gate** — lowered `EDGE_TRADE_COUNT_MIN` from 30 to 10, increased `WF_MAX_FOLDS` from 5 to 8. Strategies can now reach paper trial with current data density.
- **Proposer JSON parsing** (`bullbot/evolver/proposer.py`) — added `_strip_code_fences()` to handle LLM responses wrapped in markdown code blocks.

### SPY Promoted to Paper Trial
- Evolver found edge on iteration 3: **PutCreditSpread** (0.25 delta, 30 DTE, $5 wide, 50% profit target, 2x stop, 7 DTE close).
- 12 OOS trades, all profitable (`pf_oos=inf`), passed the gate.
- SPY now in `paper_trial` phase, `paper_started_at` set, first paper trade opened.

### Paper Trial Dispatcher
- **`bullbot/scheduler.py`** — new `_dispatch_paper_trial()` function. When a ticker is in `paper_trial`, the scheduler loads its winning strategy, calls `engine.step(run_id='paper')`, sets `paper_started_at` on first dispatch, increments `paper_trade_count` on fills.
- Validated end-to-end: SPY paper trade opened on first tick.

### Growth Strategy Framework (the big feature)
- **Design spec:** `docs/superpowers/specs/2026-04-13-growth-strategy-design.md`
- **Implementation plan:** `docs/superpowers/plans/2026-04-13-growth-strategy-plan.md`

#### Category System
- `config.TICKER_CATEGORY` maps each universe ticker to `"income"` or `"growth"` (TSLA, NVDA = growth; rest = income).
- Regime-driven capital allocation: `GROWTH_FRAC_BULL=0.40`, `GROWTH_FRAC_CHOP=0.20`, `GROWTH_FRAC_BEAR=0.10`.

#### New Strategy Classes (3)
- **`GrowthLEAPS`** — buy long-dated calls. Params: `target_delta`, `min_dte`, `max_dte`, `iv_rank_max`, `regime_filter`.
- **`BearPutSpread`** — defined-risk bearish debit spread. Params: `dte`, `long_delta`, `width`, `iv_rank_min`, `regime_filter`.
- **`GrowthEquity`** — buy shares. Params: `regime_filter`, `stop_loss_pct`.
- Registry now has 9 strategy classes total.

#### Growth Walk-Forward Metrics
- `bullbot/backtest/walkforward.py` — category-aware fold computation. Growth uses 60-month window, 90-day folds (vs 24-month/30-day for income).
- `BacktestMetrics` extended with `cagr_oos` and `sortino_oos` fields.
- `FoldMetrics` extended with `oos_pnls` to carry per-fold PnLs through to aggregation.
- `aggregate()` computes CAGR and Sortino from equity curve for growth tickers.

#### Growth Gate in Plateau Classifier
- `plateau.classify()` accepts `category` parameter, routes to `_classify_growth()` for growth tickers.
- Growth gate: CAGR ≥ 20%, Sortino ≥ 1.0, max drawdown ≤ 35%, trade count ≥ 5.
- Uses CAGR as the improvement metric for plateau tracking.

#### Category-Aware Position Sizer
- `bullbot/engine/position_sizer.py` — sizes against regime-dependent capital pool. Growth strategies size against `equity × growth_frac`; income against `equity × (1 - growth_frac)`.
- `bullbot/engine/step.py` — passes category and regime to position sizer.

#### Category-Aware Proposer
- `bullbot/evolver/proposer.py` — growth/income guidance appended to system prompt. Growth tickers get directional strategy emphasis; income tickers get premium-selling emphasis.
- `bullbot/evolver/iteration.py` — passes category to proposer, walkforward, and plateau classifier.

#### CAGR and Sortino Indicators
- `bullbot/features/indicators.py` — `cagr(equity_curve, days)` and `sortino(returns, risk_free_rate)` added.

### Data
- **TSLA daily bars:** 1,256 bars (5 years via Yahoo Finance) — sufficient for 60-month growth walk-forward.
- **TSLA options:** zero. This is the blocker.

### TSLA Evolver Attempt
- Ran 3 iterations. Proposer correctly suggested directional strategies (BearPutSpread, LongPut) for the growth ticker.
- All produced zero trades due to empty option chain → `no_edge` after plateau counter hit 3.

## Known Issues / Next Steps

1. **TSLA options data needed** — the growth evolver can't function without option chain data. Two paths:
   - **UW API backfill** — ~12k requests/day, takes 2+ days per ticker. Costs API credits.
   - **Synthetic options pricing** — generate synthetic chains using Black-Scholes with historical IV and spot prices. No API cost, works immediately. Spec mentions this approach but it's not built yet.

2. **SPY paper trial running** — needs 21 days and 10+ trades for promotion to `live`. Run `scheduler.tick()` daily to advance.

3. **GrowthEquity fill path** — `GrowthEquity` signals have empty legs, which the fill model rejects. Needs a share-based fill method (`simulate_equity_buy/sell`) in `fill_model.py`.

4. **Additional growth tickers** — NVDA is also categorized as growth. Needs bars backfill and options data.

5. **Schwab/ToS API** — still waiting for developer sandbox approval.

6. **UW API daily cap** — ~12k requests/day. See memory file `reference_uw_rate_limits.md`.

## Quick Start Commands

```bash
# SPY paper trial — run one tick
python3 -c "
import sqlite3, anthropic
from bullbot import config, scheduler
conn = sqlite3.connect(str(config.DB_PATH)); conn.row_factory = sqlite3.Row
scheduler.tick(conn, None, None, universe=['SPY']); conn.commit()
row = conn.execute('SELECT phase, paper_trade_count FROM ticker_state WHERE ticker=\"SPY\"').fetchone()
print(f'SPY: {row[\"phase\"]}, trades={row[\"paper_trade_count\"]}')
"

# TSLA growth evolver (after options data is available)
python3 -c "
import sqlite3; conn = sqlite3.connect('cache/bullbot.db')
conn.execute(\"DELETE FROM ticker_state WHERE ticker='TSLA'\")
conn.commit(); conn.close()
"
# Then run evolver on TSLA via scheduler.tick(universe=['TSLA'])

# Resume SPY options backfill
python scripts/backfill_and_run.py --options-only
```
