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


@dataclass(frozen=True)
class ClassifyResult:
    verdict: Verdict
    improved: bool
    new_plateau_counter: int
    new_best_pf_oos: float


def classify(state: _StateLike, metrics: _MetricsLike) -> ClassifyResult:
    """Decide the next action for a ticker given a fresh backtest result."""
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

    # inf > inf + 0.10 is False, so treat inf-vs-inf as not degraded
    both_inf = math.isinf(metrics.pf_oos) and math.isinf(state.best_pf_oos)
    improved = metrics.pf_oos > state.best_pf_oos + config.PLATEAU_IMPROVEMENT_MIN
    new_best = max(state.best_pf_oos, metrics.pf_oos)

    if improved or both_inf:
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
