import sqlite3

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.leaderboard.query import LeaderboardEntry, top_n


def _seed(conn):
    conn.executescript("""
        INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at)
            VALUES (1, 'PutCreditSpread', 1, '{}', 'a', strftime('%s','now')),
                   (2, 'IronCondor',     1, '{}', 'b', strftime('%s','now'));
        INSERT INTO evolver_proposals
            (ticker, iteration, strategy_id, rationale, llm_cost_usd,
             pf_is, pf_oos, trade_count, passed_gate, created_at,
             regime_label, score_a, size_units, max_loss_per_trade)
        VALUES
            ('META', 1, 1, '', 0, 1.9, 1.0, 7, 1, strftime('%s','now'),
             'up/low/low', 1.5, 10, 350),
            ('SPY',  2, 1, '', 0, 2.1, 0.8, 12, 1, strftime('%s','now'),
             'up/low/low', 2.3, 8, 400),
            ('NVDA', 3, 2, '', 0, 1.7, 0.6, 6, 1, strftime('%s','now'),
             'flat/mid/mid', 1.1, 4, 600),
            ('META', 4, 2, '', 0, 1.8, 0.7, 8, 1, strftime('%s','now'),
             'flat/mid/mid', 0.9, 3, 300);
    """)
    conn.commit()


def test_top_n_returns_dataclasses_sorted_by_score(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    _seed(conn)
    rows = top_n(conn, n=10)
    assert all(isinstance(r, LeaderboardEntry) for r in rows)
    assert [r.ticker for r in rows] == ["SPY", "META", "NVDA", "META"]  # by score_a desc


def test_top_n_filters_by_regime(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    _seed(conn)
    rows = top_n(conn, regime_label="up/low/low", n=10)
    assert all(r.regime_label == "up/low/low" for r in rows)
    assert len(rows) == 2


def test_top_n_filters_by_ticker(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    _seed(conn)
    rows = top_n(conn, ticker="META", n=10)
    assert all(r.ticker == "META" for r in rows)
    assert len(rows) == 2


def test_top_n_filters_by_class_name(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    _seed(conn)
    rows = top_n(conn, class_name="IronCondor", n=10)
    assert all(r.class_name == "IronCondor" for r in rows)
    assert len(rows) == 2


def test_top_n_combines_filters(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    _seed(conn)
    rows = top_n(conn, regime_label="up/low/low", ticker="META", n=10)
    assert len(rows) == 1
    assert rows[0].ticker == "META"
    assert rows[0].regime_label == "up/low/low"


def test_top_n_respects_n_limit(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    _seed(conn)
    rows = top_n(conn, n=2)
    assert len(rows) == 2
    assert [r.ticker for r in rows] == ["SPY", "META"]
