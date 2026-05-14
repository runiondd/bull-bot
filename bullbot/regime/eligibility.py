"""Eligibility bandit — per-regime ranked menu for the LLM proposer.

For each strategy class, computes the posterior of `score_a` in the
given `(regime, class)` cell from historical proposals. Uses Thompson
sampling: sample from each cell's posterior, rank by sample, return
the top `n_exploit`. Cells with fewer than `MIN_OBS_FOR_EXPLOIT`
observations are "cold" — they're force-included with `status='explore'`
so the bandit learns about them.

Returns a menu the proposer can pick from:
- `n_exploit` classes ranked by their posterior sample (the "best bets")
- `n_explore` underexplored classes (the "unknowns to learn about")
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

import numpy as np


MIN_OBS_FOR_EXPLOIT = 5
COLD_PRIOR_MEAN = 0.0
COLD_PRIOR_VARIANCE = 1.0
HALF_LIFE_DAYS = 180


@dataclass(frozen=True)
class MenuEntry:
    class_name: str
    status: str  # "exploit" or "explore"
    posterior_mean: float
    posterior_n: float


@dataclass(frozen=True)
class _CellStats:
    n: float
    mean: float
    std: float


def _cell_stats(conn: sqlite3.Connection, regime_label: str, class_name: str) -> _CellStats:
    """Weighted stats with exponential decay (half-life=HALF_LIFE_DAYS).

    Each observation is weighted by 0.5 ** (age_days / HALF_LIFE_DAYS) so
    stale observations fade naturally. n_effective is the sum of weights —
    cells whose observations are all old fall below MIN_OBS_FOR_EXPLOIT and
    revert to cold-start (explore) mode.
    """
    now_ts = int(time.time())
    rows = conn.execute(
        "SELECT ep.score_a, ep.created_at "
        "FROM evolver_proposals ep "
        "JOIN strategies s ON s.id = ep.strategy_id "
        "WHERE ep.regime_label = ? "
        "  AND s.class_name = ? "
        "  AND ep.score_a IS NOT NULL",
        (regime_label, class_name),
    ).fetchall()
    if not rows:
        return _CellStats(n=0.0, mean=0.0, std=0.0)
    weights = []
    scores = []
    for score_a, created_at in rows:
        age_days = (now_ts - created_at) / 86400.0
        weights.append(0.5 ** (age_days / HALF_LIFE_DAYS))
        scores.append(score_a)
    total_w = sum(weights)
    if total_w == 0:
        return _CellStats(n=0.0, mean=0.0, std=0.0)
    weighted_mean = sum(w * s for w, s in zip(weights, scores)) / total_w
    weighted_var = sum(w * (s - weighted_mean) ** 2 for w, s in zip(weights, scores)) / total_w
    weighted_std = weighted_var ** 0.5
    return _CellStats(n=total_w, mean=weighted_mean, std=weighted_std)


def menu_for(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    regime_label: str,
    all_classes: list[str],
    n_exploit: int = 3,
    n_explore: int = 1,
) -> list[MenuEntry]:
    """Return up to `n_exploit + n_explore` menu entries for the proposer.

    `ticker` is currently unused — the bandit aggregates across tickers
    within a regime. Future enhancement: per-ticker bandit. Kept in the
    signature for API stability.
    """
    exploit_pool: list[MenuEntry] = []
    explore_pool: list[MenuEntry] = []
    for cls in all_classes:
        stats = _cell_stats(conn, regime_label, cls)
        if stats.n < MIN_OBS_FOR_EXPLOIT:
            explore_pool.append(MenuEntry(
                class_name=cls,
                status="explore",
                posterior_mean=stats.mean,
                posterior_n=stats.n,
            ))
        else:
            # Thompson sample from Normal(mean, std/sqrt(n)) — posterior of
            # the mean under a weakly-informative prior.
            sample = float(np.random.normal(stats.mean, stats.std / np.sqrt(stats.n)))
            exploit_pool.append(MenuEntry(
                class_name=cls,
                status="exploit",
                posterior_mean=sample,
                posterior_n=stats.n,
            ))

    # Top `n_exploit` by sampled posterior mean
    exploits = sorted(exploit_pool, key=lambda e: -e.posterior_mean)[:n_exploit]
    # Pick `n_explore` from the underexplored — prefer those with fewest obs
    explores = sorted(explore_pool, key=lambda e: e.posterior_n)[:n_explore]
    return exploits + explores
