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


def test_inventory_tab_renders_table():
    data = {"inventory": [
        {"account": "growth", "ticker": "NVDA", "kind": "C", "strike": 130,
         "expiry": "2026-12-19", "qty": 1, "costBasis": 1940.00},
        {"account": "income", "ticker": "AAPL", "kind": "S", "strike": 0,
         "expiry": "", "qty": 100, "costBasis": 178.40},
    ]}
    html_str = tabs.inventory_tab(data)
    assert "NVDA" in html_str
    assert "AAPL" in html_str
    assert "growth" in html_str
    assert "income" in html_str
    assert "shares" in html_str
    assert "call" in html_str
    assert "$1,940" in html_str or "1,940.00" in html_str


def test_inventory_tab_empty_no_crash():
    html_str = tabs.inventory_tab({"inventory": []})
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


def test_transactions_tab_renders_with_totals():
    data = {"orders": [
        {"date": "2026-04-26 10:14", "ticker": "QQQ", "className": "PutCreditSpread",
         "intent": "open", "legs": "S 2x QQQ 437P / L 2x QQQ 427P",
         "pnl": None, "commission": 5.20, "isBacktest": False},
        {"date": "2026-04-23 11:20", "ticker": "QQQ", "className": "PutCreditSpread",
         "intent": "close", "legs": "S 2x QQQ 430P / L 2x QQQ 420P",
         "pnl": 94.0, "commission": 5.20, "isBacktest": False},
    ]}
    html_str = tabs.transactions_tab(data)
    assert "QQQ" in html_str
    assert "PutCreditSpread" in html_str
    assert "open" in html_str.lower()
    assert "TOTALS" in html_str.upper() or "totals" in html_str.lower()
    assert "+$94" in html_str  # signed P&L formatting
    assert "10.40" in html_str  # commission total 5.20+5.20

def test_transactions_tab_empty_no_crash():
    html_str = tabs.transactions_tab({"orders": []})
    assert html_str


def test_health_tab_renders_universe_summary_and_checks():
    data = {"health": {
        "universe": {"total": 16, "live": 3, "paper_trial": 6, "discovering": 4, "no_edge": 3},
        "checks": [
            {"name": "Data freshness", "status": "ok", "detail": "All bars current"},
            {"name": "LLM budget", "status": "warn", "detail": "57% through budget"},
        ],
    }}
    html_str = tabs.health_tab(data)
    assert ">16<" in html_str  # universe total
    assert "Live" in html_str
    assert "Paper Trial" in html_str
    assert "No Edge" in html_str
    assert "Data freshness" in html_str
    assert "LLM budget" in html_str
    assert "All bars current" in html_str

def test_health_tab_empty_checks_no_crash():
    data = {"health": {"universe": {"total": 0, "live": 0, "paper_trial": 0, "discovering": 0, "no_edge": 0}, "checks": []}}
    html_str = tabs.health_tab(data)
    assert html_str


def test_costs_tab_renders_breakdown():
    data = {
        "costs": {
            "llmPerTicker": {"AAPL": 4.20, "MSFT": 3.80, "TSLA": 3.10},
            "llmTotal": 28.74, "llmBudget": 50.00,
            "paperCommissions": 67.20, "backtestCommissions": 1284.50,
        },
        "metrics": {"paperTradeCount": 31, "backtestCount": 247},
    }
    html_str = tabs.costs_tab(data)
    assert "AAPL" in html_str
    assert "28.74" in html_str
    assert "67.20" in html_str
    assert "1,284.50" in html_str or "1284.50" in html_str
    assert "Cost Efficiency" in html_str

def test_costs_tab_zero_paper_trades_no_div_zero():
    data = {
        "costs": {"llmPerTicker": {}, "llmTotal": 0, "llmBudget": 50.0,
                  "paperCommissions": 0, "backtestCommissions": 0},
        "metrics": {"paperTradeCount": 0, "backtestCount": 0},
    }
    html_str = tabs.costs_tab(data)
    assert html_str  # don't crash on division-by-zero


def test_leaderboard_tab_renders_table():
    data = {"leaderboard": [
        {"proposal_id": 412, "ticker": "AAPL", "class_name": "PutCreditSpread",
         "regime_label": "trending", "score_a": 1.84, "size_units": 1,
         "max_loss_per_trade": 250.0, "trade_count": 24, "rank": 1},
        {"proposal_id": 410, "ticker": "TSLA", "class_name": "GrowthLEAPS",
         "regime_label": "trending", "score_a": 1.42, "size_units": 1,
         "max_loss_per_trade": 480.0, "trade_count": 12, "rank": 2},
        {"proposal_id": 405, "ticker": "SPY", "class_name": "IronCondor",
         "regime_label": "range", "score_a": 0.95, "size_units": 2,
         "max_loss_per_trade": 320.0, "trade_count": 8, "rank": 3},
    ]}
    html_str = tabs.leaderboard_tab(data)
    # Tickers visible
    assert "AAPL" in html_str
    assert "TSLA" in html_str
    assert "SPY" in html_str
    # Strategy class names visible
    assert "PutCreditSpread" in html_str
    assert "GrowthLEAPS" in html_str
    assert "IronCondor" in html_str
    # Regime labels visible
    assert "trending" in html_str
    assert "range" in html_str
    # score_a formatted (1.84 → some readable form)
    assert "1.84" in html_str or "184%" in html_str
    # Proposal id surfaced for traceability
    assert "412" in html_str
    # Column header for ranking
    assert "Rank" in html_str or "rank" in html_str


def test_leaderboard_tab_empty_renders_empty_state():
    html_str = tabs.leaderboard_tab({"leaderboard": []})
    assert html_str  # non-empty
    # Empty-state message mentions warming up / no entries — match pattern from
    # other empty-state tabs (e.g. "No P&L yet — paper trial in progress").
    lowered = html_str.lower()
    assert "no entries" in lowered or "warming up" in lowered or "no leaderboard" in lowered
