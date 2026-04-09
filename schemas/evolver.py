"""
Strategy evolver proposals and approval records.

Flow:
1. Evolver analyzes a PerformanceReport for a parent_version.
2. It emits one or more EvolverProposals, each with a ConfigDiff.
3. The approval gate evaluates each proposal's holdout_metrics against
   the baseline. Each proposal gets an ApprovalRecord (auto or manual).
4. Accepted proposals are materialized into a new StrategyConfig and
   written to the strategy_configs table.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator

from schemas.common import BaseSchema, utc_now


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    AUTO_APPROVED = "auto_approved"
    AUTO_REJECTED = "auto_rejected"
    MANUAL_APPROVED = "manual_approved"
    MANUAL_REJECTED = "manual_rejected"
    SUPERSEDED = "superseded"


class DiffOp(str, Enum):
    SET = "set"
    ADD = "add"
    REMOVE = "remove"
    MULTIPLY = "multiply"


class ConfigDiff(BaseSchema):
    """
    A single atomic change to a StrategyConfig.

    path is a dotted JSON path, e.g.:
      'thresholds.min_confluence_score'
      'strategy_weights.put_credit_spread'
      'regime_adjustments.high_vix.min_confluence_score'
    """

    path: str = Field(..., min_length=1, max_length=256)
    op: DiffOp
    old_value: Any | None = None
    new_value: Any | None = None
    rationale: str = Field(..., min_length=1, max_length=2000)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        if not all(part for part in v.split(".")):
            raise ValueError("path segments cannot be empty")
        return v


class EvolverProposal(BaseSchema):
    """One proposed evolution of the strategy config."""

    proposal_id: str = Field(..., min_length=1, max_length=64)
    run_id: str = Field(..., min_length=1, max_length=64)
    parent_version: str = Field(..., min_length=1, max_length=32)
    proposed_version: str = Field(..., min_length=1, max_length=32)

    diffs: list[ConfigDiff] = Field(..., min_length=1, max_length=50)
    rationale: str = Field(..., min_length=1, max_length=4000)
    expected_impact: str | None = Field(default=None, max_length=2000)

    # Evidence — pointers to backtest artifacts that justify the change
    training_report_id: str | None = Field(default=None, max_length=64)
    holdout_report_id: str | None = Field(default=None, max_length=64)
    baseline_report_id: str | None = Field(default=None, max_length=64)

    # Approval gate metrics (filled in after walk-forward validation)
    holdout_sharpe_delta: float | None = None
    holdout_return_delta: float | None = None
    holdout_dd_ratio: float | None = Field(default=None, ge=0)
    holdout_trade_count: int | None = Field(default=None, ge=0)
    max_single_regime_share: float | None = Field(default=None, ge=0, le=1)

    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    agent_version: str = Field(..., min_length=1, max_length=32)
    llm_cost_usd: float | None = Field(default=None, ge=0)


class ApprovalRecord(BaseSchema):
    """
    Records the outcome of evaluating a proposal against the approval gate.

    Auto-rejects always produce a record so Dan can audit why an idea
    was killed. The soft_gate flag lets Dan override an auto-reject.
    """

    approval_id: str = Field(..., min_length=1, max_length=64)
    proposal_id: str = Field(..., min_length=1, max_length=64)
    decided_at: datetime = Field(default_factory=utc_now)
    decided_by: str = Field(..., min_length=1, max_length=32)
    decision: ApprovalStatus

    gate_reasons: list[str] = Field(
        default_factory=list,
        description="Human-readable reasons the gate triggered, e.g. "
        "'holdout_sharpe_delta=-0.18 < -0.10 threshold'.",
    )
    override_rationale: str | None = Field(default=None, max_length=2000)
