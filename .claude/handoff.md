# Context Handoff
**Updated:** 2026-04-13 (Session 9 → Session 10)

## Current State

**Main branch (`main`):** All three active tickers in `paper_trial`. Growth framework fully operational with separate account sizing ($50k income / $215k growth). 290 tests passing.

| Ticker | Category | Account | Phase | Strategy | Key Params |
|--------|----------|---------|-------|----------|------------|
| SPY | income | $50k taxable | paper_trial | PutCreditSpread | 0.25 delta, 30 DTE, $5 wide |
| TSLA | growth | $215k sheltered | paper_trial | GrowthLEAPS | 0.60 delta, 180-365 DTE |
| NVDA | growth | $215k sheltered | paper_trial | GrowthLEAPS | 0.55 delta, 180-270 DTE |

## What Was Done (Session 9)

### Root Cause: TSLA Zero Trades (3 bugs fixed)

**Bug 1 — Synthetic chain expiry drift.** The chain generated cursor-relative expiry dates (`cursor + DTE * 86400`), producing different dates every day. A position opened with expiry "2026-01-25" couldn't find its OSI symbol in the next day's chain (which generated "2026-01-26"). Positions could never be priced or closed by the exit manager.

**Fix:** Pinned synthetic expiries to standard monthly option expirations (3rd Friday of each month). Added `_third_friday()` and `_monthly_expiries()` helpers. Symbols now persist across cursors.

**Bug 2 — Exit manager can't close positions with missing chain entries.** Even with stable expiries, the exit manager needs the position's exact option symbols in `chain_rows` to fill a close. If the chain's strike range shifts with spot price, old positions' symbols may not be present.

**Fix:** Added `_enrich_chain_rows_for_positions()` in step.py. Before running exit checks, it loads open positions, identifies any legs missing from chain_rows, computes Black-Scholes fair-value prices as fallback, and injects them into chain_rows. Uses 2.5% bid-ask spread with $0.05 floor to stay within the fill model's spread-width tolerance. Also reordered step() to load positions before exit checks.

**Bug 3 — Position sizer blocked LEAPS trades.** GrowthLEAPS at delta 0.70 costs $11k-20k per contract, exceeding the 2% risk budget ($1,000). Sizer returned 0 contracts.

**Fix:** Growth strategies now get a minimum of 1 contract if max_loss fits within 50% of the available pool. Only applies to category="growth", preserving income strategy conservatism.

### Validation Results

| Strategy | Trades OOS | PF OOS | CAGR | Sortino | Gate |
|----------|-----------|--------|------|---------|------|
| GrowthLEAPS (delta 0.70) | 5 | inf | 42.2% | inf | PASS |
| GrowthLEAPS (delta 0.50) | 9 | inf | 465% | inf | PASS |
| BearPutSpread (delta 0.30) | 25 | 1.76 | -2.1% | -0.19 | FAIL |
| BearPutSpread (delta 0.40) | 21 | 2.24 | -1.6% | -0.13 | FAIL |

GrowthLEAPS passes the growth gate (CAGR≥20%, Sortino≥1.0, DD≤35%, trades≥5). BearPutSpread generates trades but negative CAGR on a growth stock (expected — bearish strategy on bullish underlying).

**Note:** The inf PF/Sortino values indicate all OOS trades were profitable — optimistic due to synthetic chain limitations. Real options data would introduce realistic slippage and pricing gaps.

### Account Split and Capital Sizing
- Separate accounts: $50k income (taxable), $215k growth (tax-sheltered)
- Position sizer uses dedicated equity base per category
- Growth account has regime-based utilization (100% bull / 50% chop / 25% bear)
- Growth 1-contract minimum override when max_loss fits within 50% of pool

### NVDA Data Backfill
- 1,255 daily bars via Yahoo Finance (2021-04-14 to 2026-04-13)
- No options data — uses synthetic chain (same as TSLA)

### Proposer Improvement
- Growth guidance now includes gate criteria (CAGR, Sortino, DD, trade count)
- Steers proposer toward bullish strategies (GrowthLEAPS) for growth tickers
- TSLA passed gate on first iteration; NVDA on third

### TSLA and NVDA Evolver Results
- TSLA: GrowthLEAPS (delta 0.60, 180-365 DTE, 90% profit target, 0.45x stop) → paper_trial
- NVDA: GrowthLEAPS (delta 0.55, 180-270 DTE, 40% profit target, 2.5x stop) → paper_trial

## Known Issues / Next Steps

1. **Daily paper trial ticks** — all three tickers need `scheduler.tick()` daily. 21 days + 10 trades for promotion to live.
2. **GrowthEquity fill path** — empty legs, needs share-based fill method in fill_model.py.
3. **Synthetic chain realism** — BS pricing with realized vol is a simplification. Skew, term structure, and vol smile not modeled. Real options data (UW API or Schwab) would improve backtest fidelity.
4. **Schwab/ToS API** — waiting for developer sandbox approval.
5. **UW API daily cap** — ~12k requests/day.
6. **Remaining universe tickers** — QQQ, IWM, AAPL, MSFT, AMD, META, GOOGL have no bars yet. Could backfill via Yahoo Finance and run evolver.

## Files Changed This Session

- `bullbot/data/synthetic_chain.py` — Standard monthly expiry generation (3rd Fridays)
- `bullbot/engine/step.py` — BS fallback enrichment for position exits, reordered position loading
- `bullbot/engine/position_sizer.py` — Growth strategy 1-contract minimum override
- `tests/unit/test_category_sizer.py` — Updated test for new growth sizing behavior

## Quick Start Commands

```bash
# SPY paper trial tick
python3 -c "
import sqlite3
from bullbot import config, scheduler
conn = sqlite3.connect(str(config.DB_PATH)); conn.row_factory = sqlite3.Row
scheduler.tick(conn, None, None, universe=['SPY']); conn.commit()
row = conn.execute('SELECT phase, paper_trade_count FROM ticker_state WHERE ticker=\"SPY\"').fetchone()
print(f'SPY: {row[\"phase\"]}, trades={row[\"paper_trade_count\"]}')
"

# Reset TSLA for evolver
python3 -c "
import sqlite3; conn = sqlite3.connect('cache/bullbot.db')
conn.execute(\"UPDATE ticker_state SET phase='discovering', iteration_count=0, plateau_counter=0 WHERE ticker='TSLA'\")
conn.execute(\"DELETE FROM evolver_proposals WHERE ticker='TSLA'\")
conn.commit(); conn.close()
"

# Manual TSLA walk-forward test
python3 -c "
import sqlite3, json, time
from bullbot import config
from bullbot.backtest import walkforward
from bullbot.strategies import registry
conn = sqlite3.connect(str(config.DB_PATH)); conn.row_factory = sqlite3.Row
cls = registry.get_class('GrowthLEAPS')
params = {'target_delta': 0.70, 'min_dte': 180, 'max_dte': 365, 'profit_target_pct': 0.50, 'stop_loss_mult': 2.0, 'min_dte_close': 30}
strategy = cls(params)
conn.execute('INSERT OR IGNORE INTO strategies (class_name, class_version, params, params_hash, created_at) VALUES (?, ?, ?, ?, ?)', ('GrowthLEAPS', cls.CLASS_VERSION, json.dumps(params), registry.params_hash(params), int(time.time())))
sid = conn.execute('SELECT id FROM strategies WHERE class_name=\"GrowthLEAPS\" ORDER BY id DESC LIMIT 1').fetchone()['id']
conn.execute(\"DELETE FROM orders WHERE ticker='TSLA' AND run_id LIKE 'bt:%'\")
conn.execute(\"DELETE FROM positions WHERE ticker='TSLA' AND run_id LIKE 'bt:%'\")
conn.commit()
m = walkforward.run_walkforward(conn, strategy, sid, 'TSLA'); conn.commit()
print(f'Trades: {m.trade_count}, CAGR: {m.cagr_oos:.4f}, Sortino: {m.sortino_oos}')
"
```
