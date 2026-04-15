"""Refresh daily bars from Yahoo Finance for every tracked ticker.

Usage:
    python scripts/update_bars.py                  # refresh every ticker already in bars
    python scripts/update_bars.py NVDA TSLA SPY    # refresh only the listed tickers

Idempotent — safe to run multiple times per day. Upserts on
`(ticker, timeframe, ts)`.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bullbot import config
from bullbot.data import daily_refresh


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("update_bars")

    argv = argv if argv is not None else sys.argv[1:]
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        tickers = argv or daily_refresh.discover_tracked_tickers(conn)
        if not tickers:
            log.warning("no tickers to refresh (bars table is empty)")
            return 0
        log.info("refreshing %d tickers: %s", len(tickers), ",".join(sorted(tickers)))
        result = daily_refresh.refresh_all_bars(conn, tickers)
        total = sum(result.values())
        failures = [t for t, n in result.items() if n == 0]
        log.info("wrote %d bars across %d tickers", total, len(result))
        if failures:
            log.warning("failed: %s", ",".join(failures))
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
