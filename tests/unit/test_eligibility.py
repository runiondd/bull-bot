import sqlite3

import numpy as np
import pytest

from bullbot.db.migrations import apply_schema
from bullbot.regime.eligibility import MenuEntry, menu_for


def _strategy_row(conn, sid, class_name):
    conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (?, ?, 1, '{}', ?, strftime('%s','now'))",
        (sid, class_name, f"hash-{sid}"),
    )


_proposal_counter: dict[tuple, int] = {}


def _proposal_row(conn, ticker, strategy_id, regime_label, score_a):
    key = (ticker, strategy_id)
    iteration = _proposal_counter.get(key, 0)
    _proposal_counter[key] = iteration + 1
    conn.execute(
        "INSERT INTO evolver_proposals "
        "(ticker, iteration, strategy_id, rationale, llm_cost_usd, "
        " trade_count, passed_gate, created_at, regime_label, score_a) "
        "VALUES (?, ?, ?, '', 0, 5, 1, strftime('%s','now'), ?, ?)",
        (ticker, iteration, strategy_id, regime_label, score_a),
    )


def test_cold_start_includes_all_classes_with_explore_status(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    menu = menu_for(
        conn, ticker="META", regime_label="up/low/low",
        all_classes=["PutCreditSpread", "IronCondor", "GrowthEquity"],
    )
    # All three classes have zero observations → cold-start
    # But default n_explore=1, so only 1 gets included
    assert all(m.status == "explore" for m in menu)
    assert len(menu) == 1


def test_cold_start_with_n_explore_3_returns_all(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    menu = menu_for(
        conn, ticker="META", regime_label="up/low/low",
        all_classes=["PutCreditSpread", "IronCondor", "GrowthEquity"],
        n_explore=3,
    )
    assert len(menu) == 3
    assert all(m.status == "explore" for m in menu)


def test_with_observations_ranks_by_score_a(tmp_path):
    np.random.seed(0)
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    _strategy_row(conn, 1, "PutCreditSpread")
    _strategy_row(conn, 2, "IronCondor")
    # PutCreditSpread: 10 obs at score_a around 2.0
    for _ in range(10):
        _proposal_row(conn, "META", 1, "up/low/low", 2.0)
    # IronCondor: 10 obs at score_a around 0.5
    for _ in range(10):
        _proposal_row(conn, "META", 2, "up/low/low", 0.5)
    conn.commit()
    menu = menu_for(
        conn, ticker="META", regime_label="up/low/low",
        all_classes=["PutCreditSpread", "IronCondor"], n_exploit=2,
    )
    # Both classes have >= MIN_OBS_FOR_EXPLOIT=5 → both are "exploit"
    # PutCreditSpread has higher mean → should be first
    assert len(menu) == 2
    assert menu[0].class_name == "PutCreditSpread"
    assert menu[0].status == "exploit"
    assert menu[1].class_name == "IronCondor"
    assert menu[1].status == "exploit"


def test_explore_slot_picks_underexplored_class(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    _strategy_row(conn, 1, "PutCreditSpread")
    _strategy_row(conn, 2, "IronCondor")
    # 10 obs each for PutCreditSpread and IronCondor; 0 for GrowthEquity
    for _ in range(10):
        _proposal_row(conn, "META", 1, "up/low/low", 2.0)
        _proposal_row(conn, "META", 2, "up/low/low", 1.5)
    conn.commit()
    menu = menu_for(
        conn, ticker="META", regime_label="up/low/low",
        all_classes=["PutCreditSpread", "IronCondor", "GrowthEquity"],
        n_exploit=2, n_explore=1,
    )
    statuses = {m.class_name: m.status for m in menu}
    assert statuses["GrowthEquity"] == "explore"
    assert statuses["PutCreditSpread"] == "exploit"
    assert statuses["IronCondor"] == "exploit"
