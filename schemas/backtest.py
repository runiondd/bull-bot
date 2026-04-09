"""
Backtest run metadata and walk-forward results.

The backtest engine is the heart of Bull-Bot's self-improvement loop.
Every run, every window, every metric gets persisted so the evolver
can diff versions and the faithfulness check can compare live vs.
replayed backtest on the same bars.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from schemas.common import BaseSchema, ExecMode, RunStatus, Timeframe, utc_now


class BacktestMetrics(BaseSchema):
    """
    Compact metrics summary for a backtest slice (full run or single window).

    This is the artifact the approval gate reads when deciding whether
    to promote a strategy version.
    """

    total_return_pct: float
    cagr: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    calmar: float | None = None
    max_dd_pct: float = Field(..., le=0)
    win_rate: float = Field(..., ge=0, le=1)
    profit_factor: float | None = Field(default=None, ge=0)
    expectancy: float | None = None

    total_trades: int = Field(..., ge=0)
    avg_hold_bars: float | None = Field(default=None, ge=0)

    # Regime concentration — what share of total P&L came from the
    # single highest-P&L regime. High values (>0.65) signal overfit.
    max_single_regime_share: float | None = Field(default=None, ge=0, le=1)

    # Cost / efficiency
    total_llm_cost_usd: float = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    cache_hit_rate: float | None = Field(default=None, ge=0, le=1)


class WalkForwardWindow(BaseSchema):
    """
    One train/holdout window from a walk-forward run.

    train_metrics and holdout_metrics on the same window are the
    core input to the approval gate's overfit detection.
    """

    window_id: str = Field(..., min_length=1, max_length=64)
    run_id: str = Field(..., min_length=1, max_length=64)
    scope: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="'weekly' | 'daily' | 'intraday' — maps to WALK_FORWARD_WINDOWS config.",
    )
    strategy_version: str = Field(..., min_length=1, max_length=32)

    train_start: datetime
    train_end: datetime
    holdout_start: datetime
    holdout_end: datetime

    train_metrics: BacktestMetrics
    holdout_metrics: BacktestMetrics

    regime_distribution: dict[str, float] = Field(
        default_factory=dict,
        description="regime_label -> fraction of holdout bars spent in that regime.",
    )

    passed_gate: bool | None = None
    gate_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_windows(self) -> "WalkForwardWindow":
        if self.train_end < self.train_start:
            raise ValueError("train_end must be >= train_start")
        if self.holdout_end < self.holdout_start:
            raise ValueError("holdout_end must be >= holdout_start")
        if self.holdout_start < self.train_end:
            raise ValueError(
                "holdout_start must be >= train_end (no leakage allowed)"
            )
        return self


class BacktestRun(BaseSchema):
    """
    Metadata for a single backtest execution.

    The run_id is the join key used across all tables — every signal,
    proposal, order, fill, position, and equity snapshot this backtest
    produces carries this run_id.
    """

    run_id: str = Field(..., min_length=1, max_length=64)
    mode: ExecMode
    label: str | None = Field(default=None, max_length=128)
    parent_run_id: str | None = Field(default=None, max_length=64)

    strategy_version: str = Field(..., min_length=1, max_length=32)
    timeframes: list[Timeframe] = Field(..., min_length=1)
    tickers: list[str] = Field(..., min_length=1)

    backtest_start: datetime
    backtest_end: datetime
    initial_capital: float = Field(..., gt=0)

    status: RunStatus = RunStatus.PENDING
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None

    # Cost guard-rails
    cost_cap_usd: float = Field(..., gt=0)
    cost_spent_usd: float = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    cache_misses: int = Field(default=0, ge=0)

    # Checkpoint state for resume
    last_checkpoint_ts: datetime | None = None
    checkpoint_cursor: datetime | None = Field(
        default=None,
        description="Simulated 'now' of the last successfully processed bar.",
    )

    # Headline results (filled in when status=COMPLETED)
    headline_metrics: BacktestMetrics | None = None
    error_message: str | None = Field(default=None, max_length=4000)

    @field_validator("tickers")
    @classmethod
    def _upper_tickers(cls, v: list[str]) -> list[str]:
        return [t.upper() for t in v]

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, v: str) -> str:
        if v != "live" and not v.startswith("bt_"):
            raise ValueError("run_id must be 'live' or start with 'bt_'")
        return v

    @model_validator(mode="after")
    def _validate_live_mode(self) -> "BacktestRun":
        if self.mode == ExecMode.LIVE and self.run_id != "live":
            raise ValueError("LIVE mode requires run_id='live'")
        if self.mode != ExecMode.LIVE and self.run_id == "live":
            raise ValueError("Non-LIVE mode must not use run_id='live'")
        return self


class FaithfulnessCheck(BaseSchema):
    """
    Nightly comparison of live vs. cheap-replay backtest over the
    same bars. Any significant divergence means the engine is drifting
    and live/backtest parity is broken.
    """

    check_id: str = Field(..., min_length=1, max_length=64)
    checked_at: datetime = Field(default_factory=utc_now)
    period_start: datetime
    period_end: datetime

    live_run_id: str = Field(default="live")
    replay_run_id: str = Field(..., min_length=1, max_length=64)

    live_equity_end: float
    replay_equity_end: float
    equity_delta_pct: float

    live_trade_count: int = Field(..., ge=0)
    replay_trade_count: int = Field(..., ge=0)
    trade_count_delta: int

    signal_mismatch_count: int = Field(default=0, ge=0)
    proposal_mismatch_count: int = Field(default=0, ge=0)

    passed: bool
    notes: str | None = Field(default=None, max_length=4000)
