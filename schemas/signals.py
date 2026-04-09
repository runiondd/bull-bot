"""
Research agent outputs.

One ResearchSignal per (ticker, timeframe, bar_ts, agent_version).
Persisted in the `signals` table, partitioned by run_id so backtests
and live never collide.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from schemas.common import (
    BaseSchema,
    ConvictionScore,
    Direction,
    PriceLevel,
    StrategyFamily,
    Timeframe,
    utc_now,
)


class IndicatorSnapshot(BaseSchema):
    """
    The numeric indicator state the research agent observed.

    Stored alongside the signal so the evolver can correlate signal
    quality with feature values without rerunning the indicators.
    Free-form float dict is intentional — each agent version may track
    a different set, and the evolver treats missing keys as NaN.
    """

    rsi_14: float | None = Field(default=None, ge=0, le=100)
    atr_14: float | None = Field(default=None, ge=0)
    adx_14: float | None = Field(default=None, ge=0, le=100)
    ema_9: float | None = Field(default=None, ge=0)
    ema_21: float | None = Field(default=None, ge=0)
    ema_50: float | None = Field(default=None, ge=0)
    ema_200: float | None = Field(default=None, ge=0)
    vwap: float | None = Field(default=None, ge=0)
    volume_ratio: float | None = Field(default=None, ge=0)
    iv_rank: float | None = Field(default=None, ge=0, le=100)
    iv_percentile: float | None = Field(default=None, ge=0, le=100)
    extras: dict[str, float] = Field(
        default_factory=dict,
        description="Escape hatch for agent-specific features the evolver can ingest.",
    )


class KeyLevels(BaseSchema):
    """Support / resistance / pivot levels the agent identified."""

    supports: list[PriceLevel] = Field(default_factory=list, max_length=10)
    resistances: list[PriceLevel] = Field(default_factory=list, max_length=10)
    pivots: list[PriceLevel] = Field(default_factory=list, max_length=5)


class ResearchSignal(BaseSchema):
    """
    One research agent's opinion on one ticker at one bar.

    The decision agent consumes a collection of these across timeframes
    and produces a TradeProposal if conviction aligns.
    """

    # Identity
    signal_id: str = Field(..., min_length=1, max_length=64)
    run_id: str = Field(..., min_length=1, max_length=64)
    agent_name: str = Field(..., min_length=1, max_length=64)
    agent_version: str = Field(..., min_length=1, max_length=32)
    strategy_version: str = Field(..., min_length=1, max_length=32)
    prompt_hash: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="SHA of the rendered prompt. Used for LLM cache invalidation.",
    )

    # Subject
    ticker: str = Field(..., min_length=1, max_length=16)
    timeframe: Timeframe
    bar_ts: datetime = Field(..., description="UTC timestamp of the bar being analyzed.")

    # Opinion
    direction: Direction
    conviction: ConvictionScore
    rationale: str = Field(..., min_length=1, max_length=4000)
    preferred_strategies: list[StrategyFamily] = Field(
        default_factory=list,
        max_length=len(StrategyFamily),
        description="Ranked preference; index 0 is most preferred.",
    )

    # State
    indicators: IndicatorSnapshot = Field(default_factory=IndicatorSnapshot)
    key_levels: KeyLevels = Field(default_factory=KeyLevels)
    spot_price: float = Field(..., gt=0)

    # Bookkeeping
    created_at: datetime = Field(default_factory=utc_now)
    llm_latency_ms: int | None = Field(default=None, ge=0)
    llm_cost_usd: float | None = Field(default=None, ge=0)
    cache_hit: bool = Field(default=False)

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, v: str) -> str:
        return v.upper()

    @field_validator("preferred_strategies")
    @classmethod
    def _dedupe_strategies(cls, v: list[StrategyFamily]) -> list[StrategyFamily]:
        # Preserve order, drop dupes — LLMs repeat themselves.
        seen: set[StrategyFamily] = set()
        out: list[StrategyFamily] = []
        for s in v:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out
