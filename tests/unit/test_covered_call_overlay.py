"""Unit tests for CoveredCallOverlay strategy."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta

import pytest

from bullbot.data.schemas import Bar, OptionContract
from bullbot.data.synthetic_chain import generate_synthetic_chain
from bullbot.strategies.base import StrategySnapshot
from bullbot.strategies.covered_call_overlay import CoveredCallOverlay


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TICKER = "TSLA"
_NOW_TS = int(datetime(2026, 4, 13, 16, 0, tzinfo=timezone.utc).timestamp())


def _make_bars(spot: float, n: int = 60) -> list[Bar]:
    """Generate n daily bars ending at *spot* with the last bar a 3% up day."""
    bars: list[Bar] = []
    prev_close = spot / 1.03  # so last day is +3%
    base_ts = _NOW_TS - n * 86400
    for i in range(n - 1):
        c = prev_close * (1 + 0.001 * ((i % 5) - 2))  # slight drift
        bars.append(Bar(
            ticker=_TICKER, timeframe="1d",
            ts=base_ts + i * 86400,
            open=c * 0.99, high=c * 1.01, low=c * 0.98, close=c,
            volume=1_000_000, source="polygon",
        ))
        prev_close = c
    # Last bar: 3% up day
    bars.append(Bar(
        ticker=_TICKER, timeframe="1d",
        ts=_NOW_TS,
        open=prev_close, high=spot * 1.005, low=prev_close * 0.995, close=spot,
        volume=2_000_000, source="polygon",
    ))
    return bars


def _make_chain(spot: float, cursor: int) -> list[OptionContract]:
    """Generate OTM calls at ~30/45/60 DTE with realistic bid/ask."""
    contracts: list[OptionContract] = []
    now_dt = datetime.fromtimestamp(cursor, tz=timezone.utc)

    for dte in [30, 45, 60]:
        exp_dt = now_dt + timedelta(days=dte)
        expiry = exp_dt.strftime("%Y-%m-%d")
        for pct in [1.05, 1.10, 1.15, 1.20]:
            strike = round(spot * pct / 10) * 10  # round to nearest 10
            # Realistic OTM call pricing: further OTM = cheaper
            mid_price = max(0.50, spot * 0.02 * (1 - (pct - 1.0) * 5))
            bid = round(mid_price * 0.95, 2)
            ask = round(mid_price * 1.05, 2)
            contracts.append(OptionContract(
                ticker=_TICKER, expiry=expiry, strike=float(strike),
                kind="C", ts=cursor,
                nbbo_bid=bid, nbbo_ask=ask,
                volume=500, open_interest=2000, iv=0.45,
            ))
    return contracts


def _make_snapshot(spot: float = 350.0, rsi: float = 55.0, iv_rank: float = 50.0, bars=None) -> StrategySnapshot:
    """Assemble a StrategySnapshot with indicators."""
    if bars is None:
        bars = _make_bars(spot)
    chain = _make_chain(spot, _NOW_TS)
    return StrategySnapshot(
        ticker=_TICKER,
        asof_ts=_NOW_TS,
        spot=spot,
        bars_1d=bars,
        indicators={"rsi_14": rsi},
        atm_greeks={"delta": 0.50, "gamma": 0.02, "theta": -0.15, "vega": 0.30, "iv": 0.45},
        iv_rank=iv_rank,
        regime="bull",
        chain=chain,
    )


def _default_params() -> dict:
    return {
        "short_delta": 0.20,
        "dte_min": 20,
        "dte_max": 65,
        "coverage_ratio": 0.50,
        "min_rsi": 50,
        "min_day_return": 0.02,
        "iv_rank_min": 30,
        "roll_dte": 5,
        "profit_target_pct": 0.50,
        "roll_itm_delta": 0.70,
        "defend_time_value_min": 0.10,
    }


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    """In-memory DB with long_inventory and positions tables."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE long_inventory (
            id              INTEGER PRIMARY KEY,
            account         TEXT    NOT NULL,
            ticker          TEXT    NOT NULL,
            kind            TEXT    NOT NULL,
            strike          REAL,
            expiry          TEXT,
            quantity         REAL    NOT NULL,
            cost_basis_per  REAL,
            added_at        INTEGER NOT NULL,
            removed_at      INTEGER
        );
        CREATE TABLE positions (
            id              INTEGER PRIMARY KEY,
            run_id          TEXT    NOT NULL DEFAULT 'live',
            ticker          TEXT    NOT NULL,
            strategy_id     INTEGER,
            legs            TEXT,
            contracts       INTEGER NOT NULL DEFAULT 1,
            open_price      REAL    NOT NULL,
            close_price     REAL,
            mark_to_mkt     REAL    NOT NULL DEFAULT 0.0,
            exit_rules      TEXT,
            opened_at       INTEGER NOT NULL,
            closed_at       INTEGER,
            pnl_realized    REAL
        );
    """)
    return db


def _seed_inventory(conn: sqlite3.Connection, call_qty: int = 4, share_qty: int = 0):
    """Insert TSLA call LEAPS and optionally shares into long_inventory."""
    now = int(time.time())
    if call_qty > 0:
        conn.execute(
            "INSERT INTO long_inventory (account, ticker, kind, strike, expiry, quantity, added_at) "
            "VALUES (?, ?, 'call', 300, '2027-06-18', ?, ?)",
            ("ira", _TICKER, call_qty, now),
        )
    if share_qty > 0:
        conn.execute(
            "INSERT INTO long_inventory (account, ticker, kind, quantity, added_at) "
            "VALUES (?, ?, 'shares', ?, ?)",
            ("taxable", _TICKER, share_qty, now),
        )
    conn.commit()


def _insert_open_short_call_positions(conn: sqlite3.Connection, count: int) -> list[dict]:
    """Insert *count* open short-call positions and return as dicts for open_positions."""
    positions = []
    now = int(time.time())
    legs_json = json.dumps([{"side": "short", "kind": "C", "strike": 380, "expiry": "2026-05-15"}])
    for i in range(count):
        conn.execute(
            "INSERT INTO positions (ticker, legs, contracts, open_price, opened_at) "
            "VALUES (?, ?, 1, 3.50, ?)",
            (_TICKER, legs_json, now),
        )
        positions.append({
            "id": i + 1,
            "ticker": _TICKER,
            "legs": legs_json,
            "contracts": 1,
            "strategy_class": "CoveredCallOverlay",
        })
    conn.commit()
    return positions


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCoveredCallOverlay:

    def test_generates_short_call_signal(self, conn):
        """With inventory, good RSI (55), green day (3% up), verify short OTM call."""
        _seed_inventory(conn, call_qty=4)
        snapshot = _make_snapshot(spot=350.0, rsi=55.0)
        strat = CoveredCallOverlay(params=_default_params())

        signal = strat.evaluate(snapshot, open_positions=[], conn=conn)

        assert signal is not None
        assert signal.intent == "open"
        assert signal.strategy_class == "CoveredCallOverlay"
        assert len(signal.legs) == 1
        leg = signal.legs[0]
        assert leg.side == "short"
        assert leg.kind == "C"
        assert leg.strike > 350.0  # OTM
        assert leg.quantity == 1

    def test_no_signal_when_rsi_too_low(self, conn):
        """RSI 55 but min_rsi=60 -> None."""
        _seed_inventory(conn, call_qty=4)
        snapshot = _make_snapshot(spot=350.0, rsi=55.0)
        params = _default_params()
        params["min_rsi"] = 60
        strat = CoveredCallOverlay(params=params)

        signal = strat.evaluate(snapshot, open_positions=[], conn=conn)
        assert signal is None

    def test_no_signal_when_fully_covered(self, conn):
        """All coverable contracts already have short calls -> None."""
        _seed_inventory(conn, call_qty=4)
        # coverage_ratio=0.50 => max_short=2, insert 2 existing
        existing = _insert_open_short_call_positions(conn, count=2)
        snapshot = _make_snapshot(spot=350.0, rsi=55.0)
        strat = CoveredCallOverlay(params=_default_params())

        signal = strat.evaluate(snapshot, open_positions=existing, conn=conn)
        assert signal is None

    def test_coverage_ratio_limits_short_calls(self, conn):
        """4 coverable, ratio=0.5, 2 existing = at limit -> None."""
        _seed_inventory(conn, call_qty=4)
        existing = _insert_open_short_call_positions(conn, count=2)
        params = _default_params()
        params["coverage_ratio"] = 0.50  # max_short = floor(4 * 0.5) = 2
        strat = CoveredCallOverlay(params=params)
        snapshot = _make_snapshot(spot=350.0, rsi=55.0)

        signal = strat.evaluate(snapshot, open_positions=existing, conn=conn)
        assert signal is None

    def test_no_signal_on_red_day(self, conn):
        """Flat/down day with min_day_return=0.02 -> None."""
        _seed_inventory(conn, call_qty=4)
        spot = 350.0
        # Build bars where last day is flat (0% return)
        bars = _make_bars(spot, n=60)
        # Replace last bar with a flat day: close == prev close
        prev_close = bars[-2].close
        flat_bar = Bar(
            ticker=_TICKER, timeframe="1d", ts=_NOW_TS,
            open=prev_close, high=prev_close * 1.002,
            low=prev_close * 0.998, close=prev_close,
            volume=1_500_000, source="polygon",
        )
        bars[-1] = flat_bar
        snapshot = _make_snapshot(spot=prev_close, rsi=55.0, bars=bars)
        strat = CoveredCallOverlay(params=_default_params())

        signal = strat.evaluate(snapshot, open_positions=[], conn=conn)
        assert signal is None
