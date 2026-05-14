import sqlite3

from bullbot.db.migrations import apply_schema


def test_leaderboard_view_ranks_by_score_a(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    # Insert 3 proposals with known score_a, trade_count, passed_gate.
    # NVDA fails the gate (passed_gate=0), so the view should exclude it.
    # Of the two that pass, SPY (score_a=2.3) outranks META (score_a=1.5).
    conn.executescript("""
        INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at)
            VALUES (1, 'PutCreditSpread', 1, '{}', 'a', strftime('%s','now'));
        INSERT INTO evolver_proposals
            (ticker, iteration, strategy_id, rationale, llm_cost_usd,
             pf_is, pf_oos, trade_count, passed_gate, created_at,
             regime_label, score_a, size_units, max_loss_per_trade)
        VALUES
            ('META', 1, 1, '', 0, 1.9, 1.0, 7, 1, strftime('%s','now'),
             'up/low/low', 1.5, 10, 350),
            ('NVDA', 1, 1, '', 0, 1.0, 0.5, 16, 0, strftime('%s','now'),
             'flat/mid/mid', 0.8, 5, 500),
            ('SPY', 1, 1, '', 0, 2.1, 0.8, 12, 1, strftime('%s','now'),
             'up/low/low', 2.3, 8, 400);
    """)
    conn.commit()
    rows = list(conn.execute(
        "SELECT ticker, score_a FROM leaderboard ORDER BY rank ASC"
    ))
    # SPY (score_a=2.3) > META (score_a=1.5); NVDA fails gate so excluded
    assert rows[0][0] == "SPY"
    assert rows[0][1] == 2.3
    assert rows[1][0] == "META"
    assert rows[1][1] == 1.5
    assert len(rows) == 2


def test_leaderboard_view_excludes_low_trade_count(tmp_path):
    """Trade count < 5 should be filtered out even if score_a is high and gate passed."""
    conn = sqlite3.connect(tmp_path / "t.db")
    apply_schema(conn)
    conn.executescript("""
        INSERT INTO strategies (id, class_name, class_version, params, params_hash, created_at)
            VALUES (1, 'PutCreditSpread', 1, '{}', 'a', strftime('%s','now'));
        INSERT INTO evolver_proposals
            (ticker, iteration, strategy_id, rationale, llm_cost_usd,
             pf_is, pf_oos, trade_count, passed_gate, created_at,
             regime_label, score_a, size_units, max_loss_per_trade)
        VALUES
            ('META', 1, 1, '', 0, 1.9, 1.0, 4, 1, strftime('%s','now'),
             'up/low/low', 5.0, 10, 350),
            ('SPY',  1, 1, '', 0, 2.1, 0.8, 5, 1, strftime('%s','now'),
             'up/low/low', 1.0, 8, 400);
    """)
    conn.commit()
    rows = list(conn.execute("SELECT ticker FROM leaderboard"))
    assert len(rows) == 1
    assert rows[0][0] == "SPY"
