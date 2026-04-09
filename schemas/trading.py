"""
Execution-side schemas: orders, fills, positions, equity.

These mirror the SQLite tables in the data layer. Every row is tagged
with run_id so live and backtests never share state.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from schemas.common import (
    BaseSchema,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionStatus,
    StrategyFamily,
    utc_now,
)


class Order(BaseSchema):
    """A request to the simulated broker. Lives in the `orders` table."""

    order_id: str = Field(..., min_length=1, max_length=64)
    run_id: str = Field(..., min_length=1, max_length=64)
    proposal_id: str | None = Field(default=None, max_length=64)
    position_id: str | None = Field(
        default=None,
        max_length=64,
        description="Set on exit orders so they can be reconciled.",
    )

    ticker: str = Field(..., min_length=1, max_length=16)
    side: OrderSide
    order_type: OrderType
    quantity: float = Field(..., gt=0)
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)

    status: OrderStatus = OrderStatus.PENDING
    submitted_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _validate_prices(self) -> "Order":
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("LIMIT orders require limit_price")
        if self.order_type == OrderType.STOP and self.stop_price is None:
            raise ValueError("STOP orders require stop_price")
        if self.order_type == OrderType.STOP_LIMIT and (
            self.limit_price is None or self.stop_price is None
        ):
            raise ValueError("STOP_LIMIT orders require both limit_price and stop_price")
        return self


class Fill(BaseSchema):
    """A fill against an order. Multiple fills per order are allowed (partials)."""

    fill_id: str = Field(..., min_length=1, max_length=64)
    order_id: str = Field(..., min_length=1, max_length=64)
    run_id: str = Field(..., min_length=1, max_length=64)

    fill_price: float = Field(..., gt=0)
    fill_quantity: float = Field(..., gt=0)
    commission: float = Field(default=0, ge=0)
    slippage_bps: float | None = Field(
        default=None,
        description="Observed slippage in basis points vs. the reference price.",
    )

    filled_at: datetime
    bar_ts: datetime = Field(
        ...,
        description="Timestamp of the bar on which this fill was simulated (backtest) "
        "or observed (live).",
    )


class Position(BaseSchema):
    """An open or closed position. Lives in the `positions` table."""

    position_id: str = Field(..., min_length=1, max_length=64)
    run_id: str = Field(..., min_length=1, max_length=64)
    proposal_id: str = Field(..., min_length=1, max_length=64)

    ticker: str = Field(..., min_length=1, max_length=16)
    strategy_family: StrategyFamily
    strategy_version: str = Field(..., min_length=1, max_length=32)

    quantity: float = Field(..., description="Negative = short stock position.")
    entry_price: float = Field(..., gt=0)
    entry_ts: datetime
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)

    exit_price: float | None = Field(default=None, gt=0)
    exit_ts: datetime | None = None
    exit_reason: str | None = Field(default=None, max_length=64)

    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    max_adverse_excursion: float | None = None
    max_favorable_excursion: float | None = None

    status: PositionStatus = PositionStatus.OPEN
    regime_label_at_entry: str | None = Field(default=None, max_length=32)

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _validate_closed(self) -> "Position":
        if self.status != PositionStatus.OPEN:
            if self.exit_price is None or self.exit_ts is None:
                raise ValueError(
                    f"Position with status={self.status.value} requires exit_price and exit_ts"
                )
        return self


class EquitySnapshot(BaseSchema):
    """
    Point-in-time view of the paper account. Written at close of each
    engine.step() call so we get a clean daily P&L curve.
    """

    snapshot_id: str = Field(..., min_length=1, max_length=64)
    run_id: str = Field(..., min_length=1, max_length=64)
    snapshot_ts: datetime

    total_equity: float
    cash: float
    margin_used: float = Field(default=0, ge=0)
    gross_exposure: float = Field(default=0, ge=0)
    net_exposure: float = 0
    open_positions: int = Field(default=0, ge=0)

    realized_pnl_today: float = 0
    unrealized_pnl: float = 0
    cumulative_return_pct: float | None = None
    drawdown_pct: float | None = Field(default=None, le=0)
