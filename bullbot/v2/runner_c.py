"""v2 Phase C daily forward-mode dispatcher.

Sibling to bullbot.v2.runner (Phase A signal loop). Walks config.UNIVERSE
once per day, runs the full Phase C agent pipeline (signal → S/R → earnings
→ exits-on-held → vehicle.pick on flat → validate → open → MtM), persists
results, and writes one v2_position_mtm row per open position.

Per spec §4.2.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime as _datetime
from types import SimpleNamespace
from typing import Callable

from bullbot.v2 import exits, positions, vehicle

_log = logging.getLogger(__name__)


def _write_position_mtm(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    asof_ts: int,
    mtm_value: float,
    source: str,
) -> None:
    """Idempotent write to v2_position_mtm. PK is (position_id, asof_ts);
    INSERT OR REPLACE so re-running the daily MtM step overwrites cleanly."""
    conn.execute(
        "INSERT OR REPLACE INTO v2_position_mtm "
        "(position_id, asof_ts, mtm_value, source) VALUES (?, ?, ?, ?)",
        (position_id, asof_ts, mtm_value, source),
    )
    conn.commit()


def _load_bars_up_to(conn: sqlite3.Connection, *, ticker: str, asof_ts: int, limit: int = 400):
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM bars "
        "WHERE ticker=? AND timeframe='1d' AND ts<=? "
        "ORDER BY ts DESC LIMIT ?",
        (ticker, asof_ts, limit),
    ).fetchall()
    bars = [
        SimpleNamespace(
            ts=r["ts"], open=r["open"], high=r["high"],
            low=r["low"], close=r["close"], volume=r["volume"],
        )
        for r in rows
    ]
    bars.reverse()
    return bars


def _dispatch_ticker(
    *,
    conn: sqlite3.Connection,
    ticker: str,
    asof_ts: int,
    nav: float,
    signal_fn: Callable,
    chain_fn: Callable,
    llm_client: object,
) -> str:
    """One ticker, one day, Phase C pipeline.

    Returns action label: 'opened' | 'rejected' | 'pass' | 'held' | 'closed' | 'skipped'.
    """
    bars = _load_bars_up_to(conn, ticker=ticker, asof_ts=asof_ts)
    if len(bars) < 30:
        return "skipped"
    spot = bars[-1].close
    signal = signal_fn(bars, ticker, asof_ts)
    chain = chain_fn(ticker, asof_ts, spot)

    open_pos = positions.open_for_ticker(conn, ticker)
    if open_pos is not None:
        leg_prices = {}
        for leg in open_pos.legs:
            if leg.kind == "share":
                leg_prices[leg.id] = spot
                continue
            q = chain.find_quote(expiry=leg.expiry, strike=leg.strike, kind=leg.kind)
            if q is not None and q.mid_price() is not None:
                leg_prices[leg.id] = q.mid_price()
        today = _datetime.fromtimestamp(asof_ts).date()
        action = exits.evaluate(
            conn, position=open_pos, signal=signal, spot=spot,
            atr_14=0.0, today=today, asof_ts=asof_ts,
            current_leg_prices=leg_prices,
        )
        return "held" if action.kind == "hold" else "closed"

    decision = vehicle.pick(
        conn, ticker=ticker, spot=spot, signal=signal,
        bars=bars, levels=[],
        days_to_earnings=999, earnings_window_active=False,
        iv_rank=0.5, budget_per_trade_usd=nav * 0.02,
        asof_ts=asof_ts, per_ticker_concentration_pct=0.0,
        open_positions_count=positions.open_count(conn),
        client=llm_client,
    )
    if decision.decision != "open":
        return "pass"

    entry_prices = {}
    for idx, spec in enumerate(decision.legs):
        if spec.kind == "share":
            entry_prices[idx] = spot
            continue
        q = chain.find_quote(expiry=spec.expiry, strike=spec.strike, kind=spec.kind)
        if q is not None and q.mid_price() is not None:
            entry_prices[idx] = q.mid_price()
        else:
            entry_prices[idx] = 0.0

    today = _datetime.fromtimestamp(asof_ts).date()
    validation = vehicle.validate(
        decision=decision, spot=spot, today=today, nav=nav,
        per_trade_pct=0.02, per_ticker_pct=0.15, max_open_positions=12,
        current_ticker_concentration_dollars=0.0,
        current_open_positions=positions.open_count(conn),
        earnings_window_active=False, entry_prices=entry_prices,
    )
    if not validation.ok:
        return "rejected"

    positions.open_position(
        conn, ticker=ticker, intent=decision.intent,
        structure_kind=decision.structure,
        legs=validation.sized_legs, opened_ts=asof_ts,
        profit_target_price=decision.exit_plan.get("profit_target_price"),
        stop_price=decision.exit_plan.get("stop_price"),
        time_stop_dte=decision.exit_plan.get("time_stop_dte"),
        assignment_acceptable=bool(decision.exit_plan.get("assignment_acceptable", False)),
        nearest_leg_expiry_dte=None, rationale=decision.rationale,
    )
    return "opened"
