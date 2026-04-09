"""
Performance analyzer outputs.

Rolled-up stats over a given (run_id, period). The evolver consumes
these breakdowns directly — the more dimensions we slice on, the
better the evolver can diagnose why a strategy did well or poorly.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from schemas.common import BaseSchema, StrategyFamily, utc_now


class _Breakdown(BaseSchema):
    """Shared fields for any per-dimension breakdown."""

    trades: int = Field(..., ge=0)
    wins: int = Field(..., ge=0)
    losses: int = Field(..., ge=0)
    total_pnl: float
    total_fees: float = Field(default=0, ge=0)
    win_rate: float = Field(..., ge=0, le=1)
    avg_win: float = Field(default=0)
    avg_loss: float = Field(default=0)
    profit_factor: float | None = Field(default=None, ge=0)
    sharpe: float | None = None
    max_dd_pct: float | None = Field(default=None, le=0)

    @model_validator(mode="after")
    def _check_counts(self) -> "_Breakdown":
        if self.wins + self.losses > self.trades:
            raise ValueError(
                f"wins({self.wins}) + losses({self.losses}) > trades({self.trades})"
            )
        return self


class StrategyBreakdown(_Breakdown):
    """Per strategy_family roll-up."""

    strategy_family: StrategyFamily


class TickerBreakdown(_Breakdown):
    """Per-ticker roll-up."""

    ticker: str = Field(..., min_length=1, max_length=16)


class RegimeBreakdown(_Breakdown):
    """
    Per-regime roll-up. Lets the evolver ask: 'does this strategy
    edge only appear in low-vol uptrends?'
    """

    regime_label: str = Field(..., min_length=1, max_length=64)


class PerformanceReport(BaseSchema):
    """
    Headline performance report for a run over a period.

    Used by both the live performance analyzer (nightly) and the
    backtest aggregator (per walk-forward window).
    """

    # Identity
    report_id: str = Field(..., min_length=1, max_length=64)
    run_id: str = Field(..., min_length=1, max_length=64)
    strategy_version: str = Field(..., min_length=1, max_length=32)

    # Period
    period_start: datetime
    period_end: datetime
    bars_observed: int = Field(..., ge=0)
    trading_days: int = Field(..., ge=0)

    # Headline stats
    starting_equity: float = Field(..., gt=0)
    ending_equity: float = Field(..., ge=0)
    total_return_pct: float
    cagr: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    calmar: float | None = None
    max_dd_pct: float = Field(..., le=0)
    max_dd_duration_days: int | None = Field(default=None, ge=0)

    # Trade-level stats
    total_trades: int = Field(..., ge=0)
    winning_trades: int = Field(..., ge=0)
    losing_trades: int = Field(..., ge=0)
    breakeven_trades: int = Field(default=0, ge=0)
    win_rate: float = Field(..., ge=0, le=1)
    avg_win: float = 0
    avg_loss: float = 0
    profit_factor: float | None = Field(default=None, ge=0)
    expectancy: float | None = None
    avg_hold_bars: float | None = Field(default=None, ge=0)

    # Cost stats
    total_llm_cost_usd: float = Field(default=0, ge=0)
    total_commissions: float = Field(default=0, ge=0)
    total_slippage_usd: float = Field(default=0, ge=0)

    # Breakdowns
    by_strategy: list[StrategyBreakdown] = Field(default_factory=list)
    by_ticker: list[TickerBreakdown] = Field(default_factory=list)
    by_regime: list[RegimeBreakdown] = Field(default_factory=list)

    # Bookkeeping
    created_at: datetime = Field(default_factory=utc_now)
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _check_period(self) -> "PerformanceReport":
        if self.period_end < self.period_start:
            raise ValueError("period_end must be >= period_start")
        if self.winning_trades + self.losing_trades + self.breakeven_trades > self.total_trades:
            raise ValueError("win + loss + breakeven counts cannot exceed total_trades")
        return self
