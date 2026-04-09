"""
Strategy configuration snapshot.

This is the mutable policy the evolver changes. Each snapshot is an
immutable, content-addressed version — the evolver proposes diffs,
the approval gate evaluates them, and if accepted a new StrategyConfig
is written with a new version hash.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from schemas.common import BaseSchema, StrategyFamily, Timeframe, utc_now


class StrategyConfig(BaseSchema):
    """
    Versioned strategy policy.

    Stored in `strategy_configs` table keyed by version. The active
    config is referenced by a single row in `strategy_active`.
    """

    # Identity
    version: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Content hash of (parent_version + diff) — immutable once written.",
    )
    parent_version: str | None = Field(default=None, max_length=32)
    created_at: datetime = Field(default_factory=utc_now)
    created_by: str = Field(default="evolver", max_length=32)

    # Prompts (hashed, not stored inline — prompt files live under /prompts)
    prompt_hashes: dict[str, str] = Field(
        default_factory=dict,
        description="Map of prompt_name -> SHA of rendered template. "
        "Change in any hash invalidates LLM cache.",
    )

    # Global thresholds
    thresholds: dict[str, float] = Field(
        default_factory=lambda: {
            "min_confluence_score": 60.0,
            "min_risk_reward": 1.8,
            "max_per_trade_risk_pct": 0.015,
            "default_stop_atr_mult": 1.5,
            "default_target_atr_mult": 3.0,
        }
    )

    # Per-timeframe overrides
    timeframe_weights: dict[Timeframe, float] = Field(
        default_factory=lambda: {
            Timeframe.TF_15M: 0.10,
            Timeframe.TF_1H: 0.15,
            Timeframe.TF_4H: 0.25,
            Timeframe.TF_1D: 0.30,
            Timeframe.TF_1W: 0.20,
        },
        description="How much each timeframe's signal contributes to the final confluence score.",
    )

    # Strategy family enablement + weights
    strategy_weights: dict[StrategyFamily, float] = Field(
        default_factory=lambda: {s: 1.0 for s in StrategyFamily},
        description="0.0 disables the strategy family for this version.",
    )

    # Regime-specific adjustments
    regime_adjustments: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="regime_label -> {threshold_name: multiplier}. "
        "E.g. high-vix regime might bump min_confluence_score by 1.2x.",
    )

    # Human-readable notes (what changed + why)
    change_rationale: str | None = Field(default=None, max_length=4000)

    @field_validator("timeframe_weights")
    @classmethod
    def _weights_sum_to_one(
        cls, v: dict[Timeframe, float]
    ) -> dict[Timeframe, float]:
        if not v:
            return v
        total = sum(v.values())
        if not 0.99 <= total <= 1.01:
            raise ValueError(
                f"timeframe_weights must sum to 1.0 (got {total:.4f})"
            )
        return v

    @field_validator("strategy_weights")
    @classmethod
    def _strategy_weights_nonnegative(
        cls, v: dict[StrategyFamily, float]
    ) -> dict[StrategyFamily, float]:
        for family, weight in v.items():
            if weight < 0:
                raise ValueError(f"strategy_weights[{family}] must be >= 0")
        return v
