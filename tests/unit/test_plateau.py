"""
Plateau classifier tests — the state-machine function that decides whether
an iteration outcome means 'continue', 'no_edge', or 'edge_found'.
"""
from dataclasses import dataclass

import pytest

from bullbot.evolver import plateau


@dataclass
class FakeState:
    iteration_count: int = 0
    plateau_counter: int = 0
    best_pf_oos: float = 0.0


@dataclass
class FakeMetrics:
    pf_is: float
    pf_oos: float
    trade_count: int = 40
    cagr_oos: float | None = None
    sortino_oos: float | None = None
    max_dd_pct: float = 0.0


def test_edge_found_when_all_gates_pass():
    state = FakeState(iteration_count=5, plateau_counter=1, best_pf_oos=1.1)
    metrics = FakeMetrics(pf_is=1.55, pf_oos=1.35, trade_count=35)
    result = plateau.classify(state, metrics)
    assert result.verdict == "edge_found"


def test_not_edge_found_when_trade_count_too_low():
    state = FakeState()
    metrics = FakeMetrics(pf_is=1.55, pf_oos=1.35, trade_count=9)
    result = plateau.classify(state, metrics)
    assert result.verdict != "edge_found"


def test_no_edge_when_plateau_counter_maxes_out():
    state = FakeState(iteration_count=16, plateau_counter=2, best_pf_oos=1.00)
    metrics = FakeMetrics(pf_is=1.20, pf_oos=1.05, trade_count=35)
    result = plateau.classify(state, metrics)
    assert result.verdict == "no_edge"
    assert result.new_plateau_counter == 3


def test_no_edge_when_iteration_cap_hit():
    state = FakeState(iteration_count=50, plateau_counter=0, best_pf_oos=0.8)
    metrics = FakeMetrics(pf_is=1.00, pf_oos=0.90, trade_count=40)
    result = plateau.classify(state, metrics)
    assert result.verdict == "no_edge"


def test_continue_on_small_improvement_resets_plateau():
    state = FakeState(iteration_count=8, plateau_counter=2, best_pf_oos=1.00)
    metrics = FakeMetrics(pf_is=1.20, pf_oos=1.15, trade_count=40)
    result = plateau.classify(state, metrics)
    assert result.verdict == "continue"
    assert result.new_plateau_counter == 0
    assert result.improved is True


def test_continue_on_insufficient_improvement_increments_plateau():
    state = FakeState(iteration_count=8, plateau_counter=1, best_pf_oos=1.00)
    metrics = FakeMetrics(pf_is=1.20, pf_oos=1.05, trade_count=40)
    result = plateau.classify(state, metrics)
    assert result.verdict == "continue"
    assert result.new_plateau_counter == 2


def test_improved_means_new_best_pf():
    state = FakeState(iteration_count=1, plateau_counter=0, best_pf_oos=0.8)
    metrics = FakeMetrics(pf_is=1.10, pf_oos=1.05, trade_count=40)
    result = plateau.classify(state, metrics)
    assert result.improved is True
    assert result.new_best_pf_oos == 1.05


def test_first_iteration_never_triggers_no_edge_on_iteration_cap():
    state = FakeState(iteration_count=0)
    metrics = FakeMetrics(pf_is=1.0, pf_oos=0.9, trade_count=40)
    result = plateau.classify(state, metrics)
    assert result.verdict == "continue"


def test_inf_pf_oos_does_not_increment_plateau():
    """When both current and best pf_oos are inf, plateau counter resets."""
    state = FakeState(iteration_count=5, plateau_counter=2, best_pf_oos=float("inf"))
    metrics = FakeMetrics(pf_is=1.20, pf_oos=float("inf"), trade_count=5)
    result = plateau.classify(state, metrics)
    assert result.verdict == "continue"
    assert result.new_plateau_counter == 0


def test_inf_pf_oos_still_blocked_by_trade_count_for_edge():
    """inf pf_oos doesn't bypass the trade count gate for edge_found."""
    state = FakeState(iteration_count=5, plateau_counter=0, best_pf_oos=float("inf"))
    metrics = FakeMetrics(pf_is=float("inf"), pf_oos=float("inf"), trade_count=5)
    result = plateau.classify(state, metrics)
    assert result.verdict != "edge_found"


def test_inf_pf_oos_passes_gate_with_enough_trades():
    """inf pf_oos passes edge gate when trade count is sufficient."""
    state = FakeState(iteration_count=5, plateau_counter=2, best_pf_oos=float("inf"))
    metrics = FakeMetrics(pf_is=float("inf"), pf_oos=float("inf"), trade_count=15)
    result = plateau.classify(state, metrics)
    assert result.verdict == "edge_found"


# ---------- Growth gate tests ----------


@dataclass
class FakeGrowthMetrics:
    pf_is: float = 0.0
    pf_oos: float = 0.0
    trade_count: int = 10
    cagr_oos: float | None = 0.25
    sortino_oos: float | None = 1.5
    max_dd_pct: float = 0.20


def test_growth_edge_found_when_all_gates_pass():
    state = FakeState(iteration_count=3, plateau_counter=1, best_pf_oos=0.10)
    metrics = FakeGrowthMetrics(cagr_oos=0.25, sortino_oos=1.5, max_dd_pct=0.20, trade_count=8)
    result = plateau.classify(state, metrics, category="growth")
    assert result.verdict == "edge_found"


def test_growth_no_edge_low_cagr():
    state = FakeState(iteration_count=3, plateau_counter=2, best_pf_oos=0.15)
    metrics = FakeGrowthMetrics(cagr_oos=0.10, sortino_oos=1.5, max_dd_pct=0.20, trade_count=8)
    result = plateau.classify(state, metrics, category="growth")
    assert result.verdict != "edge_found"


def test_growth_no_edge_high_drawdown():
    state = FakeState(iteration_count=3, plateau_counter=2, best_pf_oos=0.15)
    metrics = FakeGrowthMetrics(cagr_oos=0.30, sortino_oos=2.0, max_dd_pct=0.40, trade_count=8)
    result = plateau.classify(state, metrics, category="growth")
    assert result.verdict != "edge_found"


def test_growth_uses_cagr_for_plateau_tracking():
    state = FakeState(iteration_count=3, plateau_counter=0, best_pf_oos=0.15)
    metrics = FakeGrowthMetrics(cagr_oos=0.30, sortino_oos=0.5, trade_count=3)
    result = plateau.classify(state, metrics, category="growth")
    assert result.new_best_pf_oos == 0.30
    assert result.improved is True
