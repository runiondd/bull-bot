"""
Pydantic schemas for Bull-Bot.

All LLM outputs, database rows, and IPC payloads flow through these models.
Changing a schema is a breaking change — bump the module version and treat it
as a strategy version bump (the evolver will notice and invalidate caches).

Organization:
- common      — enums, shared primitives, base model
- signals     — research agent outputs (one row per ticker × timeframe × bar)
- decisions   — decision agent outputs (trade proposals)
- trading     — orders, fills, positions, equity snapshots
- performance — performance analyzer outputs (rolled-up metrics)
- evolver     — strategy evolver proposals and approval records
- config      — strategy config snapshot (what the evolver mutates)
- backtest    — backtest run metadata and walk-forward results
- regime      — regime classification snapshots
"""

from schemas.common import (
    BaseSchema,
    Direction,
    StrategyFamily,
    Timeframe,
    AssetClass,
    ExecMode,
    RunStatus,
    OrderSide,
    OrderType,
    OrderStatus,
    PositionStatus,
    ConvictionScore,
    PriceLevel,
)
from schemas.signals import ResearchSignal, IndicatorSnapshot, KeyLevels
from schemas.decisions import TradeProposal, SourceSignalRef, RiskPlan
from schemas.trading import Order, Fill, Position, EquitySnapshot
from schemas.performance import (
    PerformanceReport,
    StrategyBreakdown,
    TickerBreakdown,
    RegimeBreakdown,
)
from schemas.evolver import EvolverProposal, ConfigDiff, ApprovalRecord
from schemas.config import StrategyConfig
from schemas.backtest import (
    BacktestRun,
    WalkForwardWindow,
    BacktestMetrics,
    FaithfulnessCheck,
)
from schemas.regime import RegimeSnapshot

__all__ = [
    # common
    "BaseSchema",
    "Direction",
    "StrategyFamily",
    "Timeframe",
    "AssetClass",
    "ExecMode",
    "RunStatus",
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "PositionStatus",
    "ConvictionScore",
    "PriceLevel",
    # signals
    "ResearchSignal",
    "IndicatorSnapshot",
    "KeyLevels",
    # decisions
    "TradeProposal",
    "SourceSignalRef",
    "RiskPlan",
    # trading
    "Order",
    "Fill",
    "Position",
    "EquitySnapshot",
    # performance
    "PerformanceReport",
    "StrategyBreakdown",
    "TickerBreakdown",
    "RegimeBreakdown",
    # evolver
    "EvolverProposal",
    "ConfigDiff",
    "ApprovalRecord",
    # config
    "StrategyConfig",
    # backtest
    "BacktestRun",
    "WalkForwardWindow",
    "BacktestMetrics",
    "FaithfulnessCheck",
    # regime
    "RegimeSnapshot",
]

SCHEMA_VERSION = "0.1.0"
