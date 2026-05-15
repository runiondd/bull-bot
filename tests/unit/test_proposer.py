"""Unit tests for the proposer module."""

from __future__ import annotations

from bullbot.evolver import proposer
from bullbot.strategies.base import StrategySnapshot


def _make_snapshot(ticker: str = "MSTR", spot: float = 400.0) -> StrategySnapshot:
    return StrategySnapshot(
        ticker=ticker,
        asof_ts=0,
        spot=spot,
        bars_1d=[],
        indicators={},
        atm_greeks={},
        iv_rank=50.0,
        regime="chop",
        chain=[],
        market_brief="",
        ticker_brief="",
    )


def test_user_prompt_includes_per_trade_budget():
    snap = _make_snapshot()
    text = proposer.build_user_prompt(
        snapshot=snap,
        history=[],
        best_strategy_id=None,
        per_trade_budget_usd=4300.0,
    )
    assert "Max loss per trade" in text
    assert "4300" in text or "4,300" in text


def test_user_prompt_budget_present_for_low_capital():
    snap = _make_snapshot()
    text = proposer.build_user_prompt(
        snapshot=snap,
        history=[],
        best_strategy_id=None,
        per_trade_budget_usd=1000.0,
    )
    assert "1000" in text or "1,000" in text
