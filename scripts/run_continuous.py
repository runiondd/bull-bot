"""Continuous daemon: scheduler.tick on an hourly cadence with heartbeat.

Companion to ``scripts/run_one_tick.py`` (the cron one-shot) and
``bullbot.main`` (the legacy infinite-loop entry point). This script is
the long-running daemon driven by F.3's supervisor cron: it ticks the
scheduler, writes an ISO-8601 UTC heartbeat after each round, then
sleeps. If three rounds crash inside an hour the daemon exits non-zero
so the supervisor can decide whether to restart.

Usage::

    python scripts/run_continuous.py                       # loop forever
    python scripts/run_continuous.py --once                # one round, exit
    python scripts/run_continuous.py --heartbeat-path PATH

Heartbeat file defaults to ``cache/last_continuous_run.txt`` — that's
what F.3's mentor health check reads.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bullbot import clock, config, scheduler  # noqa: E402

log = logging.getLogger("bullbot.run_continuous")

# Restart back-off: if this many crashes happen inside this many
# seconds, run_loop returns non-zero so the supervisor can intervene.
MAX_CRASHES = 3
CRASH_WINDOW_SEC = 3600

_SHUTDOWN = False


def _handle_sigterm(signum: int, frame: Any) -> None:
    global _SHUTDOWN
    _SHUTDOWN = True
    log.info("received signal %s, setting shutdown flag", signum)


def _write_heartbeat(heartbeat_path: Path) -> None:
    """Atomically write an ISO-8601 UTC timestamp to ``heartbeat_path``."""
    heartbeat_path = Path(heartbeat_path)
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    tmp = heartbeat_path.with_suffix(heartbeat_path.suffix + ".tmp")
    tmp.write_text(ts)
    tmp.replace(heartbeat_path)


def run_one_round(
    heartbeat_path: Path,
    conn: Any = None,
    anthropic_client: Any = None,
    data_client: Any = None,
) -> None:
    """Run one scheduler.tick and write the heartbeat.

    The three client args are injection seams for tests and for
    ``run_loop`` which builds them once per process. When called as a
    one-shot from the CLI (``--once``) all three are built inside
    ``main`` before this function is called.
    """
    log.info("round start")
    t0 = time.time()
    scheduler.tick(
        conn=conn,
        anthropic_client=anthropic_client,
        data_client=data_client,
    )
    log.info("round complete in %.1fs", time.time() - t0)
    _write_heartbeat(Path(heartbeat_path))


def _sleep_for_market() -> int:
    """Choose sleep length based on market hours, matching bullbot.main."""
    if clock.is_market_open_now():
        return config.TICK_INTERVAL_MARKET_SEC
    return config.TICK_INTERVAL_OFFHOURS_SEC


def run_loop(
    heartbeat_path: Path,
    sleep_seconds: int | None = None,
    conn: Any = None,
    anthropic_client: Any = None,
    data_client: Any = None,
) -> int:
    """Repeatedly run rounds until shutdown or the crash budget is spent.

    Returns 0 on a clean shutdown (SIGTERM/SIGINT), non-zero if the
    crash back-off trips (>= MAX_CRASHES inside CRASH_WINDOW_SEC).
    """
    crashes: deque[float] = deque(maxlen=MAX_CRASHES)
    while not _SHUTDOWN:
        try:
            run_one_round(
                heartbeat_path=heartbeat_path,
                conn=conn,
                anthropic_client=anthropic_client,
                data_client=data_client,
            )
        except Exception as e:
            now = time.time()
            crashes.append(now)
            log.exception("round raised: %s", e)
            if (
                len(crashes) == MAX_CRASHES
                and (now - crashes[0]) <= CRASH_WINDOW_SEC
            ):
                log.error(
                    "%d crashes within %ds — exiting for supervisor restart",
                    MAX_CRASHES,
                    CRASH_WINDOW_SEC,
                )
                return 1
        if _SHUTDOWN:
            break
        nap = sleep_seconds if sleep_seconds is not None else _sleep_for_market()
        if nap > 0:
            time.sleep(nap)
    return 0


def _build_clients() -> tuple[Any, Any, Any]:
    """Construct the real DB / Anthropic / UW clients. Mirrors bullbot.main."""
    import anthropic
    from bullbot.data import fetchers
    from bullbot.db import connection as db_connection

    conn = db_connection.open_persistent_connection(config.DB_PATH)
    anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    uw_client = fetchers.UWHttpClient(api_key=config.UW_API_KEY)
    return conn, anthropic_client, uw_client


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--heartbeat-path",
        default=str(config.DB_PATH.parent / "last_continuous_run.txt"),
        help="path to write the ISO-8601 heartbeat after each round",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one round and exit (cron-compatible mode)",
    )
    args = parser.parse_args(argv)

    if not config.ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set — cannot run scheduler tick")
        return 1

    conn, anthropic_client, data_client = _build_clients()
    try:
        if args.once:
            try:
                run_one_round(
                    heartbeat_path=Path(args.heartbeat_path),
                    conn=conn,
                    anthropic_client=anthropic_client,
                    data_client=data_client,
                )
                return 0
            except Exception as e:
                log.exception("one-shot round raised: %s", e)
                return 1
        return run_loop(
            heartbeat_path=Path(args.heartbeat_path),
            conn=conn,
            anthropic_client=anthropic_client,
            data_client=data_client,
        )
    finally:
        log.info("closing DB")
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
