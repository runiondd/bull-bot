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
