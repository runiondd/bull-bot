"""
Walk-forward harness.

Anchored 70/30 walk-forward across a 24-month base window, stepping 30
days per fold, 3-5 folds total. See spec §6.2 and §6.6.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from bullbot import config
from bullbot.engine import step as engine_step
from bullbot.strategies.base import Strategy

log = logging.getLogger("bullbot.walkforward")


@dataclass
class Fold:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass
class FoldMetrics:
    pf_is: float
    pf_oos: float
    trade_count_is: int
    trade_count_oos: int
    max_dd_pct: float
    oos_pnls: list[float] = field(default_factory=list)


@dataclass
class BacktestMetrics:
    pf_is: float
    pf_oos: float
    sharpe_is: float
    max_dd_pct: float
    trade_count: int
    regime_breakdown: dict[str, float] = field(default_factory=dict)
    fold_metrics: list[FoldMetrics] = field(default_factory=list)
    cagr_oos: float | None = None
    sortino_oos: float | None = None


def compute_folds(
    total_days: int,
    train_frac: float,
    step_days: int,
    min_folds: int,
    max_folds: int,
) -> list[Fold]:
    if total_days <= 0:
        return []
    train_days_base = int(total_days * train_frac)
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    start_epoch = now_epoch - total_days * 86400

    folds: list[Fold] = []
    test_start_offset_days = train_days_base
    while test_start_offset_days + step_days <= total_days and len(folds) < max_folds:
        folds.append(
            Fold(
                train_start=start_epoch,
                train_end=start_epoch + test_start_offset_days * 86400,
                test_start=start_epoch + test_start_offset_days * 86400,
                test_end=start_epoch + (test_start_offset_days + step_days) * 86400,
            )
        )
        test_start_offset_days += step_days

    if len(folds) < min_folds and step_days > 7:
        return compute_folds(total_days, train_frac, max(step_days // 2, 7), min_folds, max_folds)

    return folds


def profit_factor(pnls: list[float]) -> float:
    """Profit factor = gross_win / gross_loss, capped at config.PF_CEILING.

    Returns 0.0 when there are no trades or only losing trades (gross_win == 0).
    When there are only winning trades (gross_loss == 0), returns config.PF_CEILING
    instead of IEEE +inf — the cap prevents sample-size artifacts in small OOS
    folds from poisoning downstream weighted averages.
    """
    from bullbot import config

    if not pnls:
        return 0.0
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss == 0:
        return 0.0 if gross_win == 0 else config.PF_CEILING
    return min(gross_win / gross_loss, config.PF_CEILING)


def max_drawdown_pct(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd


def aggregate(fold_metrics: list[FoldMetrics], category: str = "income") -> BacktestMetrics:
    if not fold_metrics:
        return BacktestMetrics(pf_is=0, pf_oos=0, sharpe_is=0, max_dd_pct=0, trade_count=0, fold_metrics=[])
    total_is = sum(f.trade_count_is for f in fold_metrics)
    total_oos = sum(f.trade_count_oos for f in fold_metrics)
    if total_is > 0:
        pf_is = sum(f.pf_is * f.trade_count_is for f in fold_metrics) / total_is
    else:
        pf_is = 0.0
    if total_oos > 0:
        pf_oos = sum(f.pf_oos * f.trade_count_oos for f in fold_metrics) / total_oos
    else:
        pf_oos = 0.0
    max_dd = max(f.max_dd_pct for f in fold_metrics)
    metrics = BacktestMetrics(
        pf_is=pf_is, pf_oos=pf_oos, sharpe_is=0.0, max_dd_pct=max_dd,
        trade_count=total_oos, fold_metrics=fold_metrics,
    )

    if category == "growth":
        from bullbot.features.indicators import cagr as calc_cagr, sortino as calc_sortino

        all_oos_pnls: list[float] = []
        for f in fold_metrics:
            all_oos_pnls.extend(f.oos_pnls)

        starting = 10000.0
        equity_curve = [starting]
        for pnl in all_oos_pnls:
            equity_curve.append(equity_curve[-1] + pnl)
        total_oos_days = len(all_oos_pnls) * 30  # approximate
        metrics.cagr_oos = calc_cagr(equity_curve, days=int(total_oos_days))
        returns = [pnl / max(eq, 1.0) for pnl, eq in zip(all_oos_pnls, equity_curve[:-1])]
        metrics.sortino_oos = calc_sortino(returns, risk_free_rate=config.RISK_FREE_RATE / 252)

    return metrics


def run_walkforward(
    conn: sqlite3.Connection,
    strategy: Strategy,
    strategy_id: int,
    ticker: str,
) -> BacktestMetrics:
    category = config.TICKER_CATEGORY.get(ticker, "income")
    if category == "growth":
        window_months = config.GROWTH_WF_WINDOW_MONTHS  # 60
        step_days = config.GROWTH_WF_STEP_DAYS  # 90
    else:
        window_months = config.WF_WINDOW_MONTHS  # 24
        step_days = config.WF_STEP_DAYS  # 30

    total_days = window_months * 30
    folds = compute_folds(
        total_days=total_days, train_frac=config.WF_TRAIN_FRAC,
        step_days=step_days, min_folds=config.WF_MIN_FOLDS,
        max_folds=config.WF_MAX_FOLDS,
    )
    fold_results: list[FoldMetrics] = []
    for fold in folds:
        is_pnls = _run_segment(conn, strategy, strategy_id, ticker,
                               fold.train_start, fold.train_end, tag=f"bt:is:{uuid.uuid4()}")
        oos_pnls = _run_segment(conn, strategy, strategy_id, ticker,
                                fold.test_start, fold.test_end, tag=f"bt:oos:{uuid.uuid4()}")
        fold_results.append(
            FoldMetrics(
                pf_is=profit_factor(is_pnls), pf_oos=profit_factor(oos_pnls),
                trade_count_is=len([p for p in is_pnls if p != 0]),
                trade_count_oos=len([p for p in oos_pnls if p != 0]),
                max_dd_pct=max_drawdown_pct(_cumulative(oos_pnls)),
                oos_pnls=oos_pnls,
            )
        )
    return aggregate(fold_results, category=category)


def _run_segment(conn, strategy, strategy_id, ticker, start, end, tag) -> list[float]:
    bars = conn.execute(
        "SELECT ts FROM bars WHERE ticker=? AND timeframe='1d' AND ts BETWEEN ? AND ? ORDER BY ts",
        (ticker, start, end),
    ).fetchall()
    for row in bars:
        engine_step.step(
            conn=conn, client=None, cursor=row["ts"],
            ticker=ticker, strategy=strategy,
            strategy_id=strategy_id, run_id=tag,
        )
    pnl_rows = conn.execute(
        "SELECT COALESCE(pnl_realized, 0) FROM orders WHERE run_id=? AND intent='close'",
        (tag,),
    ).fetchall()
    return [float(r[0]) for r in pnl_rows]


def _cumulative(pnls: list[float]) -> list[float]:
    curve = []
    total = 0.0
    for p in pnls:
        total += p
        curve.append(total)
    return curve
