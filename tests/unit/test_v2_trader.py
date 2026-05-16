"""Unit tests for bullbot.v2.trader — entry/exit decision logic."""
from __future__ import annotations

import sqlite3

import pytest

from bullbot.v2 import trader, trades
from bullbot.v2.signals import DirectionalSignal


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE v2_paper_trades (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            shares REAL NOT NULL,
            entry_price REAL NOT NULL,
            entry_ts INTEGER NOT NULL,
            exit_price REAL,
            exit_ts INTEGER,
            pnl_realized REAL,
            exit_reason TEXT,
            signal_id INTEGER,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE directional_signals (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            asof_ts INTEGER NOT NULL,
            direction TEXT NOT NULL,
            confidence REAL NOT NULL,
            horizon_days INTEGER NOT NULL,
            rationale TEXT,
            rules_version TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE (ticker, asof_ts, rules_version)
        );
    """)
    return c


def _sig(direction: str, confidence: float, signal_id: int = 1) -> DirectionalSignal:
    return DirectionalSignal(
        ticker="AAPL", asof_ts=1_700_000_000, direction=direction,
        confidence=confidence, horizon_days=30, rationale="", rules_version="v1",
    )


def test_bullish_signal_opens_long_when_no_position(conn):
    sig = _sig("bullish", 0.8)
    action = trader.dispatch(
        conn, signal=sig, signal_id=99, spot=100.0, budget_usd=1000.0, now_ts=1_700_000_000,
    )
    assert action.kind == "opened"
    pos = trades.open_position_for(conn, "AAPL")
    assert pos.direction == "long"
    assert pos.shares == 10  # floor(1000 / 100)


def test_bearish_signal_opens_short_when_no_position(conn):
    sig = _sig("bearish", 0.8)
    action = trader.dispatch(
        conn, signal=sig, signal_id=99, spot=100.0, budget_usd=1000.0, now_ts=1_700_000_000,
    )
    assert action.kind == "opened"
    pos = trades.open_position_for(conn, "AAPL")
    assert pos.direction == "short"


def test_low_confidence_does_nothing(conn):
    sig = _sig("bullish", 0.20)  # below default threshold
    action = trader.dispatch(
        conn, signal=sig, signal_id=99, spot=100.0, budget_usd=1000.0, now_ts=1_700_000_000,
    )
    assert action.kind == "skipped_low_confidence"
    assert trades.open_position_for(conn, "AAPL") is None


def test_chop_signal_closes_existing_position(conn):
    # Open a long first.
    trades.open_trade(conn, ticker="AAPL", direction="long", shares=10, entry_price=100, entry_ts=1, signal_id=None)
    sig = _sig("chop", 0.9)
    action = trader.dispatch(
        conn, signal=sig, signal_id=99, spot=110.0, budget_usd=1000.0, now_ts=1_700_086_400,
    )
    assert action.kind == "closed_to_flat"
    assert trades.open_position_for(conn, "AAPL") is None
    assert trades.total_realized_pnl(conn) == pytest.approx(100.0)


def test_no_edge_signal_closes_existing_position(conn):
    trades.open_trade(conn, ticker="AAPL", direction="long", shares=10, entry_price=100, entry_ts=1, signal_id=None)
    sig = _sig("no_edge", 0.0)
    action = trader.dispatch(
        conn, signal=sig, signal_id=99, spot=95.0, budget_usd=1000.0, now_ts=1_700_086_400,
    )
    assert action.kind == "closed_to_flat"


def test_bullish_signal_holds_existing_long(conn):
    trades.open_trade(conn, ticker="AAPL", direction="long", shares=10, entry_price=100, entry_ts=1, signal_id=None)
    sig = _sig("bullish", 0.8)
    action = trader.dispatch(
        conn, signal=sig, signal_id=99, spot=105.0, budget_usd=1000.0, now_ts=1_700_086_400,
    )
    assert action.kind == "held"
    pos = trades.open_position_for(conn, "AAPL")
    assert pos.direction == "long"


def test_bullish_signal_flips_existing_short(conn):
    trades.open_trade(conn, ticker="AAPL", direction="short", shares=10, entry_price=100, entry_ts=1, signal_id=None)
    sig = _sig("bullish", 0.8)
    # Spot $105 = -5% on short, below stop-loss threshold (-10%) so flip path runs.
    action = trader.dispatch(
        conn, signal=sig, signal_id=99, spot=105.0, budget_usd=1000.0, now_ts=1_700_086_400,
    )
    assert action.kind == "flipped"
    pos = trades.open_position_for(conn, "AAPL")
    assert pos.direction == "long"
    # Short closed at $105, entry $100 → -$50 PnL (loss on short).
    assert trades.total_realized_pnl(conn) == pytest.approx(-50.0)


def test_budget_too_small_to_buy_one_share(conn):
    sig = _sig("bullish", 0.8)
    action = trader.dispatch(
        conn, signal=sig, signal_id=99, spot=2000.0, budget_usd=100.0, now_ts=1_700_000_000,
    )
    assert action.kind == "skipped_budget"
    assert trades.open_position_for(conn, "AAPL") is None


def test_stop_loss_triggers_close_on_long(conn):
    """Long position with loss > STOP_LOSS_PCT must close even with bullish signal."""
    trades.open_trade(conn, ticker="AAPL", direction="long", shares=10, entry_price=100, entry_ts=1, signal_id=None)
    sig = _sig("bullish", 0.8)  # signal still says hold, but loss is too big
    action = trader.dispatch(
        conn, signal=sig, signal_id=99, spot=85.0, budget_usd=1000.0, now_ts=1_700_086_400,
    )
    assert action.kind == "stopped_out"
    assert trades.open_position_for(conn, "AAPL") is None
    assert trades.total_realized_pnl(conn) == pytest.approx(-150.0)  # (85-100)*10


def test_stop_loss_triggers_on_short(conn):
    """Short with loss > STOP_LOSS_PCT (i.e. price rose >= 10%) must close."""
    trades.open_trade(conn, ticker="AAPL", direction="short", shares=10, entry_price=100, entry_ts=1, signal_id=None)
    sig = _sig("bearish", 0.8)
    action = trader.dispatch(
        conn, signal=sig, signal_id=99, spot=115.0, budget_usd=1000.0, now_ts=1_700_086_400,
    )
    assert action.kind == "stopped_out"


def test_small_loss_does_not_trigger_stop(conn):
    """Loss below threshold = no stop-out."""
    trades.open_trade(conn, ticker="AAPL", direction="long", shares=10, entry_price=100, entry_ts=1, signal_id=None)
    sig = _sig("bullish", 0.8)
    action = trader.dispatch(
        conn, signal=sig, signal_id=99, spot=95.0, budget_usd=1000.0, now_ts=1_700_086_400,
    )
    assert action.kind == "held"  # -5% < 10% threshold, hold
