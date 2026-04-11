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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic
from bullbot import config, scheduler
from bullbot.data import cache, fetchers, options_backfill
from bullbot.db import connection as db_connection

log = logging.getLogger("backfill")

BARS_TICKERS = ["SPY"] + config.REGIME_DATA_TICKERS
BARS_TICKERS = list(dict.fromkeys(BARS_TICKERS))  # dedupe preserving order

BARS_LIMIT = 2500


def _backfill_vix_yahoo(conn: sqlite3.Connection) -> int:
    """Fetch VIX from Yahoo Finance and persist to bars table."""
    log.info("  -> Falling back to Yahoo Finance for VIX...")
    bars = fetchers.fetch_vix_bars_yahoo(limit=BARS_LIMIT)
    for b in bars:
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (b.ticker, b.timeframe, b.ts, b.open, b.high, b.low, b.close, b.volume),
        )
    return len(bars)


def backfill_bars(conn: sqlite3.Connection, client: fetchers._ClientLike) -> None:
    log.info("=== BACKFILLING DAILY BARS (%d tickers) ===", len(BARS_TICKERS))
    for i, ticker in enumerate(BARS_TICKERS, 1):
        log.info("[%d/%d] Fetching bars for %s (limit=%d)...", i, len(BARS_TICKERS), ticker, BARS_LIMIT)
        try:
            bars = cache.get_daily_bars(conn, client, ticker, limit=BARS_LIMIT)
            log.info("  -> %s: %d bars loaded", ticker, len(bars))
        except Exception as e:
            if ticker == "VIX":
                try:
                    count = _backfill_vix_yahoo(conn)
                    log.info("  -> VIX: %d bars loaded (Yahoo Finance)", count)
                except Exception as e2:
                    log.error("  -> VIX Yahoo fallback also FAILED: %s", e2)
            else:
                log.error("  -> %s FAILED: %s", ticker, e)
    conn.commit()

    log.info("=== BARS COVERAGE REPORT ===")
    for ticker in BARS_TICKERS:
        row = conn.execute(
            "SELECT COUNT(*), MIN(date(ts, 'unixepoch')), MAX(date(ts, 'unixepoch')) "
            "FROM bars WHERE ticker=? AND timeframe='1d'", (ticker,),
        ).fetchone()
        log.info("  %6s: %4d bars  (%s -> %s)", ticker, row[0], row[1], row[2])


def backfill_options(conn: sqlite3.Connection, client: fetchers._ClientLike) -> None:
    row = conn.execute(
        "SELECT close FROM bars WHERE ticker='SPY' AND timeframe='1d' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    if row is None:
        log.error("No SPY bars in database — run --bars-only first")
        return
    spot = row[0]

    end = date.today()
    start = end - timedelta(days=config.WF_WINDOW_MONTHS * 30 + 60)

    log.info("=== BACKFILLING SPY OPTIONS ===")
    log.info("  spot=%.2f  window=%s -> %s", spot, start, end)

    result = options_backfill.run(
        conn=conn, client=client, ticker="SPY",
        spot=spot, start=start, end=end,
        strike_range_fraction=0.10, strike_step=1.0,
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
    log.info("=== RUNNING EVOLVER ON SPY (%d iterations) ===", iterations)
    for i in range(1, iterations + 1):
        log.info("--- Evolver iteration %d/%d ---", i, iterations)
        try:
            scheduler.tick(conn=conn, anthropic_client=anthropic_client,
                           data_client=uw_client, universe=["SPY"])
        except Exception as e:
            log.exception("Iteration %d failed: %s", i, e)
        time.sleep(2)

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

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

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
