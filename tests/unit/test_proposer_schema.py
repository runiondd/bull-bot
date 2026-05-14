import pytest

from bullbot.evolver.proposer import parse_proposer_response
from bullbot.evolver.sweep import StrategySpec


def test_parse_returns_strategy_spec_with_ranges():
    payload = {
        "class": "PutCreditSpread",
        "rationale": "META bull regime, low IV",
        "ranges": {
            "short_delta": [0.20, 0.25, 0.30],
            "width": [5, 10],
            "dte": [21, 30, 45],
            "iv_rank_min": [10, 20, 30],
            "profit_target_pct": [0.5],
            "stop_loss_mult": [2.0],
        },
        "max_loss_per_trade": 350.0,
        "stop_loss_pct": None,
    }
    spec = parse_proposer_response(payload)
    assert isinstance(spec, StrategySpec)
    assert spec.class_name == "PutCreditSpread"
    assert spec.ranges["short_delta"] == [0.20, 0.25, 0.30]
    assert spec.max_loss_per_trade == 350.0
    assert spec.stop_loss_pct is None


def test_parse_equity_with_stop_loss_pct():
    payload = {
        "class": "GrowthEquity",
        "rationale": "long hold",
        "ranges": {"hold_days": [60, 90]},
        "max_loss_per_trade": 5000.0,
        "stop_loss_pct": 0.20,
    }
    spec = parse_proposer_response(payload)
    assert spec.class_name == "GrowthEquity"
    assert spec.stop_loss_pct == 0.20


def test_parse_missing_class_raises():
    payload = {"ranges": {}, "max_loss_per_trade": 100.0}
    with pytest.raises((KeyError, ValueError)):
        parse_proposer_response(payload)


def test_parse_missing_ranges_raises():
    payload = {"class": "X", "max_loss_per_trade": 100.0}
    with pytest.raises((KeyError, ValueError)):
        parse_proposer_response(payload)


def test_parse_missing_max_loss_raises():
    payload = {"class": "X", "ranges": {"a": [1]}}
    with pytest.raises((KeyError, ValueError)):
        parse_proposer_response(payload)


def test_parse_rationale_is_silently_dropped():
    """Rationale isn't in StrategySpec; parser shouldn't fail or store it."""
    payload = {
        "class": "X",
        "rationale": "something",
        "ranges": {"a": [1]},
        "max_loss_per_trade": 100.0,
    }
    spec = parse_proposer_response(payload)
    assert spec.class_name == "X"
