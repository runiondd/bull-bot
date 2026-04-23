"""Tests for engine-level exit manager."""
import json
import sqlite3

import pytest

from bullbot.db import connection as db_connection
from bullbot.engine import exit_manager, fill_model
from bullbot.data.schemas import Leg, OptionContract


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from bullbot.db.migrations import apply_schema
    apply_schema(c)
    return c


def _insert_position(conn, *, run_id="test", ticker="SPY", strategy_id=None,
                      opened_at=1000, legs_json, contracts=1, open_price,
                      exit_rules_json=None):
    conn.execute(
        "INSERT INTO positions (run_id, ticker, strategy_id, opened_at, legs, "
        "contracts, open_price, mark_to_mkt, exit_rules) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, ticker, strategy_id, opened_at, legs_json,
         contracts, open_price, open_price, exit_rules_json),
    )


def _make_chain(short_sym, short_bid, short_ask, long_sym, long_bid, long_ask):
    return {
        short_sym: {"nbbo_bid": short_bid, "nbbo_ask": short_ask},
        long_sym: {"nbbo_bid": long_bid, "nbbo_ask": long_ask},
    }


def _spread_legs_json(short_sym="SPY260620P00670000", long_sym="SPY260620P00665000",
                       short_strike=670.0, long_strike=665.0):
    return json.dumps([
        {"option_symbol": short_sym, "side": "short", "quantity": 1,
         "strike": short_strike, "expiry": "2026-06-20", "kind": "P"},
        {"option_symbol": long_sym, "side": "long", "quantity": 1,
         "strike": long_strike, "expiry": "2026-06-20", "kind": "P"},
    ])


class TestProfitTarget:
    def test_no_exit_below_target(self, conn):
        """50% profit target, spread worth ~60% of credit -> no exit (only ~40% profit)."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)
        # close_cost = (0.76 - 0.06) * 100 = 70 -> pnl = 48 = 40.7% < 50% target
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.70, 0.80,
            "SPY260620P00665000", 0.06, 0.08,
        )
        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert closed == []

    def test_exit_at_target(self, conn):
        """50% profit target, spread decayed -> profit exceeds target, fires."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.14, 0.16,
            "SPY260620P00665000", 0.04, 0.06,
        )
        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert len(closed) == 1
        pos = conn.execute("SELECT * FROM positions WHERE id=?", (closed[0],)).fetchone()
        assert pos["closed_at"] == 2000
        assert pos["pnl_realized"] is not None


class TestStopLoss:
    def test_no_exit_below_stop(self, conn):
        """2x stop, loss at 1.5x credit -> no exit."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)
        chain_rows = _make_chain(
            "SPY260620P00670000", 2.50, 2.60,
            "SPY260620P00665000", 0.30, 0.40,
        )
        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert closed == []

    def test_exit_at_stop(self, conn):
        """2x stop, loss exceeds 2x credit -> exit fires."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)
        chain_rows = _make_chain(
            "SPY260620P00670000", 4.50, 4.70,
            "SPY260620P00665000", 0.35, 0.45,
        )
        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert len(closed) == 1


class TestDteClose:
    def test_no_exit_above_dte(self, conn):
        """min_dte_close=7, cursor at 10 DTE -> no exit."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)
        cursor_10dte = 1781251200  # 2026-06-10 00:00:00 UTC
        # close_cost = 70 -> pnl = 48 = 40.7% < 50% target (below profit threshold too)
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.70, 0.80,
            "SPY260620P00665000", 0.06, 0.08,
        )
        closed = exit_manager.check_exits(conn, "test", "SPY", cursor_10dte, chain_rows)
        assert closed == []

    def test_exit_at_dte(self, conn):
        """min_dte_close=7, cursor at 5 DTE -> exit fires."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)
        cursor_5dte = 1781683200  # 2026-06-15 00:00:00 UTC
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.65, 0.75,
            "SPY260620P00665000", 0.25, 0.35,
        )
        closed = exit_manager.check_exits(conn, "test", "SPY", cursor_5dte, chain_rows)
        assert len(closed) == 1


class TestNoneRules:
    def test_no_exit_rules_means_no_exit(self, conn):
        legs_json = _spread_legs_json()
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=None)
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.04, 0.06,
            "SPY260620P00665000", 0.01, 0.03,
        )
        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert closed == []

    def test_partial_none_rules(self, conn):
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)
        # close_cost = 12 -> pnl = 106 = 89.8% > 50% target -> fires
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.14, 0.16,
            "SPY260620P00665000", 0.04, 0.06,
        )
        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert len(closed) == 1


class TestFillRejected:
    def test_skip_when_no_chain_data(self, conn):
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)
        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows={})
        assert closed == []


class TestUnrealizedPnLRefresh:
    """check_exits must refresh positions.unrealized_pnl on every visit,
    even when no exit rule fires. This is what makes the dashboard's
    paper-P&L number move day-to-day rather than being frozen at entry."""

    def test_unrealized_pnl_updated_on_open_credit_spread(self, conn):
        """Credit spread that hasn't hit target: unrealized_pnl should reflect
        credit - close_cost using the same formula used for exit decisions."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)
        # close_cost ~ 70 -> unrealized_pnl ~ 118 - 70 = 48 (below 50% target, won't exit)
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.70, 0.80,
            "SPY260620P00665000", 0.06, 0.08,
        )
        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert closed == []
        pos = conn.execute("SELECT unrealized_pnl FROM positions WHERE ticker='SPY'").fetchone()
        assert pos["unrealized_pnl"] is not None
        assert pos["unrealized_pnl"] == pytest.approx(48.0, abs=0.5)

    def test_unrealized_pnl_set_to_zero_on_close(self, conn):
        """When a position closes, unrealized_pnl goes to 0 and pnl_realized
        takes over. This preserves the invariant `realized + unrealized = total`."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)
        # close_cost ~ 12 -> unrealized would be ~106, well past 50% target
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.14, 0.16,
            "SPY260620P00665000", 0.04, 0.06,
        )
        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert len(closed) == 1
        pos = conn.execute("SELECT unrealized_pnl, pnl_realized FROM positions WHERE id=?",
                            (closed[0],)).fetchone()
        assert pos["unrealized_pnl"] == 0.0
        assert pos["pnl_realized"] is not None
        assert pos["pnl_realized"] > 0  # profit target fired, so realized is positive

    def test_unrealized_pnl_left_stale_when_pricing_fails(self, conn):
        """If the option chain doesn't have the leg symbols (fill rejected),
        unrealized_pnl keeps its previous value rather than getting nulled out."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)
        # Prime unrealized_pnl with a known previous value
        conn.execute("UPDATE positions SET unrealized_pnl=42.0 WHERE ticker='SPY'")
        # Empty chain_rows -> simulate_close_multi_leg raises KeyError
        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows={})
        assert closed == []
        pos = conn.execute("SELECT unrealized_pnl FROM positions WHERE ticker='SPY'").fetchone()
        # Stale previous value preserved (not reset to NULL)
        assert pos["unrealized_pnl"] == 42.0

    def test_unrealized_pnl_updated_even_without_exit_rules(self, conn):
        """Positions with no exit_rules should still get unrealized_pnl refreshed."""
        legs_json = _spread_legs_json()
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=None)
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.70, 0.80,
            "SPY260620P00665000", 0.06, 0.08,
        )
        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert closed == []
        pos = conn.execute("SELECT unrealized_pnl FROM positions WHERE ticker='SPY'").fetchone()
        assert pos["unrealized_pnl"] is not None
        assert pos["unrealized_pnl"] == pytest.approx(48.0, abs=0.5)
