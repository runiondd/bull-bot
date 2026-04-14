from bullbot.dashboard import templates


def test_page_shell_wraps_content():
    html = templates.page_shell("2026-04-14 12:00", "<p>body</p>")
    assert "Bull-Bot Dashboard" in html
    assert "2026-04-14 12:00" in html
    assert "<p>body</p>" in html
    assert "<html" in html
    assert "switchTab" in html


def test_summary_cards():
    html = templates.summary_cards({
        "open_positions": 3,
        "paper_pnl": 1247.50,
        "llm_spend": 4.98,
    })
    assert "$265,000" in html
    assert "3" in html
    assert "+$1,247.50" in html
    assert "$4.98" in html


def test_summary_cards_negative_pnl():
    html = templates.summary_cards({
        "open_positions": 0,
        "paper_pnl": -500.0,
        "llm_spend": 1.0,
    })
    assert "-$500.00" in html


def test_ticker_grid_row():
    html = templates.ticker_grid([{
        "ticker": "SPY",
        "phase": "paper_trial",
        "iteration_count": 3,
        "paper_trade_count": 1,
        "strategy": "PutCreditSpread",
    }])
    assert "SPY" in html
    assert "paper_trial" in html
    assert "PutCreditSpread" in html
    assert 'data-ticker="SPY"' in html


def test_evolver_card_pass():
    html = templates.evolver_section([{
        "ticker": "TSLA",
        "iteration": 1,
        "class_name": "GrowthLEAPS",
        "params": {"target_delta": 0.6},
        "rationale": "Long-dated calls for growth",
        "pf_is": 1.3,
        "pf_oos": float("inf"),
        "max_dd_pct": 0.0,
        "trade_count": 6,
        "passed_gate": True,
        "created_at": 1000,
        "llm_cost_usd": 0.03,
    }])
    assert "GrowthLEAPS" in html
    assert "PASS" in html
    assert "target_delta=0.6" in html
    assert "Long-dated calls for growth" in html


def test_evolver_card_fail_is_dimmed():
    html = templates.evolver_section([{
        "ticker": "TSLA",
        "iteration": 2,
        "class_name": "BearPutSpread",
        "params": {},
        "rationale": "test",
        "pf_is": 0.5,
        "pf_oos": 0.0,
        "max_dd_pct": 0.5,
        "trade_count": 0,
        "passed_gate": False,
        "created_at": 2000,
        "llm_cost_usd": 0.02,
    }])
    assert "FAIL" in html
    assert "opacity" in html


def test_position_card_open():
    html = templates.positions_section([{
        "id": 1, "run_id": "paper", "ticker": "TSLA", "class_name": "GrowthLEAPS",
        "contracts": 1, "open_price": -9528.0, "close_price": None,
        "mark_to_mkt": -8800.0, "opened_at": 1000, "closed_at": None,
        "pnl_realized": None, "exit_rules": {"profit_target_pct": 0.9},
        "legs": [{"option_symbol": "TSLA270119C00260000", "side": "long", "quantity": 1, "strike": 260.0, "expiry": "2027-01-19", "kind": "C"}],
        "is_open": True, "is_backtest": False,
    }])
    assert "TSLA" in html
    assert "OPEN" in html
    assert "TSLA270119C00260000" in html


def test_transactions_table():
    html = templates.transactions_section([{
        "id": 1, "run_id": "paper", "ticker": "SPY", "intent": "close",
        "legs": [{"option_symbol": "SPY260515P00570000", "side": "short", "quantity": 1, "strike": 570.0, "expiry": "2026-05-15", "kind": "P"}],
        "status": "filled", "commission": 1.30, "pnl": 95.0,
        "placed_at": 2000, "class_name": "PCS", "is_backtest": False,
    }])
    assert "SPY" in html
    assert "close" in html
    assert "+$95.00" in html


def test_costs_section():
    html = templates.costs_section({
        "llm_per_ticker": {"SPY": 2.50, "TSLA": 0.03},
        "llm_ledger_total": 3.00,
        "paper_commissions": 6.50,
        "backtest_commissions": 502.30,
    })
    assert "SPY" in html
    assert "$2.50" in html
    assert "$6.50" in html
