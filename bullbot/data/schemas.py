"""
Pydantic v2 models for every row type that crosses a module boundary.

No raw dicts escape the data layer — everything is validated into one of
these models first. Pydantic v2's `model_config` with `frozen=True` gives
us hashable, immutable value objects for free.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_FROZEN = ConfigDict(frozen=True, strict=True, extra="forbid")


class Bar(BaseModel):
    model_config = _FROZEN

    ticker: str
    timeframe: Literal["1d", "1h", "15m", "5m", "1m"]
    ts: int = Field(ge=0, description="UTC epoch seconds")
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    source: Literal["uw", "polygon", "yahoo"]

    @field_validator("ticker")
    @classmethod
    def _ticker_uppercase(cls, v: str) -> str:
        return v.upper()


class OptionContract(BaseModel):
    model_config = _FROZEN

    ticker: str
    expiry: str  # ISO YYYY-MM-DD
    strike: float = Field(gt=0)
    kind: Literal["C", "P"]
    ts: int = Field(ge=0)
    nbbo_bid: float = Field(ge=0)
    nbbo_ask: float = Field(ge=0)
    last: float | None = Field(default=None, ge=0)
    volume: int | None = Field(default=None, ge=0)
    open_interest: int | None = Field(default=None, ge=0)
    iv: float | None = Field(default=None, ge=0)


class IVSurfacePoint(BaseModel):
    model_config = _FROZEN

    ticker: str
    ts: int
    iv_rank: float
    iv_percentile: float
    atm_iv: float
    implied_move: float


class Greeks(BaseModel):
    model_config = _FROZEN

    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float


class Leg(BaseModel):
    model_config = _FROZEN

    option_symbol: str
    side: Literal["long", "short"]
    quantity: int = Field(gt=0)
    strike: float = Field(gt=0)
    expiry: str
    kind: Literal["C", "P"]


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    intent: Literal["open", "close"]
    strategy_class: str
    legs: list[Leg]
    max_loss_per_contract: float = Field(ge=0)
    rationale: str
    position_id_to_close: int | None = None   # set when intent='close'
    profit_target_pct: float | None = None
    stop_loss_mult: float | None = None
    min_dte_close: int | None = None
