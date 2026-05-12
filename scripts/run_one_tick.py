"""Run a single scheduler tick against the production DB and exit.

This is the one-shot version of ``python -m bullbot.main`` (which runs the
tick in an infinite loop). Designed to be invoked by a cron-style scheduler
(e.g. the Claude scheduled-task feature) every N minutes during market hours.

Each tick:
  * refreshes market + per-ticker regime briefs (cached, idempotent within a day)
  * runs the evolver iteration for any ticker in ``discovering`` phase
  * dispatches paper-trial fills for any ticker in ``paper_trial`` phase

Usage:
    python scripts/run_one_tick.py                       # all tickers in config.UNIVERSE
    python scripts/run_one_tick.py SPY TSLA              # restrict to these tickers
    python scripts/run_one_tick.py --skip-regime         # don't call LLM for regime briefs

Cost: typically $0.05-$0.30 per tick depending on how many proposals fire.
Writes to ``cache/bullbot.db`` — same DB the dashboard reads.
Exit 0 on success (including soft-failures recorded in iteration_failures);
exit 1 only on hard infrastructure errors (DB open, missing API key, etc.).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bullbot import config, scheduler
from bullbot.db import connection as db_connection


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("run_one_tick")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="*", help="optional subset of tickers to tick")
    parser.add_argument(
        "--skip-regime",
        action="store_true",
        help="skip regime-brief refresh (saves ~$0.05 / tick if briefs are still fresh)",
    )
    args = parser.parse_args(argv)

    if not config.ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set — cannot run scheduler tick")
        return 1

    import anthropic
    anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # UW is canceled (2026-05-11) — pass None. evolver.iteration.run does not
    # call any methods on data_client; the snapshot path reads from the DB.
    data_client = None

    conn = db_connection.open_persistent_connection(config.DB_PATH)
    try:
        universe = args.tickers or list(config.UNIVERSE)
        log.info("tick start: %d tickers (%s)", len(universe), ",".join(universe))
        t0 = time.time()
        scheduler.tick(
            conn=conn,
            anthropic_client=anthropic_client,
            data_client=data_client,
            universe=universe,
        )
        log.info("tick complete in %.1fs", time.time() - t0)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
