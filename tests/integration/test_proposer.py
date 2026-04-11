"""Proposer tests — uses FakeAnthropicClient from conftest."""
import json
import pytest
from bullbot.evolver import proposer
from bullbot.strategies.base import StrategySnapshot


def _snap():
    return StrategySnapshot(
        ticker="SPY", asof_ts=1718395200, spot=582.14,
        bars_1d=[], indicators={"sma_20": 578.45, "rsi_14": 58.4},
        atm_greeks={"delta": 0.52}, iv_rank=60.0, regime="bull", chain=[],
    )


def test_proposer_returns_parsed_proposal(fake_anthropic):
    fake_anthropic.queue_response(json.dumps({
        "class_name": "PutCreditSpread",
        "params": {"dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50},
        "rationale": "Baseline credit spread for bull regime",
    }))
    result = proposer.propose(client=fake_anthropic, snapshot=_snap(), history=[], best_strategy_id=None)
    assert result.class_name == "PutCreditSpread"
    assert result.params["dte"] == 14
    assert "credit spread" in result.rationale.lower()
    assert result.llm_cost_usd > 0


def test_proposer_retries_once_on_malformed_json(fake_anthropic):
    fake_anthropic.queue_response("not json")
    fake_anthropic.queue_response(json.dumps({
        "class_name": "IronCondor",
        "params": {"dte": 21, "wing_delta": 0.20, "wing_width": 5, "iv_rank_min": 60},
        "rationale": "Second attempt",
    }))
    result = proposer.propose(fake_anthropic, _snap(), [], None)
    assert result.class_name == "IronCondor"


def test_proposer_raises_after_two_malformed(fake_anthropic):
    fake_anthropic.queue_response("still not json")
    fake_anthropic.queue_response("also not json")
    with pytest.raises(proposer.ProposerJsonError):
        proposer.propose(fake_anthropic, _snap(), [], None)


def test_proposer_raises_on_unknown_class(fake_anthropic):
    fake_anthropic.queue_response(json.dumps({
        "class_name": "NonExistentStrategy",
        "params": {},
        "rationale": "test",
    }))
    with pytest.raises(proposer.ProposerUnknownStrategyError):
        proposer.propose(fake_anthropic, _snap(), [], None)


def test_build_history_block_formats_past_proposals():
    history = [
        {"iteration": 3, "class_name": "PutCreditSpread", "params": '{"dte": 14}',
         "pf_is": 1.2, "pf_oos": 0.9, "trade_count": 40, "passed_gate": 0, "rationale": "test"},
    ]
    block = proposer.build_history_block(history)
    assert "iter=3" in block
    assert "PutCreditSpread" in block
    assert "FAILED" in block or "failed" in block


def test_build_user_prompt_includes_regime_briefs():
    from bullbot.evolver.proposer import build_user_prompt
    from bullbot.strategies.base import StrategySnapshot

    snap = StrategySnapshot(
        ticker="AAPL",
        asof_ts=1000000,
        spot=180.0,
        bars_1d=[],
        indicators={"sma_20": 178.0},
        atm_greeks={},
        iv_rank=65.0,
        regime="bull",
        chain=[],
        market_brief="Low vol bull regime. Favors short puts.",
        ticker_brief="AAPL IV elevated at 72nd pct. Consider credit spreads.",
    )

    prompt = build_user_prompt(snap, [], None)
    assert "=== Market Regime Analysis ===" in prompt
    assert "Low vol bull regime" in prompt
    assert "=== Ticker Analysis (AAPL) ===" in prompt
    assert "AAPL IV elevated" in prompt


def test_build_user_prompt_omits_regime_when_empty():
    from bullbot.evolver.proposer import build_user_prompt
    from bullbot.strategies.base import StrategySnapshot

    snap = StrategySnapshot(
        ticker="AAPL",
        asof_ts=1000000,
        spot=180.0,
        bars_1d=[],
        indicators={},
        atm_greeks={},
        iv_rank=50.0,
        regime="bull",
        chain=[],
    )

    prompt = build_user_prompt(snap, [], None)
    assert "=== Market Regime Analysis ===" not in prompt
