"""
Plateau / edge-gate classifier. Pure function — no I/O.

Called inside evolver_iteration after backtest metrics are computed to
decide whether to (a) continue iterating, (b) mark the ticker as no_edge,
or (c) mark it as edge_found (promote to paper_trial).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol

from bullbot import config


Verdict = Literal["continue", "no_edge", "edge_found"]


class _StateLike(Protocol):
    iteration_count: int
    plateau_counter: int
    best_pf_oos: float


class _MetricsLike(Protocol):
    pf_is: float
    pf_oos: float
    trade_count: int
    # Growth metrics (None for income strategies)
    cagr_oos: float | None
    sortino_oos: float | None
    max_dd_pct: float


@dataclass(frozen=True)
class ClassifyResult:
    verdict: Verdict
    improved: bool
    new_plateau_counter: int
    new_best_pf_oos: float


def classify(state: _StateLike, metrics: _MetricsLike, category: str = "income") -> ClassifyResult:
    """Decide the next action for a ticker given a fresh backtest result."""
    if category == "growth":
        return _classify_growth(state, metrics)

    passed_gate = (
        metrics.pf_is >= config.EDGE_PF_IS_MIN
        and metrics.pf_oos >= config.EDGE_PF_OOS_MIN
        and metrics.trade_count >= config.EDGE_TRADE_COUNT_MIN
    )

    if passed_gate:
        return ClassifyResult(
            verdict="edge_found",
            improved=metrics.pf_oos > state.best_pf_oos,
            new_plateau_counter=0,
            new_best_pf_oos=max(state.best_pf_oos, metrics.pf_oos),
        )

    # Treat "both at ceiling" (or both +inf, defensively) as not degraded —
    # the cap obscures whether the candidate is better than the incumbent,
    # so we shouldn't count it as a plateau.
    both_inf = math.isinf(metrics.pf_oos) and math.isinf(state.best_pf_oos)
    both_at_ceiling = (
        metrics.pf_oos >= config.PF_CEILING
        and state.best_pf_oos >= config.PF_CEILING
    )
    improved = metrics.pf_oos > state.best_pf_oos + config.PLATEAU_IMPROVEMENT_MIN
    new_best = max(state.best_pf_oos, metrics.pf_oos)

    if improved or both_inf or both_at_ceiling:
        new_plateau = 0
    else:
        new_plateau = state.plateau_counter + 1

    # Safety cap
    if state.iteration_count + 1 >= config.ITERATION_CAP:
        return ClassifyResult(
            verdict="no_edge",
            improved=improved,
            new_plateau_counter=new_plateau,
            new_best_pf_oos=new_best,
        )

    if new_plateau >= config.PLATEAU_COUNTER_MAX:
        return ClassifyResult(
            verdict="no_edge",
            improved=improved,
            new_plateau_counter=new_plateau,
            new_best_pf_oos=new_best,
        )

    return ClassifyResult(
        verdict="continue",
        improved=improved,
        new_plateau_counter=new_plateau,
        new_best_pf_oos=new_best,
    )


def _classify_growth(state: _StateLike, metrics: _MetricsLike) -> ClassifyResult:
    """Growth gate: CAGR, Sortino, max drawdown, trade count."""
    cagr_val = metrics.cagr_oos if metrics.cagr_oos is not None else 0.0
    sortino_val = metrics.sortino_oos if metrics.sortino_oos is not None else 0.0
    dd = metrics.max_dd_pct

    passed_gate = (
        cagr_val >= config.GROWTH_EDGE_CAGR_MIN
        and sortino_val >= config.GROWTH_EDGE_SORTINO_MIN
        and dd <= config.GROWTH_EDGE_MAX_DD_PCT
        and metrics.trade_count >= config.GROWTH_EDGE_TRADE_COUNT_MIN
    )

    if passed_gate:
        return ClassifyResult(
            verdict="edge_found",
            improved=cagr_val > state.best_pf_oos + config.PLATEAU_IMPROVEMENT_MIN,
            new_plateau_counter=0,
            new_best_pf_oos=max(state.best_pf_oos, cagr_val),
        )

    # Plateau detection uses CAGR as the improvement metric for growth
    improved = cagr_val > state.best_pf_oos + config.PLATEAU_IMPROVEMENT_MIN
    new_best = max(state.best_pf_oos, cagr_val)

    if improved:
        new_plateau = 0
    else:
        new_plateau = state.plateau_counter + 1

    if state.iteration_count + 1 >= config.ITERATION_CAP:
        return ClassifyResult(
            verdict="no_edge",
            improved=improved,
            new_plateau_counter=new_plateau,
            new_best_pf_oos=new_best,
        )

    if new_plateau >= config.PLATEAU_COUNTER_MAX:
        return ClassifyResult(
            verdict="no_edge",
            improved=improved,
            new_plateau_counter=new_plateau,
            new_best_pf_oos=new_best,
        )

    return ClassifyResult(
        verdict="continue",
        improved=improved,
        new_plateau_counter=new_plateau,
        new_best_pf_oos=new_best,
    )
