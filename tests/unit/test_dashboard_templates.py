from bullbot.dashboard import templates


def test_page_shell_wraps_content():
    # Updated for reskin: timestamp no longer embedded in shell (moved to body
    # templates); switchTab replaced by IIFE showTab; title now "Bull-Bot — Dashboard"
    html = templates.page_shell("2026-04-14 12:00", "<p>body</p>")
    assert "Bull-Bot" in html
    assert "<p>body</p>" in html
    assert "<html" in html
    assert "showTab" in html


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
    assert "Long 1x 260C Jan-19-27" in html


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


def test_page_shell_includes_lifted_css():
    from bullbot.dashboard import templates, styles_css
    body = "<div>hi</div>"
    html_str = templates.page_shell("2026-04-26 12:00 UTC", body)
    # Sample of CSS tokens that must be present
    assert "oklch(15% 0.005 250)" in html_str  # --bg-0
    assert ".chip.live" in html_str
    assert "data-theme" in html_str
    assert "data-accent" in html_str


def test_page_shell_loads_ibm_plex_via_link():
    from bullbot.dashboard import templates
    html_str = templates.page_shell("ts", "")
    assert "fonts.googleapis.com" in html_str
    assert "IBM+Plex+Sans" in html_str
    assert "IBM+Plex+Mono" in html_str


def test_page_shell_includes_tab_switching_js():
    from bullbot.dashboard import templates
    html_str = templates.page_shell("ts", "")
    assert "<script>" in html_str
    # Tab switching toggles .active on .nav-item and shows .tab-content
    assert "nav-item" in html_str
    assert "tab-content" in html_str


def test_page_shell_embeds_body_content():
    from bullbot.dashboard import templates
    html_str = templates.page_shell("ts", "<div id='test-marker'>marker</div>")
    assert "<div id='test-marker'>marker</div>" in html_str


def test_page_shell_has_meta_refresh_60s():
    """G.2: dashboard auto-refreshes every 60s so the served HTML stays current
    without a Flask process. Renders as a meta refresh tag in <head>."""
    from bullbot.dashboard import templates
    html_str = templates.page_shell("ts", "")
    assert '<meta http-equiv="refresh" content="60">' in html_str


def test_header_section_includes_last_updated_label():
    """G.2: the generated-at timestamp is labeled 'Last updated' so a PM
    glancing at the dashboard understands what the timestamp means."""
    from bullbot.dashboard import templates
    html_str = templates.header_section(
        generated_at="2026-05-14 21:00 UTC",
        total_pnl=0.0,
    )
    assert "Last updated" in html_str
    assert "2026-05-14 21:00 UTC" in html_str


def test_header_section_includes_brand_and_pnl():
    from bullbot.dashboard import templates
    html_str = templates.header_section(
        generated_at="2026-04-26 12:00 UTC",
        total_pnl=123.45,
    )
    assert '<header class="app-header">' in html_str
    assert "Bull-Bot" in html_str
    assert "v3" in html_str  # version sub
    assert "2026-04-26 12:00 UTC" in html_str
    assert "+$123" in html_str  # signed money formatting


def test_header_section_negative_pnl():
    from bullbot.dashboard import templates
    html_str = templates.header_section(generated_at="ts", total_pnl=-50.0)
    assert "-$50" in html_str
    assert "neg" in html_str  # pnl_class adds 'neg'


def test_sidebar_section_lists_all_8_tabs_in_2_groups():
    from bullbot.dashboard import templates
    counts = {
        "positions": 6, "evolver": 12, "universe": 16,
        "transactions": 47, "health": 1, "inventory": 3,
    }
    html_str = templates.sidebar_section(active_tab="overview", counts=counts)
    for tab in ("Overview", "Positions", "Evolver", "Universe",
                "Transactions", "Health", "Costs", "Inventory"):
        assert tab in html_str
    assert ">Operations<" in html_str
    assert ">Diagnostics<" in html_str
    assert 'data-tab="overview"' in html_str
    assert "active" in html_str


def test_sidebar_section_renders_badge_counts():
    from bullbot.dashboard import templates
    counts = {"positions": 3, "evolver": 0, "universe": 16,
              "transactions": 5, "health": 2, "inventory": 1}
    html_str = templates.sidebar_section(active_tab="overview", counts=counts)
    assert ">3<" in html_str
    assert ">16<" in html_str


def test_sidebar_section_omits_zero_badges():
    from bullbot.dashboard import templates
    counts = {"positions": 0, "evolver": 0, "universe": 0,
              "transactions": 0, "health": 0, "inventory": 0}
    html_str = templates.sidebar_section(active_tab="overview", counts=counts)
    assert html_str  # just don't crash


def test_kpi_strip_renders_5_cards():
    from bullbot.dashboard import templates
    account = {"total_equity": 268_412.18, "income_account": 51_204.42,
               "growth_account": 217_207.76, "target_monthly": 10_000,
               "month_to_date": 4_812.55, "days_to_target": 75}
    metrics = {"open_positions": 6, "realized_pnl": 3_104.55,
               "unrealized_pnl": 1_708.00, "llm_spend": 28.74,
               "llm_spend_7d": 6.91, "sharpe_30d": 1.42, "win_rate": 0.68,
               "profit_factor": 1.71}
    equity_curve = [{"total_equity": 265_000.0 + i * 100} for i in range(30)]
    html_out = templates.kpi_strip(account=account, metrics=metrics,
                                     equity_curve=equity_curve)
    assert '<div class="kpi-grid">' in html_out
    assert "Total Equity" in html_out
    assert "Realized P&amp;L" in html_out
    assert "Unrealized P&amp;L" in html_out
    assert "Target Progress" in html_out
    assert "LLM Spend" in html_out


def test_kpi_strip_empty_metrics_no_crash():
    from bullbot.dashboard import templates
    account = {"total_equity": 265_000, "income_account": 50_000,
               "growth_account": 215_000, "target_monthly": 10_000,
               "month_to_date": 0, "days_to_target": 75}
    metrics = {"open_positions": 0, "realized_pnl": 0, "unrealized_pnl": 0,
               "llm_spend": 0, "llm_spend_7d": 0, "sharpe_30d": 0,
               "win_rate": 0, "profit_factor": 0}
    html_out = templates.kpi_strip(account=account, metrics=metrics,
                                     equity_curve=[])
    assert '<div class="kpi-grid">' in html_out


# ---------- test_status_tiles (G.3) ----------


def test_status_tiles_renders_three_tiles():
    """status_tiles renders three labelled tiles with per-color CSS classes."""
    daemon = {"status": "fresh", "value": "<5m ago", "color": "green"}
    cost = {"value": "$0.50 / $5.00", "color": "green", "cost": 0.5, "cap": 5.0}
    sweep = {"value": "92%", "color": "amber", "successes": 23, "failures": 2}

    html_out = templates.status_tiles(daemon, cost, sweep)

    # Three tile labels
    assert "Daemon" in html_out
    assert "LLM Cost" in html_out
    assert "Sweep Success" in html_out

    # Each value appears (the daemon "<" is HTML-escaped)
    assert "5m ago" in html_out
    assert "$0.50 / $5.00" in html_out
    assert "92%" in html_out

    # Per-color tile classes are present
    assert "tile-green" in html_out
    assert "tile-amber" in html_out


def test_status_tiles_red_and_gray_classes():
    """Red and gray colors map to corresponding tile classes."""
    daemon = {"status": "down", "value": "no heartbeat", "color": "red"}
    cost = {"value": "$5.00 / $5.00", "color": "red", "cost": 5.0, "cap": 5.0}
    sweep = {"value": "no sweeps yet", "color": "gray", "successes": 0, "failures": 0}

    html_out = templates.status_tiles(daemon, cost, sweep)
    assert "tile-red" in html_out
    assert "tile-gray" in html_out
    assert "no heartbeat" in html_out
    assert "no sweeps yet" in html_out
