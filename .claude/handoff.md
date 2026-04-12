# Context Handoff
**Updated:** 2026-04-12 (Session 7 closed → Session 8)

## Current State

**Main branch (`main`):** Exit manager implemented and working. 228 tests passing. Evolver pipeline runs end-to-end with real trades. SPY ticker_state at `no_edge` (needs reset for next run).

## What Was Done (Session 7)

### Data Infrastructure
- **`scripts/backfill_and_run.py`** — orchestrates DB init, bars backfill, options backfill, evolver. Supports `--bars-only`, `--options-only`, `--evolver-only`, `--iterations N`.
- **Daily bars backfilled** for 15 tickers (SPY + 14 regime). VIX via Yahoo Finance fallback (UW blocks index OHLC). All have 251+ bars covering 2025-04-10 → 2026-04-10.
- **SPY options backfilled** — 443k rows across 53 expiries (2025-05-16 → 2026-05-29). Hit UW daily cap (~12k requests/day). Missing 4 early April/May 2025 expiries.
- **Options backfill improvements** — rate limit resilience (catch 429s, exponential backoff, periodic commits), configurable strike range/step, newest-first ordering aligned with bar data.

### Exit Manager (the big feature)
- **Engine-level exit manager** (`bullbot/engine/exit_manager.py`) — checks open positions against per-position exit rules on every bar, before strategy.evaluate() runs.
- **Three exit conditions:** profit target (% of max profit), stop loss (multiple of credit), DTE close (days to expiry).
- **Exit rules stored per-position** at open time in `exit_rules` JSON column on positions table.
- **All 6 strategies** pass exit params from their `params` dict onto Signal, with config defaults (50% profit target, 2x stop loss, 7 DTE close).
- **Proposer prompt updated** to include exit params in strategy proposals.
- **PnL calculation bug fixed** — `pnl = open_price - net_close` was wrong for credit spreads; corrected to `pnl = -(open_price + net_close)`.
- **228 tests passing** (9 exit manager unit tests + 1 integration test + 218 existing).

### Evolver Results
- Pipeline validated: regime signals → LLM briefs → strategy proposal → walk-forward backtest → classification.
- Exit manager closing positions via profit target, stop loss, and DTE triggers.
- `pf_oos=inf` (all OOS trades profitable in small samples), `trade_count=3-5` per fold.

## Known Issues / Next Steps

1. **Plateau detection with `inf`** — when all OOS trades are profitable, `pf_oos=inf` and the plateau counter ticks up identically each iteration → `no_edge` after 3. The plateau detector needs to handle this case (e.g., treat `inf` as passing if trade_count > threshold).

2. **Low trade count** — only 3-5 OOS trades per fold, below the 30-trade minimum for edge detection. Options: tune WF parameters, increase options data density, or lower the threshold for initial discovery.

3. **Missing early options data** — 4 April/May 2025 expiries still missing (UW daily cap). Can retry backfill to fill gaps.

4. **UW API daily cap** — ~12k requests/day. Full options backfill for one ticker takes ~2 days. See memory file `reference_uw_rate_limits.md`.

5. **Schwab/ToS API** — still waiting for developer sandbox approval.

6. **Layer 2 web research** — not yet built.

## Quick Start Commands

```bash
# Resume options backfill (fills remaining gaps)
python scripts/backfill_and_run.py --options-only

# Reset SPY and run evolver
python3 -c "
import sqlite3; conn = sqlite3.connect('cache/bullbot.db')
conn.execute(\"DELETE FROM ticker_state WHERE ticker='SPY'\")
conn.execute(\"DELETE FROM evolver_proposals\"); conn.execute(\"DELETE FROM strategies\")
conn.execute(\"DELETE FROM orders\"); conn.execute(\"DELETE FROM positions\")
conn.commit(); conn.close()
"
python scripts/backfill_and_run.py --evolver-only --iterations 20
```
