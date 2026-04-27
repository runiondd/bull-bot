"""Tests for tab render functions."""
from bullbot.dashboard import tabs


def test_overview_tab_renders_required_sections():
    data = {
        "equity_curve": [{"total_equity": 265000.0 + i * 100} for i in range(30)],
        "metrics": {"realized_pnl": 100, "unrealized_pnl": 50, "sharpe_30d": 1.2,
                    "win_rate": 0.6, "avg_win": 200, "avg_loss": -100,
                    "profit_factor": 1.5, "open_positions": 3,
                    "llm_spend": 0, "llm_spend_7d": 0, "paper_trade_count": 0,
                    "backtest_count": 0},
        "pnl_by_ticker": [
            {"ticker": "SPY", "realized": 100, "unrealized": 50},
            {"ticker": "QQQ", "realized": -30, "unrealized": 0},
        ],
        "universe": [
            {"ticker": "SPY", "category": "income", "phase": "live",
             "strategy": "PutCreditSpread", "iterations": 5, "paperTrades": 2,
             "edge": {"pf_oos": 1.4, "pf_is": 1.6, "dd": -0.05}},
        ],
        "activity": [],
    }
    html_str = tabs.overview_tab(data)
    assert "Equity Curve" in html_str
    assert "P&amp;L by Ticker" in html_str
    assert "Universe Pipeline" in html_str
    assert "Activity" in html_str


def test_overview_tab_empty_universe_no_crash():
    data = {"equity_curve": [], "metrics": {"realized_pnl": 0,
            "unrealized_pnl": 0, "sharpe_30d": 0, "win_rate": 0,
            "avg_win": 0, "avg_loss": 0, "profit_factor": 0,
            "open_positions": 0, "llm_spend": 0, "llm_spend_7d": 0,
            "paper_trade_count": 0, "backtest_count": 0},
            "pnl_by_ticker": [], "universe": [], "activity": []}
    html_str = tabs.overview_tab(data)
    assert html_str  # non-empty


def test_positions_tab_renders_with_open_and_closed():
    data = {"positions": [
        {"id": 1, "ticker": "SPY", "className": "PutCreditSpread", "isOpen": True,
         "openedAt": "2026-04-22", "entrySpot": 521.40, "mark": 0.42, "openPrice": 0.78,
         "pnl": 180.0, "pnlPct": 0.46, "dte": 19,
         "legs": [{"side": "short", "qty": 1, "strike": 510, "kind": "P", "expiry": "2026-05-15"}],
         "exitRules": {"profit_target_pct": 0.50}, "rationale": "test rationale"},
        {"id": 2, "ticker": "META", "className": "PutCreditSpread", "isOpen": False,
         "openedAt": "2026-04-02", "closedAt": "2026-04-09", "entrySpot": 598.40,
         "mark": 1.92, "openPrice": 1.10, "pnl": -164.0, "pnlPct": -0.74, "dte": 21,
         "legs": [{"side": "short", "qty": 1, "strike": 590, "kind": "P", "expiry": "2026-04-30"}],
         "exitRules": {"profit_target_pct": 0.50}, "rationale": "miscall"},
    ]}
    html_str = tabs.positions_tab(data)
    assert "SPY" in html_str
    assert "META" in html_str
    assert "PutCreditSpread" in html_str or "Put Credit Spread" in html_str
    assert "open" in html_str.lower()
    assert "closed" in html_str.lower()
    assert "test rationale" in html_str
    assert "miscall" in html_str
    # Filter bar buttons
    assert "All" in html_str


def test_positions_tab_empty_no_crash():
    html_str = tabs.positions_tab({"positions": []})
    assert html_str  # non-empty


def test_evolver_tab_renders_proposals_table():
    data = {"proposals": [
        {"id": "ep_412", "ticker": "AAPL", "className": "PutCreditSpread",
         "iteration": 9, "passed": True, "createdAt": "2026-04-26 02:14 UTC",
         "pf_oos": 1.38, "pf_is": 1.62, "max_dd_pct": -0.06, "trade_count": 24,
         "llm_cost": 0.42, "params": {"delta_short": 0.18, "width": 10},
         "rationale": "tightened delta"},
        {"id": "ep_410", "ticker": "TSLA", "className": "GrowthLEAPS",
         "iteration": 8, "passed": False, "createdAt": "2026-04-25 18:22 UTC",
         "pf_oos": 1.18, "pf_is": 1.40, "max_dd_pct": -0.14, "trade_count": 12,
         "llm_cost": 0.51, "params": {"delta_long": 0.60},
         "rationale": "below gate"},
    ]}
    html_str = tabs.evolver_tab(data)
    assert "AAPL" in html_str
    assert "TSLA" in html_str
    assert "PASS" in html_str.upper() or "pass" in html_str.lower()
    assert "FAIL" in html_str.upper() or "fail" in html_str.lower()
    # Filter labels
    assert "All" in html_str
    assert "Passed" in html_str or "passed" in html_str.lower()
    assert "Rejected" in html_str or "rejected" in html_str.lower()


def test_evolver_tab_empty_no_crash():
    html_str = tabs.evolver_tab({"proposals": []})
    assert html_str


def test_universe_tab_renders_table():
    data = {"universe": [
        {"ticker": "SPY", "category": "income", "phase": "live",
         "strategy": "PutCreditSpread", "iterations": 18, "paperTrades": 8,
         "edge": {"pf_oos": 1.84, "pf_is": 2.10, "dd": -0.06}},
        {"ticker": "XLE", "category": "income", "phase": "no_edge",
         "strategy": None, "iterations": 22, "paperTrades": 0,
         "edge": {"pf_oos": 0.92, "pf_is": 1.08, "dd": -0.18}},
    ]}
    html_str = tabs.universe_tab(data)
    assert "SPY" in html_str
    assert "XLE" in html_str
    assert "PutCreditSpread" in html_str
    assert "income" in html_str
    assert "Ticker" in html_str  # column header


def test_universe_tab_empty_no_crash():
    html_str = tabs.universe_tab({"universe": []})
    assert html_str
