"""Backtest replay runner for v2 Phase C.

Single public entry: backtest(conn, ticker, start, end, starting_nav, llm_client)
-> BacktestResult. Walks one ticker through N historical days, calling the
same Phase C agent + validator + exit-rule pipeline as forward mode but
against chains synthesized from bars via synth_chain.synthesize.

LLM responses are cached on disk (sqlite table backtest_llm_cache) so reruns
of the same backtest cost $0 in Anthropic credits. Cache key is sha256 of
the full LLM prompt.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date as _date
from types import SimpleNamespace
from typing import Callable

from bullbot.v2 import exits, positions, vehicle
from bullbot.v2.backtest import synth_chain
from bullbot.v2.chains import _iv_proxy
from bullbot.v2.signals import DirectionalSignal


def _cache_key(*, prompt: str) -> str:
    """sha256 hex digest of the full LLM prompt — used as the cache PK."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _cache_get(conn: sqlite3.Connection, *, key: str) -> str | None:
    row = conn.execute(
        "SELECT response_text FROM backtest_llm_cache WHERE prompt_sha=?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return row[0]


def _cache_put(conn: sqlite3.Connection, *, key: str, response: str) -> None:
    """INSERT OR REPLACE so re-running with a new response overwrites
    (typically only useful when developing the prompt template)."""
    conn.execute(
        "INSERT OR REPLACE INTO backtest_llm_cache (prompt_sha, response_text) "
        "VALUES (?, ?)",
        (key, response),
    )
    conn.commit()


INTENTS = ("trade", "accumulate")


@dataclass(frozen=True)
class BacktestTrade:
    ticker: str
    structure_kind: str
    intent: str
    opened_ts: int
    closed_ts: int
    close_reason: str
    realized_pnl: float
    rationale: str

    def __post_init__(self) -> None:
        if self.intent not in INTENTS:
            raise ValueError(f"intent must be one of {INTENTS}; got {self.intent!r}")


@dataclass
class BacktestResult:
    ticker: str
    start_date: _date
    end_date: _date
    starting_nav: float
    ending_nav: float
    trades: list[BacktestTrade]
    daily_mtm: list[tuple[int, float]]  # (asof_ts, nav)

    def total_realized_pnl(self) -> float:
        return sum(t.realized_pnl for t in self.trades)


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


def _atr_14_simple(bars: list) -> float:
    """ATR-14 from bars (simple average TR). Returns 0.0 when <15 bars."""
    if len(bars) < 15:
        return 0.0
    trs = []
    for i, b in enumerate(bars[-15:]):
        if i == 0:
            continue
        prev_close = bars[-15:][i - 1].close
        trs.append(max(
            b.high - b.low,
            abs(b.high - prev_close),
            abs(b.low - prev_close),
        ))
    return sum(trs) / 14


def _compute_position_mtm(*, position, current_chain) -> float:
    """Sum per-leg mid prices × qty for a position using the current chain.
    Share legs use entry_price as a placeholder; caller refines if needed."""
    total = 0.0
    for leg in position.legs:
        if leg.kind == "share":
            total += leg.entry_price * leg.qty  # placeholder: caller refines
            continue
        quote = current_chain.find_quote(expiry=leg.expiry, strike=leg.strike, kind=leg.kind)
        if quote is None or quote.mid_price() is None:
            continue
        sign = 1.0 if leg.action == "buy" else -1.0
        total += sign * quote.mid_price() * leg.qty * 100
    return total


def _replay_one_day(
    *,
    conn: sqlite3.Connection,
    ticker: str,
    today: _date,
    asof_ts: int,
    starting_nav_today: float,
    signal_fn: Callable,
    strike_grid_fn: Callable,
    expiries_fn: Callable,
    llm_client: object,
    llm_cache_conn: sqlite3.Connection,
) -> dict | None:
    """Replay one historical day for one ticker.

    Returns dict with `action_taken`, `trade_closed` (Optional[BacktestTrade]),
    `mtm_nav` (float). Returns None when too few bars to compute signal.

    The LLM client is wrapped in a cache check: first call for a given prompt
    hits the real client; subsequent calls with the same prompt hit the cache.
    """
    underlying_bars = _load_bars_up_to(conn, ticker=ticker, asof_ts=asof_ts)
    if len(underlying_bars) < 30:
        return None
    vix_bars = _load_bars_up_to(conn, ticker="VIX", asof_ts=asof_ts, limit=60)
    spot = underlying_bars[-1].close

    signal = signal_fn(underlying_bars)
    iv = _iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)  # noqa: F841
    chain = synth_chain.synthesize(
        ticker=ticker, asof_ts=asof_ts, today=today, spot=spot,
        underlying_bars=underlying_bars, vix_bars=vix_bars,
        expiries=expiries_fn(today),
        strikes=strike_grid_fn(spot),
    )

    # 1. Exit evaluation on held position (if any)
    open_pos = positions.open_for_ticker(conn, ticker)
    trade_closed: BacktestTrade | None = None
    if open_pos is not None:
        leg_prices = {}
        for leg in open_pos.legs:
            if leg.kind == "share":
                leg_prices[leg.id] = spot
                continue
            q = chain.find_quote(expiry=leg.expiry, strike=leg.strike, kind=leg.kind)
            if q is not None and q.mid_price() is not None:
                leg_prices[leg.id] = q.mid_price()
        exit_action = exits.evaluate(
            conn, position=open_pos, signal=signal, spot=spot,
            atr_14=_atr_14_simple(underlying_bars),
            today=today, asof_ts=asof_ts,
            current_leg_prices=leg_prices,
        )
        if exit_action.kind != "hold":
            # Materialize a BacktestTrade from the closed position
            closed = positions.load_position(conn, open_pos.id)
            realized = sum(
                ((leg.exit_price or 0) - leg.entry_price) * leg.qty *
                (100 if leg.kind != "share" else 1) *
                (1 if leg.action == "buy" else -1)
                for leg in closed.legs
            )
            trade_closed = BacktestTrade(
                ticker=ticker, structure_kind=closed.structure_kind,
                intent=closed.intent, opened_ts=closed.opened_ts,
                closed_ts=closed.closed_ts or asof_ts,
                close_reason=closed.close_reason or "unknown",
                realized_pnl=realized, rationale=closed.rationale or "",
            )

    # 2. Vehicle pick on flat tickers
    action_taken = "skipped"
    if positions.open_for_ticker(conn, ticker) is None:
        # Build prompt once, check cache, only call LLM on miss
        ctx = vehicle.build_llm_context(
            conn, ticker=ticker, spot=spot, signal=signal,
            bars=underlying_bars, levels=[],
            days_to_earnings=999, earnings_window_active=False,
            iv_rank=0.5, budget_per_trade_usd=starting_nav_today * 0.02,
            asof_ts=asof_ts, per_ticker_concentration_pct=0.0,
            open_positions_count=positions.open_count(conn),
            current_position=None,
        )
        prompt = json.dumps(ctx, sort_keys=True)
        cache_key = _cache_key(prompt=prompt)
        cached = _cache_get(llm_cache_conn, key=cache_key)
        if cached is not None:
            decision = vehicle._parse_llm_response(cached)
            if decision is None:
                decision = vehicle.VehicleDecision(
                    decision="pass", intent="trade", structure="long_call",
                    legs=[], exit_plan={}, rationale="cached parse failed",
                )
        else:
            decision = vehicle.pick(
                conn, ticker=ticker, spot=spot, signal=signal,
                bars=underlying_bars, levels=[],
                days_to_earnings=999, earnings_window_active=False,
                iv_rank=0.5, budget_per_trade_usd=starting_nav_today * 0.02,
                asof_ts=asof_ts, per_ticker_concentration_pct=0.0,
                open_positions_count=positions.open_count(conn),
                client=llm_client,
            )
            _cache_put(llm_cache_conn, key=cache_key, response=json.dumps({
                "decision": decision.decision, "intent": decision.intent,
                "structure": decision.structure,
                "legs": [{"action": l.action, "kind": l.kind, "strike": l.strike,
                          "expiry": l.expiry, "qty_ratio": l.qty_ratio}
                         for l in decision.legs],
                "exit_plan": decision.exit_plan,
                "rationale": decision.rationale,
            }))

        if decision.decision == "open":
            # Build entry_prices dict by indexing legs to BS-priced quotes
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
            validation = vehicle.validate(
                decision=decision, spot=spot, today=today,
                nav=starting_nav_today, per_trade_pct=0.02, per_ticker_pct=0.15,
                max_open_positions=12, current_ticker_concentration_dollars=0.0,
                current_open_positions=positions.open_count(conn),
                earnings_window_active=False, entry_prices=entry_prices,
            )
            if validation.ok:
                positions.open_position(
                    conn, ticker=ticker, intent=decision.intent,
                    structure_kind=decision.structure,
                    legs=validation.sized_legs, opened_ts=asof_ts,
                    profit_target_price=decision.exit_plan.get("profit_target_price"),
                    stop_price=decision.exit_plan.get("stop_price"),
                    time_stop_dte=decision.exit_plan.get("time_stop_dte"),
                    assignment_acceptable=bool(decision.exit_plan.get("assignment_acceptable", False)),
                    nearest_leg_expiry_dte=None,
                    rationale=decision.rationale,
                )
                action_taken = "opened"
            else:
                action_taken = "rejected"
        else:
            action_taken = "pass"
    else:
        action_taken = "held"

    # 3. End-of-day MtM
    mtm_total = 0.0
    open_now = positions.open_for_ticker(conn, ticker)
    if open_now is not None:
        mtm_total = _compute_position_mtm(position=open_now, current_chain=chain)

    return {
        "action_taken": action_taken,
        "trade_closed": trade_closed,
        "mtm_nav": starting_nav_today + mtm_total + (trade_closed.realized_pnl if trade_closed else 0.0),
    }
