"""
Decision agent outputs.

A TradeProposal is the decision agent's decision to open a new position
(or explicitly pass). Proposals are never executed directly — they enter
the risk manager which applies portfolio-level constraints before an
Order is generated.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from schemas.common import (
    BaseSchema,
    ConvictionScore,
    Direction,
    StrategyFamily,
    Timeframe,
    utc_now,
)


class SourceSignalRef(BaseSchema):
    """
    Lightweight back-reference from a proposal to a research signal.

    We don't embed the full signal to keep proposals JSON-small; the
    signal_id is enough for the evolver to join back on the signals table.
    """

    signal_id: str = Field(..., min_length=1, max_length=64)
    timeframe: Timeframe
    direction: Direction
    conviction: ConvictionScore
    weight: float = Field(
        ...,
        ge=0,
        le=1,
        description="How much this signal contributed to the decision (0-1).",
    )


class RiskPlan(BaseSchema):
    """
    The complete risk profile for a trade, expressed before execution.

    Every field is deterministic given the proposal — the risk manager
    uses these to size the position and enforce portfolio caps.
    """

    entry_price: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    max_loss_usd: float = Field(..., gt=0, description="Dollars at risk if stop hits.")
    position_size: float = Field(..., gt=0, description="Shares / contracts.")
    risk_reward_ratio: float = Field(..., gt=0)
    atr_at_entry: float | None = Field(default=None, ge=0)
    stop_atr_mult: float | None = Field(default=None, ge=0)
    target_atr_mult: float | None = Field(default=None, ge=0)
    max_hold_bars: int | None = Field(
        default=None,
        ge=1,
        description="Optional time stop, in bars of the decision timeframe.",
    )

    @model_validator(mode="after")
    def _validate_stop_target_direction(self) -> "RiskPlan":
        # Not checking direction here because a short-biased RiskPlan has
        # stop > entry > target. We enforce that logic in TradeProposal
        # where we know the direction.
        if self.stop_loss == self.entry_price:
            raise ValueError("stop_loss must differ from entry_price")
        if self.take_profit == self.entry_price:
            raise ValueError("take_profit must differ from entry_price")
        return self


class TradeProposal(BaseSchema):
    """
    The decision agent's proposal to open a new position.

    The decision agent may also emit 'pass' proposals (direction=NEUTRAL,
    strategy=None) to explicitly log 'I looked and declined' — the
    performance analyzer uses these to track opportunity cost.
    """

    # Identity
    proposal_id: str = Field(..., min_length=1, max_length=64)
    run_id: str = Field(..., min_length=1, max_length=64)
    agent_version: str = Field(..., min_length=1, max_length=32)
    strategy_version: str = Field(..., min_length=1, max_length=32)
    prompt_hash: str = Field(..., min_length=8, max_length=128)

    # Subject
    ticker: str = Field(..., min_length=1, max_length=16)
    decision_ts: datetime = Field(
        ..., description="Bar timestamp at which the decision was made."
    )

    # Decision
    direction: Direction
    strategy_family: StrategyFamily | None = Field(
        default=None,
        description="None iff direction == NEUTRAL (an explicit pass).",
    )
    confluence_score: ConvictionScore
    risk_plan: RiskPlan | None = Field(
        default=None,
        description="None iff direction == NEUTRAL.",
    )
    rationale: str = Field(..., min_length=1, max_length=4000)

    # Context
    source_signals: list[SourceSignalRef] = Field(default_factory=list, max_length=20)
    regime_label: str | None = Field(default=None, max_length=32)

    # Bookkeeping
    created_at: datetime = Field(default_factory=utc_now)
    llm_latency_ms: int | None = Field(default=None, ge=0)
    llm_cost_usd: float | None = Field(default=None, ge=0)
    cache_hit: bool = Field(default=False)

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _validate_neutral_has_no_trade(self) -> "TradeProposal":
        if self.direction == Direction.NEUTRAL:
            if self.strategy_family is not None:
                raise ValueError("NEUTRAL proposals must have strategy_family=None")
            if self.risk_plan is not None:
                raise ValueError("NEUTRAL proposals must have risk_plan=None")
        else:
            if self.strategy_family is None:
                raise ValueError("Non-NEUTRAL proposals require strategy_family")
            if self.risk_plan is None:
                raise ValueError("Non-NEUTRAL proposals require risk_plan")
        return self

    @model_validator(mode="after")
    def _validate_risk_plan_direction(self) -> "TradeProposal":
        if self.direction == Direction.NEUTRAL or self.risk_plan is None:
            return self
        rp = self.risk_plan
        if self.direction == Direction.LONG:
            if not (rp.stop_loss < rp.entry_price < rp.take_profit):
                raise ValueError(
                    "LONG risk_plan requires stop_loss < entry_price < take_profit"
                )
        else:  # SHORT
            if not (rp.take_profit < rp.entry_price < rp.stop_loss):
                raise ValueError(
                    "SHORT risk_plan requires take_profit < entry_price < stop_loss"
                )
        return self
