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


def test_propose_threads_budget_into_user_prompt(monkeypatch):
    """propose() should compute per_trade_budget_usd from category and pass it through."""
    seen: dict = {}

    def fake_messages_create(**kwargs):
        seen["user"] = kwargs["messages"][0]["content"]

        class _Block:
            text = '{"class_name":"PutCreditSpread","params":{"dte":21,"short_delta":0.3,"width":5,"iv_rank_min":50,"profit_target_pct":0.5,"stop_loss_mult":2.0,"min_dte_close":7},"rationale":"x"}'

        class _Resp:
            content = [_Block()]
            usage = type("U", (), {"input_tokens": 100, "output_tokens": 50})()

        return _Resp()

    fake_client = type("C", (), {"messages": type("M", (), {"create": staticmethod(fake_messages_create)})()})()
    snap = _make_snapshot(ticker="MSTR")

    proposer.propose(
        client=fake_client,
        snapshot=snap,
        history=[],
        best_strategy_id=None,
        category="growth",
        model="claude-sonnet-4-6",
    )
    assert "Max loss per trade" in seen["user"]
    assert "4300" in seen["user"] or "4,300" in seen["user"]
