"""Scheduler — the outer loop."""
from __future__ import annotations
import logging, sqlite3, time, traceback
from typing import Any
from bullbot import clock, config, nightly
from bullbot.evolver import iteration as evolver_iteration
from bullbot.features import regime_agent, regime_signals
from bullbot.risk import kill_switch

log = logging.getLogger("bullbot.scheduler")


def _today_ts() -> int:
    """Return midnight UTC epoch for today."""
    now = time.time()
    return int(now - (now % 86400))


def _load_bars_for_ticker(conn, ticker: str, limit: int = 252) -> list[dict]:
    """Load up to `limit` daily bars for `ticker`, oldest first."""
    rows = conn.execute(
        "SELECT * FROM bars WHERE ticker=? AND timeframe='1d' ORDER BY ts DESC LIMIT ?",
        (ticker, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _refresh_regime(conn, anthropic_client) -> None:
    """Compute and cache market + per-ticker regime briefs.

    Idempotent: skips if briefs are already cached for today's timestamp.
    Non-fatal: caller wraps in try/except.
    """
    ts = _today_ts()

    # --- Market brief ---
    cached = regime_agent.get_brief(conn, "market", ts)
    if cached is not None:
        log.debug("scheduler: market regime brief already cached for ts=%d", ts)
        return

    vix_bars = _load_bars_for_ticker(conn, "VIX")
    spy_bars = _load_bars_for_ticker(conn, "SPY")
    sector_bars = {etf: _load_bars_for_ticker(conn, etf) for etf in config.SECTOR_ETFS}
    hyg_bars = _load_bars_for_ticker(conn, "HYG")
    tlt_bars = _load_bars_for_ticker(conn, "TLT")

    market_signals = regime_signals.compute_market_signals(
        vix_bars=vix_bars,
        spy_bars=spy_bars,
        sector_bars=sector_bars,
        hyg_bars=hyg_bars,
        tlt_bars=tlt_bars,
    )
    if market_signals is None:
        log.warning("scheduler: insufficient data for market regime signals — skipping refresh")
        return

    market_brief = regime_agent.refresh_market_brief(conn, anthropic_client, market_signals, ts)

    # --- Per-ticker briefs ---
    for ticker in config.UNIVERSE:
        try:
            ticker_bars = _load_bars_for_ticker(conn, ticker)
            sector_etf = config.TICKER_SECTOR_MAP.get(ticker)
            sector_etf_bars = _load_bars_for_ticker(conn, sector_etf) if sector_etf else []

            iv_rows = conn.execute(
                "SELECT iv FROM iv_surface WHERE ticker=? ORDER BY ts DESC LIMIT 252",
                (ticker,),
            ).fetchall()
            iv_history = [r["iv"] for r in iv_rows]
            current_iv = iv_history[0] if iv_history else None

            ticker_signals = regime_signals.compute_ticker_signals(
                ticker=ticker,
                ticker_bars=ticker_bars,
                iv_history=iv_history,
                current_iv=current_iv,
                sector_etf_bars=sector_etf_bars,
            )
            if ticker_signals is None:
                log.warning("scheduler: insufficient data for ticker regime signals (%s) — skipping", ticker)
                continue

            regime_agent.refresh_ticker_brief(conn, anthropic_client, ticker_signals, market_brief, ts)
        except Exception as exc:
            log.warning("scheduler: ticker regime refresh failed for %s: %s", ticker, exc)


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
    try:
        _refresh_regime(conn, anthropic_client)
    except Exception:
        log.exception("scheduler: regime refresh failed — continuing with evolver")
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
