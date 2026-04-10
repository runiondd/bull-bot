"""Scheduler — the outer loop."""
from __future__ import annotations
import logging, sqlite3, time, traceback
from typing import Any
from bullbot import clock, config, nightly
from bullbot.evolver import iteration as evolver_iteration
from bullbot.risk import kill_switch

log = logging.getLogger("bullbot.scheduler")


def _record_iteration_failure(conn, ticker, phase, exc):
    conn.execute(
        "INSERT INTO iteration_failures (ts, ticker, phase, exc_type, exc_message, traceback) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (int(time.time()), ticker, phase, type(exc).__name__, str(exc), traceback.format_exc()),
    )


def _dispatch_ticker(conn, ticker, anthropic_client, data_client):
    row = conn.execute("SELECT * FROM ticker_state WHERE ticker=?", (ticker,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES (?, 'discovering', ?)",
            (ticker, int(time.time())),
        )
        row = conn.execute("SELECT * FROM ticker_state WHERE ticker=?", (ticker,)).fetchone()
    phase = row["phase"]
    if row["retired"]:
        return
    if phase == "discovering":
        evolver_iteration.run(conn, anthropic_client, data_client, ticker)
        return
    # paper_trial/live: dispatch to engine.step (skipped in v1 scheduler tests)


def tick(conn, anthropic_client, data_client, universe=None):
    if kill_switch.is_tripped(conn):
        return
    if kill_switch.should_trip_now(conn):
        kill_switch.trip(conn, reason="pre_tick_check")
        return
    universe = universe or config.UNIVERSE
    for ticker in universe:
        try:
            _dispatch_ticker(conn, ticker, anthropic_client, data_client)
        except Exception as e:
            log.warning("ticker %s failed: %s", ticker, e)
            try:
                _record_iteration_failure(conn, ticker, "unknown", e)
            except Exception:
                log.exception("failed to record iteration_failure")
            continue
