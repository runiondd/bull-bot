"""
Bull-Bot v3 main entry point.
    python -m bullbot.main

Initializes DB, opens persistent sqlite3 connection, creates Anthropic and
UW clients, enters the scheduler loop.
"""
from __future__ import annotations
import logging, signal, sys, time
import anthropic
from bullbot import config, scheduler
from bullbot.data import fetchers
from bullbot.db import connection as db_connection

log = logging.getLogger("bullbot.main")
_SHUTDOWN = False


def _handle_sigterm(signum, frame):
    global _SHUTDOWN
    _SHUTDOWN = True
    log.info("received signal %s, setting shutdown flag", signum)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    log.info("Bull-Bot v3 starting. universe=%s", config.UNIVERSE)
    conn = db_connection.open_persistent_connection(config.DB_PATH)
    anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    uw_client = fetchers.UWHttpClient(api_key=config.UW_API_KEY)
    try:
        while not _SHUTDOWN:
            try:
                scheduler.tick(conn=conn, anthropic_client=anthropic_client, data_client=uw_client)
            except Exception as e:
                log.exception("scheduler.tick raised: %s", e)
            from bullbot import clock
            if clock.is_market_open_now():
                time.sleep(config.TICK_INTERVAL_MARKET_SEC)
            else:
                time.sleep(config.TICK_INTERVAL_OFFHOURS_SEC)
    finally:
        log.info("main loop exiting, closing DB")
        conn.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
