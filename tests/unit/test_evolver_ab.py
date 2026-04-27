"""Unit tests for bullbot.evolver.ab — Phase 2 A/B model selection."""
from __future__ import annotations

from bullbot.evolver import ab


def test_pick_returns_default_model_when_ab_disabled(monkeypatch):
    """With A/B off, every ticker gets PROPOSER_MODEL."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_MODEL_AB_ENABLED", False)
    monkeypatch.setattr(config, "PROPOSER_MODEL", "claude-opus-4-6")
    for t in ["AAPL", "SPY", "TSLA", "NVDA"]:
        assert ab.pick_proposer_model(t) == "claude-opus-4-6"


def test_pick_is_deterministic_for_same_ticker(monkeypatch):
    """Same ticker → same model on every call (no flip-flopping mid-day)."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_MODEL_AB_ENABLED", True)
    monkeypatch.setattr(config, "PROPOSER_MODEL_A", "claude-opus-4-6")
    monkeypatch.setattr(config, "PROPOSER_MODEL_B", "claude-sonnet-4-6")
    for t in ["AAPL", "SPY", "TSLA", "NVDA", "AMD", "META"]:
        first = ab.pick_proposer_model(t)
        for _ in range(5):
            assert ab.pick_proposer_model(t) == first


def test_pick_splits_universe_roughly_evenly(monkeypatch):
    """Across the live UNIVERSE, hash split must put both arms at ≥30%.
    With 16 tickers a perfect 50/50 isn't required, but neither arm should
    be empty or near-empty."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_MODEL_AB_ENABLED", True)
    monkeypatch.setattr(config, "PROPOSER_MODEL_A", "claude-opus-4-6")
    monkeypatch.setattr(config, "PROPOSER_MODEL_B", "claude-sonnet-4-6")

    counts = {"claude-opus-4-6": 0, "claude-sonnet-4-6": 0}
    for t in config.UNIVERSE:
        counts[ab.pick_proposer_model(t)] += 1
    n = len(config.UNIVERSE)
    assert counts["claude-opus-4-6"] / n >= 0.30
    assert counts["claude-sonnet-4-6"] / n >= 0.30


def test_pick_returns_only_configured_models(monkeypatch):
    """Output is always one of the two configured arms — never anything else."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_MODEL_AB_ENABLED", True)
    monkeypatch.setattr(config, "PROPOSER_MODEL_A", "MODEL_A")
    monkeypatch.setattr(config, "PROPOSER_MODEL_B", "MODEL_B")
    for t in ["AAPL", "SPY", "TSLA", "NVDA", "AMD", "META", "GOOGL", "MSFT"]:
        assert ab.pick_proposer_model(t) in {"MODEL_A", "MODEL_B"}
