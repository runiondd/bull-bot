# SPY Backfill & Evolver Run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backfill daily bars for 15 tickers + SPY options chain, then run the evolver on SPY to discover strategies.

**Architecture:** Uses existing `cache.get_daily_bars()` for bar backfill, `options_backfill.run()` for options, and `scheduler.tick()` to drive evolver iterations. A new `scripts/backfill_and_run.py` script orchestrates these steps.

**Tech Stack:** Python, SQLite, Unusual Whales API, Anthropic API

---

### Task 1: Create Backfill Script

**Files:**
- Create: `scripts/backfill_and_run.py`

This script orchestrates the entire pipeline: init DB, backfill bars, backfill options, run evolver.

- [ ] **Step 1: Create the script**

```python
"""
Backfill market data and run the evolver on SPY.

Usage:
    python scripts/backfill_and_run.py              # full pipeline
    python scripts/backfill_and_run.py --bars-only   # just backfill bars
    python scripts/backfill_and_run.py --options-only # just backfill options
    python scripts/backfill_and_run.py --evolver-only # just run evolver
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic
from bullbot import config, scheduler
from bullbot.data import cache, fetchers, options_backfill
from bullbot.db import connection as db_connection

log = logging.getLogger("backfill")

# All tickers needed for regime signals + SPY
BARS_TICKERS = ["SPY"] + config.REGIME_DATA_TICKERS
# Remove duplicates while preserving order
BARS_TICKERS = list(dict.fromkeys(BARS_TICKERS))

BARS_LIMIT = 2500  # max UW supports


def backfill_bars(conn: sqlite3.Connection, client: fetchers._ClientLike) -> None:
    """Fetch daily bars for all regime + universe tickers."""
    log.info("=== BACKFILLING DAILY BARS (%d tickers) ===", len(BARS_TICKERS))
    for i, ticker in enumerate(BARS_TICKERS, 1):
        log.info("[%d/%d] Fetching bars for %s (limit=%d)...",
                 i, len(BARS_TICKERS), ticker, BARS_LIMIT)
        try:
            bars = cache.get_daily_bars(conn, client, ticker, limit=BARS_LIMIT)
            log.info("  -> %s: %d bars loaded", ticker, len(bars))
        except Exception as e:
            log.error("  -> %s FAILED: %s", ticker, e)
    conn.commit()

    # Report coverage
    log.info("=== BARS COVERAGE REPORT ===")
    for ticker in BARS_TICKERS:
        row = conn.execute(
            "SELECT COUNT(*), MIN(date(ts, 'unixepoch')), MAX(date(ts, 'unixepoch')) "
            "FROM bars WHERE ticker=? AND timeframe='1d'",
            (ticker,),
        ).fetchone()
        log.info("  %6s: %4d bars  (%s -> %s)", ticker, row[0], row[1], row[2])


def backfill_options(conn: sqlite3.Connection, client: fetchers._ClientLike) -> None:
    """Backfill SPY options chain for the walk-forward window."""
    # Get latest SPY close for spot price
    row = conn.execute(
        "SELECT close FROM bars WHERE ticker='SPY' AND timeframe='1d' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    if row is None:
        log.error("No SPY bars in database — run --bars-only first")
        return
    spot = row[0]

    # Walk-forward needs 24 months of data
    end = date.today()
    start = end - timedelta(days=config.WF_WINDOW_MONTHS * 30 + 60)  # extra buffer

    log.info("=== BACKFILLING SPY OPTIONS ===")
    log.info("  spot=%.2f  window=%s -> %s", spot, start, end)

    result = options_backfill.run(
        conn=conn, client=client, ticker="SPY",
        spot=spot, start=start, end=end,
    )
    conn.commit()

    log.info("=== OPTIONS BACKFILL RESULT ===")
    log.info("  symbols_tried:     %d", result["symbols_tried"])
    log.info("  symbols_with_data: %d", result["symbols_with_data"])
    log.info("  rows_written:      %d", result["rows_written"])


def run_evolver(
    conn: sqlite3.Connection,
    anthropic_client: anthropic.Anthropic,
    uw_client: fetchers._ClientLike,
    iterations: int = 5,
) -> None:
    """Run evolver iterations on SPY."""
    log.info("=== RUNNING EVOLVER ON SPY (%d iterations) ===", iterations)
    for i in range(1, iterations + 1):
        log.info("--- Evolver iteration %d/%d ---", i, iterations)
        try:
            scheduler.tick(conn=conn, anthropic_client=anthropic_client,
                           data_client=uw_client, universe=["SPY"])
        except Exception as e:
            log.exception("Iteration %d failed: %s", i, e)
        # Brief pause between iterations
        time.sleep(2)

    # Report state
    row = conn.execute("SELECT * FROM ticker_state WHERE ticker='SPY'").fetchone()
    if row:
        log.info("=== SPY STATE ===")
        log.info("  phase:           %s", row["phase"])
        log.info("  iteration_count: %d", row["iteration_count"])
        log.info("  best_pf_oos:     %s", row["best_pf_oos"])
        log.info("  cumulative_llm:  $%.4f", row["cumulative_llm_usd"])


def main():
    parser = argparse.ArgumentParser(description="Backfill data and run evolver")
    parser.add_argument("--bars-only", action="store_true", help="Only backfill daily bars")
    parser.add_argument("--options-only", action="store_true", help="Only backfill SPY options")
    parser.add_argument("--evolver-only", action="store_true", help="Only run evolver")
    parser.add_argument("--iterations", type=int, default=5, help="Number of evolver iterations (default: 5)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    conn = db_connection.open_persistent_connection(config.DB_PATH)
    uw_client = fetchers.UWHttpClient(api_key=config.UW_API_KEY)
    anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    try:
        specific = args.bars_only or args.options_only or args.evolver_only

        if not specific or args.bars_only:
            backfill_bars(conn, uw_client)

        if not specific or args.options_only:
            backfill_options(conn, uw_client)

        if not specific or args.evolver_only:
            run_evolver(conn, anthropic_client, uw_client, iterations=args.iterations)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify script parses correctly**

Run: `python scripts/backfill_and_run.py --help`
Expected: help text showing all flags

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill_and_run.py
git commit -m "add backfill_and_run script for data loading and evolver execution"
```

### Task 2: Run Bars Backfill

- [ ] **Step 1: Backfill daily bars for all 15 tickers**

Run: `python scripts/backfill_and_run.py --bars-only`

Expected: Each ticker logs bar count. All 15 should have 250+ bars. Watch for:
- UW API errors (rate limits, auth failures)
- VIX may return fewer bars than equity tickers

- [ ] **Step 2: Verify bars coverage**

Run:
```python
python3 -c "
import sqlite3
conn = sqlite3.connect('cache/bullbot.db')
conn.row_factory = sqlite3.Row
for row in conn.execute(\"\"\"
    SELECT ticker, COUNT(*) as cnt,
           MIN(date(ts, 'unixepoch')) as first_date,
           MAX(date(ts, 'unixepoch')) as last_date
    FROM bars WHERE timeframe='1d'
    GROUP BY ticker ORDER BY ticker
\"\"\").fetchall():
    print(f'{row[\"ticker\"]:6s}: {row[\"cnt\"]:5d} bars  ({row[\"first_date\"]} -> {row[\"last_date\"]})')
conn.close()
"
```

Expected: 15 tickers, each with 250+ bars covering at least 1 year of history.

### Task 3: Run SPY Options Backfill

- [ ] **Step 1: Backfill SPY options chain**

Run: `python scripts/backfill_and_run.py --options-only`

This will take significant time (potentially 30+ minutes) due to:
- Enumerating ~100+ expiry Fridays × ~200+ strikes × 2 kinds = thousands of symbols
- Rate-limited at 10 RPS (0.1s sleep per request)

Watch the logs for progress. Expected output: `symbols_tried`, `symbols_with_data`, `rows_written`.

- [ ] **Step 2: Verify options coverage**

Run:
```python
python3 -c "
import sqlite3
conn = sqlite3.connect('cache/bullbot.db')
r = conn.execute('SELECT COUNT(*) FROM option_contracts WHERE ticker=\"SPY\"').fetchone()
print(f'Total SPY option rows: {r[0]}')
r = conn.execute('SELECT COUNT(DISTINCT expiry) FROM option_contracts WHERE ticker=\"SPY\"').fetchone()
print(f'Distinct expiries: {r[0]}')
r = conn.execute('SELECT MIN(date(ts, \"unixepoch\")), MAX(date(ts, \"unixepoch\")) FROM option_contracts WHERE ticker=\"SPY\"').fetchone()
print(f'Date range: {r[0]} -> {r[1]}')
conn.close()
"
```

Expected: Thousands of option rows, multiple expiries, covering the walk-forward window.

### Task 4: Run Evolver on SPY

- [ ] **Step 1: Run 5 evolver iterations**

Run: `python scripts/backfill_and_run.py --evolver-only --iterations 5`

Watch for:
- Regime signals computation succeeding
- Market + ticker brief generation (LLM calls)
- Strategy proposal (LLM call)
- Walk-forward backtest completing
- Classification result (edge/plateau/no_edge)

- [ ] **Step 2: Check results**

Run:
```python
python3 -c "
import sqlite3
conn = sqlite3.connect('cache/bullbot.db')
conn.row_factory = sqlite3.Row

# Ticker state
row = conn.execute('SELECT * FROM ticker_state WHERE ticker=\"SPY\"').fetchone()
if row:
    print(f'Phase: {row[\"phase\"]}')
    print(f'Iterations: {row[\"iteration_count\"]}')
    print(f'Best PF OOS: {row[\"best_pf_oos\"]}')
    print(f'LLM spend: \${row[\"cumulative_llm_usd\"]:.4f}')

# Proposals
print()
for p in conn.execute(
    'SELECT iteration, ep.strategy_id, s.class_name, s.params, ep.pf_oos, ep.trade_count, ep.passed_gate '
    'FROM evolver_proposals ep JOIN strategies s ON ep.strategy_id = s.id '
    'WHERE ep.ticker=\"SPY\" ORDER BY ep.iteration'
).fetchall():
    gate = 'PASS' if p['passed_gate'] else 'FAIL'
    print(f'  iter {p[\"iteration\"]}: {p[\"class_name\"]} pf_oos={p[\"pf_oos\"]} trades={p[\"trade_count\"]} [{gate}]')

conn.close()
"
```

Expected: 5 proposals with backtest metrics. Most will likely fail the gate (PF OOS >= 1.3, trades >= 30) — that's normal early in discovery.

- [ ] **Step 3: Commit state report**

```bash
git add scripts/backfill_and_run.py
git commit -m "session 7: initial SPY backfill and evolver run complete"
```

### Task 5: Extended Evolver Run (if pipeline works)

If Tasks 1-4 complete successfully, run a longer evolver session to give it a real chance at finding edge.

- [ ] **Step 1: Run 20+ iterations**

Run: `python scripts/backfill_and_run.py --evolver-only --iterations 20`

Monitor for:
- Improvement in PF OOS across iterations (proposer learning from history)
- Phase transition from `discovering` to `paper_trial` (means edge found)
- LLM cost accumulation (each iteration ~$0.10-0.15)
- Any data-related failures that need addressing

- [ ] **Step 2: Report final state and update handoff**

Check final ticker state, total proposals, best strategy found, total LLM spend.
