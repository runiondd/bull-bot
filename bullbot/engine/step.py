"""
The unified execution primitive: engine.step(cursor, ticker, strategy, run_id).
Same code path for backtest and live.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bullbot import config
from bullbot.data.schemas import Bar, Leg, OptionContract, Signal
from bullbot.engine import exit_manager, fill_model, position_sizer
from bullbot.features import greeks as greeks_mod
from bullbot.features import indicators, regime as regime_mod
from bullbot.strategies.base import Strategy, StrategySnapshot

log = logging.getLogger("bullbot.engine")

# Map DB kind values to model kind values
_DB_KIND_TO_MODEL: dict[str, str] = {"call": "C", "put": "P"}
_MODEL_KIND_TO_DB: dict[str, str] = {"C": "call", "P": "put"}


@dataclass
class StepResult:
    signal: Signal | None
    filled: bool
    cash_flow: float = 0.0
    commission: float = 0.0
    position_id: int | None = None


def _load_bars_at_cursor(conn: sqlite3.Connection, ticker: str, cursor: int, limit: int = 400) -> list[Bar]:
    """Load up to `limit` daily bars with ts <= cursor, ordered oldest-first."""
    rows = conn.execute(
        "SELECT * FROM bars WHERE ticker=? AND timeframe='1d' AND ts<=? "
        "ORDER BY ts DESC LIMIT ?",
        (ticker, cursor, limit),
    ).fetchall()
    return [
        Bar(
            ticker=r["ticker"],
            timeframe=r["timeframe"],
            ts=r["ts"],
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=int(r["volume"]),
            source="uw",  # default; DB schema doesn't carry source
        )
        for r in reversed(rows)
    ]


def _load_chain_at_cursor(conn: sqlite3.Connection, ticker: str, cursor: int) -> list[OptionContract]:
    """Load option chain as it looked on/before cursor.

    Falls back to synthetic chain (Black-Scholes + realized vol) when
    no real options data exists in the database.
    """
    rows = conn.execute("""
        SELECT oc.*
        FROM option_contracts oc
        INNER JOIN (
            SELECT ticker, expiry, strike, kind, MAX(ts) AS max_ts
            FROM option_contracts
            WHERE ticker=? AND ts<=?
            GROUP BY ticker, expiry, strike, kind
        ) m ON oc.ticker=m.ticker AND oc.expiry=m.expiry
            AND oc.strike=m.strike AND oc.kind=m.kind AND oc.ts=m.max_ts
    """, (ticker, cursor)).fetchall()

    if rows:
        return [
            OptionContract(
                ticker=r["ticker"],
                expiry=r["expiry"],
                strike=r["strike"],
                kind=_DB_KIND_TO_MODEL.get(r["kind"], r["kind"]),
                ts=r["ts"],
                nbbo_bid=r["bid"],
                nbbo_ask=r["ask"],
                last=None,
                volume=int(r["volume"]) if r["volume"] is not None else None,
                open_interest=int(r["open_interest"]) if r["open_interest"] is not None else None,
                iv=r["iv"],
            )
            for r in rows
        ]

    bars = _load_bars_at_cursor(conn, ticker, cursor, limit=60)
    if len(bars) < 2:
        return []
    from bullbot.data.synthetic_chain import generate_synthetic_chain
    return generate_synthetic_chain(
        ticker=ticker, spot=bars[-1].close, cursor=cursor, bars=bars,
    )


def _compute_indicators(bars: list[Bar]) -> dict[str, float]:
    if len(bars) < 20:
        return {}
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    out: dict[str, float] = {}
    out["sma_20"] = indicators.sma(closes, 20) or 0.0
    out["ema_20"] = indicators.ema(closes, 20) or 0.0
    out["rsi_14"] = indicators.rsi(closes, 14) or 0.0
    atr_val = indicators.atr(highs, lows, closes, 14)
    out["atr_14"] = atr_val if atr_val else 0.0
    return out


def _compute_iv_rank(conn: sqlite3.Connection, ticker: str, cursor: int) -> float:
    """Compute IV rank from iv_surface table.

    Uses the most recent IV observation at each day as the daily IV.
    Falls back to 50.0 if insufficient data.
    """
    rows = conn.execute(
        "SELECT ts, iv FROM iv_surface "
        "WHERE ticker=? AND ts<=? "
        "ORDER BY ts DESC LIMIT 252",
        (ticker, cursor),
    ).fetchall()

    if len(rows) < 20:
        return 50.0

    ivs = [float(r["iv"]) for r in rows if r["iv"] is not None]
    if len(ivs) < 20:
        return 50.0

    current_iv = ivs[0]
    return indicators.iv_rank(current_iv, ivs[1:])


def _load_brief(conn: sqlite3.Connection, scope: str, cursor: int) -> str:
    """Load the most recent regime brief for scope on or before cursor's day."""
    day_ts = cursor - (cursor % 86400)
    row = conn.execute(
        "SELECT brief_text FROM regime_briefs WHERE scope=? AND ts<=? ORDER BY ts DESC LIMIT 1",
        (scope, day_ts),
    ).fetchone()
    return row["brief_text"] if row else ""


def _build_snapshot(conn: sqlite3.Connection, ticker: str, cursor: int) -> StrategySnapshot | None:
    bars = _load_bars_at_cursor(conn, ticker, cursor, limit=400)
    if len(bars) < 60:
        return None
    chain = _load_chain_at_cursor(conn, ticker, cursor)
    ind = _compute_indicators(bars)
    regime = regime_mod.classify([b.close for b in bars[-60:]])
    iv_rank = _compute_iv_rank(conn, ticker, cursor)
    market_brief = _load_brief(conn, "market", cursor)
    ticker_brief = _load_brief(conn, ticker, cursor)
    return StrategySnapshot(
        ticker=ticker,
        asof_ts=cursor,
        spot=bars[-1].close,
        bars_1d=bars,
        indicators=ind,
        atm_greeks={},
        iv_rank=iv_rank,
        regime=regime,
        chain=chain,
        market_brief=market_brief,
        ticker_brief=ticker_brief,
    )


def _load_open_positions(conn: sqlite3.Connection, run_id: str, ticker: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM positions WHERE run_id=? AND ticker=? AND closed_at IS NULL",
        (run_id, ticker),
    ).fetchall()
    return [dict(r) for r in rows]


def _compute_equity(conn: sqlite3.Connection, run_id: str) -> float:
    realized = conn.execute(
        "SELECT COALESCE(SUM(pnl_realized), 0) FROM positions "
        "WHERE run_id=? AND closed_at IS NOT NULL",
        (run_id,),
    ).fetchone()[0]
    mark = conn.execute(
        "SELECT COALESCE(SUM(mark_to_mkt), 0) FROM positions "
        "WHERE run_id=? AND closed_at IS NULL",
        (run_id,),
    ).fetchone()[0]
    return config.INITIAL_CAPITAL_USD + float(realized) + float(mark)


def _build_chain_rows(chain: list[OptionContract]) -> dict[str, dict[str, Any]]:
    """Build option_symbol -> {nbbo_bid, nbbo_ask} lookup for fill model."""
    chain_rows: dict[str, dict[str, Any]] = {}
    for c in chain:
        d = datetime.strptime(c.expiry, "%Y-%m-%d").date()
        sym = f"{c.ticker}{d:%y%m%d}{c.kind}{int(round(c.strike * 1000)):08d}"
        chain_rows[sym] = {"nbbo_bid": c.nbbo_bid, "nbbo_ask": c.nbbo_ask}
    return chain_rows


def step(
    conn: sqlite3.Connection,
    client: Any,
    cursor: int,
    ticker: str,
    strategy: Strategy,
    strategy_id: int,
    run_id: str,
) -> StepResult:
    """Run one execution step for a ticker."""
    snap = _build_snapshot(conn, ticker, cursor)
    if snap is None:
        return StepResult(signal=None, filled=False)

    chain_rows = _build_chain_rows(snap.chain)
    exit_manager.check_exits(conn, run_id, ticker, cursor, chain_rows)

    open_positions = _load_open_positions(conn, run_id, ticker)
    signal = strategy.evaluate(snap, open_positions)
    if signal is None:
        return StepResult(signal=None, filled=False)

    if signal.intent == "open":
        equity = _compute_equity(conn, run_id)
        category = config.TICKER_CATEGORY.get(ticker, "income")
        contracts = position_sizer.size_position(
            equity=equity,
            max_loss_per_contract=signal.max_loss_per_contract,
            category=category,
            regime=snap.regime,
        )
        if contracts <= 0:
            return StepResult(signal=signal, filled=False)

        try:
            net_cash, filled_legs = fill_model.simulate_open_multi_leg(
                legs=signal.legs, chain_rows=chain_rows, contracts=contracts,
            )
        except fill_model.FillRejected as e:
            log.info("fill rejected for %s: %s", ticker, e)
            return StepResult(signal=signal, filled=False)

        comm = fill_model.commission(contracts=contracts, n_legs=len(signal.legs))
        conn.execute(
            "INSERT INTO orders (run_id, ticker, strategy_id, placed_at, legs, intent, status, commission) "
            "VALUES (?, ?, ?, ?, ?, 'open', 'filled', ?)",
            (run_id, ticker, strategy_id, cursor,
             json.dumps([l.model_dump() for l in signal.legs]), comm),
        )
        exit_rules = json.dumps({
            k: v for k, v in {
                "profit_target_pct": signal.profit_target_pct,
                "stop_loss_mult": signal.stop_loss_mult,
                "min_dte_close": signal.min_dte_close,
            }.items() if v is not None
        }) or None
        cur = conn.execute(
            "INSERT INTO positions (run_id, ticker, strategy_id, opened_at, legs, contracts, open_price, mark_to_mkt, exit_rules) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, ticker, strategy_id, cursor,
             json.dumps([l.model_dump() for l in signal.legs]),
             contracts, net_cash, net_cash, exit_rules),
        )
        pos_id = cur.lastrowid
        return StepResult(
            signal=signal, filled=True, cash_flow=net_cash,
            commission=comm, position_id=pos_id,
        )

    # intent == 'close'
    pos_id = signal.position_id_to_close
    if pos_id is None:
        return StepResult(signal=signal, filled=False)
    pos_row = conn.execute(
        "SELECT * FROM positions WHERE id=? AND run_id=?", (pos_id, run_id),
    ).fetchone()
    if not pos_row:
        return StepResult(signal=signal, filled=False)

    legs = [Leg(**l) for l in json.loads(pos_row["legs"])]
    try:
        net_close, _ = fill_model.simulate_close_multi_leg(legs, chain_rows, pos_row["contracts"])
    except fill_model.FillRejected:
        return StepResult(signal=signal, filled=False)

    pnl = -(pos_row["open_price"] + net_close)
    comm = fill_model.commission(pos_row["contracts"], len(legs))
    conn.execute(
        "UPDATE positions SET closed_at=?, close_price=?, pnl_realized=?, mark_to_mkt=0.0 WHERE id=?",
        (cursor, net_close, pnl - comm, pos_id),
    )
    conn.execute(
        "INSERT INTO orders (run_id, ticker, strategy_id, placed_at, legs, intent, status, commission, pnl_realized) "
        "VALUES (?, ?, ?, ?, ?, 'close', 'filled', ?, ?)",
        (run_id, ticker, pos_row["strategy_id"], cursor,
         pos_row["legs"], comm, pnl - comm),
    )
    return StepResult(
        signal=signal, filled=True, cash_flow=net_close,
        commission=comm, position_id=pos_id,
    )
