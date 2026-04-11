# SPY Backfill & Evolver Run

**Date:** 2026-04-11
**Goal:** Backfill all required market data for SPY and regime signals, then run the evolver to discover strategies.

## Context

The database is empty (schema not initialized). The evolver has never been run for a meaningful number of iterations. Before it can run, we need:
- Daily bars for 15 tickers (SPY + 14 regime data tickers)
- Historical options chain data for SPY (the backtest needs real bid/ask for fills)

## Steps

### 1. Initialize Database Schema

Run `schema.sql` against `cache/bullbot.db` to create all tables and indexes.

### 2. Backfill Daily Bars (15 tickers)

Tickers: SPY, VIX, XLK, XLF, XLE, XLV, XLI, XLC, XLY, XLP, XLU, XLRE, XLB, TLT, HYG

Use `cache.get_daily_bars()` with `limit=2500` for each ticker. This fetches from UW and stores in the `bars` table via the read-through cache. One API call per ticker.

**Target:** 252+ bars per ticker for full regime signal computation (252-day VIX percentile, 50-day SMA for breadth, 20-day momentum).

### 3. Backfill SPY Options Chain

Use `options_backfill.run()` with:
- `ticker="SPY"`
- `spot` = latest SPY close from bars
- `start` / `end` = covering the walk-forward window (24 months)
- Default rate limiting (0.1s / 10 RPS)

This enumerates Friday expiries × strikes (±20% of spot, $1 steps) and fetches historical snapshots from UW. Heavy API usage — could take significant time.

### 4. Run Evolver on SPY

Use `scheduler.tick()` or call `evolver.iteration.run()` directly. This will:
1. Compute market signals from regime data
2. Generate market + ticker regime briefs via LLM
3. Propose a strategy variant via LLM
4. Walk-forward backtest against historical options data
5. Classify result (edge / plateau / no_edge)

Run multiple iterations to give the evolver a real chance to discover edge.

## IV Surface

The `iv_surface` table has no active backfill. The engine falls back to `iv_rank=50.0` when empty. Acceptable for initial runs — can be addressed later.

## Success Criteria

- All 15 tickers have 252+ daily bars in database
- SPY options chain populated with enough data for walk-forward backtests to execute trades
- At least one full evolver iteration completes end-to-end on SPY
- Evolver can run continuously without data-related failures

## Risks

- **UW API rate limits:** Options backfill generates many requests. Existing 10 RPS limit + exponential backoff should handle this.
- **Options data gaps:** Some strike/expiry combos may return no data from UW. The backfill handles this gracefully (skips empty responses).
- **LLM costs:** Each evolver iteration uses ~$0.10 for the proposer + regime brief costs. Budget awareness needed for extended runs.
