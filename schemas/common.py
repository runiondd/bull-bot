"""
Shared enums, primitives, and the base schema class.

Everything else in schemas/ inherits from BaseSchema to get consistent
config (strict, forbid extras, ISO datetimes, frozen where appropriate).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


# ---------- Base model ----------


class BaseSchema(BaseModel):
    """
    Base for every Bull-Bot schema.

    - forbid: reject unknown fields so LLM hallucinations fail loudly
    - validate_assignment: catch mutations that would violate constraints
    - str_strip_whitespace: clean up LLM text outputs
    - use_enum_values=False: keep enum instances so downstream code can type-check
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        use_enum_values=False,
        populate_by_name=True,
    )


# ---------- Enums ----------


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class StrategyFamily(str, Enum):
    LONG_EQUITY = "long_equity"
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    PUT_CREDIT_SPREAD = "put_credit_spread"
    CALL_CREDIT_SPREAD = "call_credit_spread"
    COVERED_CALL = "covered_call"
    CASH_SECURED_PUT = "cash_secured_put"


class Timeframe(str, Enum):
    TF_15M = "15m"
    TF_1H = "1h"
    TF_4H = "4h"
    TF_1D = "1d"
    TF_1W = "1w"


class AssetClass(str, Enum):
    EQUITY = "equity"
    INDEX = "index"
    CRYPTO_ETF = "crypto_etf"
    COMMODITY_ETF = "commodity_etf"
    COMMODITY = "commodity"
    OPTION = "option"


class ExecMode(str, Enum):
    """
    Execution mode for the unified engine.

    - LIVE: today's bar, real LLM calls, writes to run_id='live'
    - BACKTEST_FULL: historical cursor, real LLM calls, writes to bt_<uuid>
    - BACKTEST_CHEAP: historical cursor, cache-only LLM lookups, fail if miss
    - BACKTEST_HYBRID: historical cursor, cache then LLM on miss
    """

    LIVE = "live"
    BACKTEST_FULL = "backtest_full"
    BACKTEST_CHEAP = "backtest_cheap"
    BACKTEST_HYBRID = "backtest_hybrid"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED_COST_CAP = "aborted_cost_cap"
    ABORTED_USER = "aborted_user"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"
    BUY_TO_OPEN = "buy_to_open"
    SELL_TO_OPEN = "sell_to_open"
    BUY_TO_CLOSE = "buy_to_close"
    SELL_TO_CLOSE = "sell_to_close"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PositionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    STOPPED = "stopped"
    TARGET_HIT = "target_hit"
    EXPIRED = "expired"


# ---------- Scalars ----------


ConvictionScore = Annotated[int, Field(ge=0, le=100, description="0-100 conviction")]
"""Standardized 0-100 conviction / confluence score."""


class PriceLevel(BaseSchema):
    """A named price level with optional context. Used for S/R, stops, targets."""

    label: str = Field(..., min_length=1, max_length=64)
    price: float = Field(..., gt=0)
    strength: int | None = Field(default=None, ge=0, le=100)
    source: str | None = Field(default=None, max_length=64)


# ---------- Helpers ----------


def utc_now() -> datetime:
    """Single source of truth for 'now' in schemas. Always UTC, always aware."""
    return datetime.now(timezone.utc)
