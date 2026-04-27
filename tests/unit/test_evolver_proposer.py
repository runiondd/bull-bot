"""Unit tests for bullbot.evolver.proposer."""
from __future__ import annotations

import pytest

from bullbot.evolver import proposer
from bullbot.evolver.proposer import (
    Proposal,
    ProposerJsonError,
    ProposerUnknownStrategyError,
    build_history_block,
    build_user_prompt,
)
from bullbot.strategies.base import StrategySnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snap(**overrides) -> StrategySnapshot:
    defaults = dict(
        ticker="SPY",
        asof_ts=0,
        spot=500.0,
        bars_1d=[],
        indicators={},
        atm_greeks={},
        iv_rank=50.0,
        regime="up_low_vix",
        chain=[],
        market_brief="",
        ticker_brief="",
    )
    defaults.update(overrides)
    return StrategySnapshot(**defaults)


# ---------------------------------------------------------------------------
# build_history_block
# ---------------------------------------------------------------------------


def test_build_history_block_empty():
    assert build_history_block([]) == "(no prior proposals)"


def test_build_history_block_formats_entries():
    history = [
        {
            "iteration": 1,
            "class_name": "PutCreditSpread",
            "params": {"dte": 21},
            "pf_is": 1.5,
            "pf_oos": 1.2,
            "trade_count": 10,
            "passed_gate": True,
            "rationale": "ok",
        }
    ]
    block = build_history_block(history)
    assert "iter=1" in block
    assert "PutCreditSpread" in block
    assert "PASSED" in block


# ---------------------------------------------------------------------------
# build_user_prompt
# ---------------------------------------------------------------------------


def test_build_user_prompt_contains_ticker(sample_indicators, sample_key_levels):
    snap = _make_snap(ticker="AAPL", spot=195.0)
    prompt = build_user_prompt(snap, [], None)
    assert "AAPL" in prompt
    assert "195.0" in prompt


def test_build_user_prompt_no_history_note(sample_indicators, sample_key_levels):
    snap = _make_snap()
    prompt = build_user_prompt(snap, [], None)
    assert "no prior proposals" in prompt


def test_build_user_prompt_includes_best_strategy_id(
    sample_indicators, sample_key_levels
):
    snap = _make_snap()
    prompt = build_user_prompt(snap, [], best_strategy_id="strat-42")
    assert "strat-42" in prompt


def test_build_user_prompt_market_brief_included(
    sample_indicators, sample_key_levels
):
    snap = _make_snap(market_brief="Bullish regime", ticker_brief="AAPL trending up")
    prompt = build_user_prompt(snap, [], None)
    assert "Bullish regime" in prompt
    assert "AAPL trending up" in prompt


# ---------------------------------------------------------------------------
# propose() — basic happy path
# ---------------------------------------------------------------------------


def test_propose_returns_proposal(fake_anthropic, sample_indicators, sample_key_levels):
    fake_anthropic.queue_response(
        '{"class_name": "PutCreditSpread", '
        '"params": {"dte": 21, "short_delta": 0.30, "width": 5}, '
        '"rationale": "test"}'
    )
    snap = _make_snap()
    result = proposer.propose(fake_anthropic, snap, history=[], best_strategy_id=None)
    assert isinstance(result, Proposal)
    assert result.class_name == "PutCreditSpread"
    assert result.params["dte"] == 21
    assert result.rationale == "test"
    assert result.llm_cost_usd > 0


def test_propose_strips_code_fences(fake_anthropic, sample_indicators, sample_key_levels):
    fake_anthropic.queue_response(
        "```json\n"
        '{"class_name": "PutCreditSpread", '
        '"params": {"dte": 21, "short_delta": 0.30, "width": 5}, '
        '"rationale": "fenced"}\n'
        "```"
    )
    snap = _make_snap()
    result = proposer.propose(fake_anthropic, snap, history=[], best_strategy_id=None)
    assert result.class_name == "PutCreditSpread"


def test_propose_retries_on_bad_json(fake_anthropic, sample_indicators, sample_key_levels):
    fake_anthropic.queue_response("not json at all")
    fake_anthropic.queue_response(
        '{"class_name": "PutCreditSpread", '
        '"params": {"dte": 21, "short_delta": 0.30, "width": 5}, '
        '"rationale": "retry"}'
    )
    snap = _make_snap()
    result = proposer.propose(fake_anthropic, snap, history=[], best_strategy_id=None)
    assert result.class_name == "PutCreditSpread"
    assert result.rationale == "retry"


def test_propose_raises_json_error_after_two_failures(
    fake_anthropic, sample_indicators, sample_key_levels
):
    fake_anthropic.queue_response("bad1")
    fake_anthropic.queue_response("bad2")
    snap = _make_snap()
    with pytest.raises(ProposerJsonError):
        proposer.propose(fake_anthropic, snap, history=[], best_strategy_id=None)


def test_propose_raises_unknown_strategy(
    fake_anthropic, sample_indicators, sample_key_levels
):
    fake_anthropic.queue_response(
        '{"class_name": "NonExistentStrategy", '
        '"params": {}, "rationale": "x"}'
    )
    snap = _make_snap()
    with pytest.raises(ProposerUnknownStrategyError):
        proposer.propose(fake_anthropic, snap, history=[], best_strategy_id=None)


# ---------------------------------------------------------------------------
# propose() — growth category
# ---------------------------------------------------------------------------


def test_propose_growth_category(fake_anthropic, sample_indicators, sample_key_levels):
    fake_anthropic.queue_response(
        '{"class_name": "GrowthLEAPS", '
        '"params": {"dte": 365, "delta": 0.70}, '
        '"rationale": "growth"}'
    )
    snap = _make_snap(ticker="NVDA")
    result = proposer.propose(
        fake_anthropic, snap, history=[], best_strategy_id=None, category="growth"
    )
    assert result.class_name == "GrowthLEAPS"


# ---------------------------------------------------------------------------
# Task 3: prompt-caching tests
# ---------------------------------------------------------------------------


def test_proposer_uses_cached_system_blocks_when_enabled(
    fake_anthropic, sample_indicators, sample_key_levels, monkeypatch
):
    """When PROPOSER_CACHE_ENABLED=True, the system kwarg is a list of content
    blocks where the last block has cache_control."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_CACHE_ENABLED", True)

    from bullbot.evolver import proposer
    from bullbot.strategies.base import StrategySnapshot

    fake_anthropic.queue_response(
        '{"class_name": "PutCreditSpread", '
        '"params": {"dte": 21, "short_delta": 0.30, "width": 5}, '
        '"rationale": "test"}'
    )

    snap = StrategySnapshot(
        ticker="SPY", asof_ts=0, spot=500.0, bars_1d=[],
        indicators={}, atm_greeks={}, iv_rank=50.0,
        regime="up_low_vix", chain=[], market_brief="", ticker_brief="",
    )

    proposer.propose(fake_anthropic, snap, history=[], best_strategy_id=None)

    call = fake_anthropic.call_log[-1]
    system = call["system"]
    assert isinstance(system, list), "system should be a list of blocks when cached"
    assert len(system) >= 1
    assert system[-1].get("cache_control") == {"type": "ephemeral"}


def test_proposer_uses_string_system_when_caching_disabled(
    fake_anthropic, monkeypatch
):
    """When PROPOSER_CACHE_ENABLED=False, fall back to plain string system arg."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_CACHE_ENABLED", False)

    from bullbot.evolver import proposer
    from bullbot.strategies.base import StrategySnapshot

    fake_anthropic.queue_response(
        '{"class_name": "PutCreditSpread", '
        '"params": {"dte": 21, "short_delta": 0.30, "width": 5}, '
        '"rationale": "test"}'
    )

    snap = StrategySnapshot(
        ticker="SPY", asof_ts=0, spot=500.0, bars_1d=[],
        indicators={}, atm_greeks={}, iv_rank=50.0,
        regime="up_low_vix", chain=[], market_brief="", ticker_brief="",
    )

    proposer.propose(fake_anthropic, snap, history=[], best_strategy_id=None)

    call = fake_anthropic.call_log[-1]
    system = call["system"]
    if isinstance(system, list):
        for block in system:
            assert "cache_control" not in block
