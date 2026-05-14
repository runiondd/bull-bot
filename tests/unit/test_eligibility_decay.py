import sqlite3
import time

import numpy as np
import pytest

from bullbot.db.migrations import apply_schema
from bullbot.regime.eligibility import HALF_LIFE_DAYS, menu_for


_iter_counter = {"i": 0}


def _strategy(conn, sid, class_name):
    conn.execute(
        "INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (?, ?, 1, '{}', ?, strftime('%s','now'))",
        (sid, class_name, f"hash-{sid}"),
    )


def _proposal(conn, ticker, strategy_id, regime_label, score_a, age_days):
    _iter_counter["i"] += 1
    created_at = int(time.time()) - int(age_days * 86400)
    conn.execute(
        "INSERT INTO evolver_proposals "
        "(ticker, iteration, strategy_id, rationale, llm_cost_usd, "
        " trade_count, passed_gate, created_at, regime_label, score_a) "
        "VALUES (?, ?, ?, '', 0, 5, 1, ?, ?, ?)",
        (ticker, _iter_counter["i"], strategy_id, created_at, regime_label, score_a),
    )


def test_recent_observations_dominate_mean_over_old(tmp_path):
    """10 obs from 360 days ago at score=0.5, 10 from today at score=2.0.
    With half-life=180, old obs have weight ~0.25 each, recent have weight ~1.0.
    Effective n = 10 * 0.25 + 10 * 1.0 = 12.5
    Weighted sum = 10 * 0.25 * 0.5 + 10 * 1.0 * 2.0 = 1.25 + 20 = 21.25
    Weighted mean = 21.25 / 12.5 = 1.7
    """
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    _strategy(conn, 1, "PutCreditSpread")
    for _ in range(10):
        _proposal(conn, "META", 1, "up/low/low", 0.5, age_days=360)
        _proposal(conn, "META", 1, "up/low/low", 2.0, age_days=0)
    conn.commit()
    # Use np.random.seed for Thompson determinism
    np.random.seed(0)
    menu = menu_for(
        conn, ticker="META", regime_label="up/low/low",
        all_classes=["PutCreditSpread"], n_exploit=1, n_explore=0,
    )
    assert len(menu) == 1
    # Posterior_mean is a Thompson sample around the weighted mean (~1.7),
    # which is much closer to 2.0 (recent) than 0.5 (old). With seed=0
    # the sample is well above 1.0.
    assert menu[0].class_name == "PutCreditSpread"
    assert menu[0].posterior_mean > 1.0
    assert menu[0].status == "exploit"


def test_all_old_observations_fall_back_to_cold_start(tmp_path):
    """20 obs from 5 years ago all have weight ~0.0001 each.
    Effective n = 20 * 0.0001 = 0.002 -- way below MIN_OBS_FOR_EXPLOIT=5.
    Should be cold-start (status='explore').
    """
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    _strategy(conn, 1, "PutCreditSpread")
    for _ in range(20):
        _proposal(conn, "META", 1, "up/low/low", 2.0, age_days=365 * 5)
    conn.commit()
    menu = menu_for(
        conn, ticker="META", regime_label="up/low/low",
        all_classes=["PutCreditSpread"], n_exploit=3, n_explore=1,
    )
    assert len(menu) == 1
    assert menu[0].status == "explore"


def test_half_life_constant_is_180(tmp_path):
    """Sanity check that the constant is exposed and equals 180 days."""
    assert HALF_LIFE_DAYS == 180
