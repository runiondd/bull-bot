"""
Shared pytest fixtures for Bull-Bot.

Anything that multiple test files need goes here: sample schemas,
frozen timestamps, temp SQLite dbs, fake LLM clients, etc.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Make the repo root importable so tests can do `from schemas import ...`
# without a package install.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Ensure .env values don't leak into tests
os.environ.setdefault("BULLBOT_LOG_LEVEL", "WARNING")

from schemas import (  # noqa: E402
    Direction,
    ExecMode,
    IndicatorSnapshot,
    KeyLevels,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionStatus,
    PriceLevel,
    ResearchSignal,
    RiskPlan,
    SourceSignalRef,
    StrategyFamily,
    Timeframe,
    TradeProposal,
)


# ---------- Time fixtures ----------


@pytest.fixture
def frozen_now() -> datetime:
    """A deterministic UTC timestamp for any test that needs 'now'."""
    return datetime(2026, 4, 1, 15, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def frozen_bar_ts() -> datetime:
    """Bar close timestamp used in sample signals / proposals."""
    return datetime(2026, 4, 1, 15, 0, 0, tzinfo=timezone.utc)


# ---------- ID helpers ----------


@pytest.fixture
def new_id():
    """Call `new_id()` inside a test to get a fresh short UUID string."""

    def _make(prefix: str = "") -> str:
        base = uuid.uuid4().hex[:12]
        return f"{prefix}{base}" if prefix else base

    return _make


# ---------- Sample schemas ----------


@pytest.fixture
def sample_indicators() -> IndicatorSnapshot:
    return IndicatorSnapshot(
        rsi_14=62.5,
        atr_14=4.25,
        adx_14=28.0,
        ema_9=250.10,
        ema_21=248.00,
        ema_50=242.50,
        ema_200=225.00,
        vwap=249.80,
        volume_ratio=1.35,
        iv_rank=42.0,
        iv_percentile=55.0,
    )


@pytest.fixture
def sample_key_levels() -> KeyLevels:
    return KeyLevels(
        supports=[
            PriceLevel(label="swing_low", price=245.00, strength=70, source="swing"),
            PriceLevel(label="vwap", price=249.80, strength=40, source="intraday"),
        ],
        resistances=[
            PriceLevel(label="prior_high", price=258.00, strength=75, source="weekly"),
        ],
        pivots=[],
    )


@pytest.fixture
def sample_signal(
    frozen_bar_ts: datetime,
    sample_indicators: IndicatorSnapshot,
    sample_key_levels: KeyLevels,
) -> ResearchSignal:
    return ResearchSignal(
        signal_id="sig_test_001",
        run_id="live",
        agent_name="research_1d",
        agent_version="0.1.0",
        strategy_version="v0",
        prompt_hash="a" * 16,
        ticker="TSLA",
        timeframe=Timeframe.TF_1D,
        bar_ts=frozen_bar_ts,
        direction=Direction.LONG,
        conviction=72,
        rationale="Higher highs on daily, ADX rising, above 50EMA. Bullish continuation.",
        preferred_strategies=[
            StrategyFamily.PUT_CREDIT_SPREAD,
            StrategyFamily.LONG_EQUITY,
        ],
        indicators=sample_indicators,
        key_levels=sample_key_levels,
        spot_price=251.25,
    )


@pytest.fixture
def sample_risk_plan() -> RiskPlan:
    return RiskPlan(
        entry_price=251.25,
        stop_loss=245.00,
        take_profit=265.00,
        max_loss_usd=312.50,
        position_size=50,
        risk_reward_ratio=2.20,
        atr_at_entry=4.25,
        stop_atr_mult=1.5,
        target_atr_mult=3.0,
        max_hold_bars=10,
    )


@pytest.fixture
def sample_proposal(
    frozen_bar_ts: datetime,
    sample_risk_plan: RiskPlan,
    sample_signal: ResearchSignal,
) -> TradeProposal:
    return TradeProposal(
        proposal_id="prop_test_001",
        run_id="live",
        agent_version="0.1.0",
        strategy_version="v0",
        prompt_hash="b" * 16,
        ticker="TSLA",
        decision_ts=frozen_bar_ts,
        direction=Direction.LONG,
        strategy_family=StrategyFamily.PUT_CREDIT_SPREAD,
        confluence_score=74,
        risk_plan=sample_risk_plan,
        rationale="Daily + weekly aligned bullish, IV rank 42 favors premium sell.",
        source_signals=[
            SourceSignalRef(
                signal_id=sample_signal.signal_id,
                timeframe=Timeframe.TF_1D,
                direction=Direction.LONG,
                conviction=72,
                weight=0.6,
            )
        ],
        regime_label="up_low_vix_normal_open",
    )


@pytest.fixture
def sample_position(frozen_bar_ts: datetime) -> Position:
    return Position(
        position_id="pos_test_001",
        run_id="live",
        proposal_id="prop_test_001",
        ticker="TSLA",
        strategy_family=StrategyFamily.PUT_CREDIT_SPREAD,
        strategy_version="v0",
        quantity=50,
        entry_price=251.25,
        entry_ts=frozen_bar_ts,
        stop_loss=245.00,
        take_profit=265.00,
        status=PositionStatus.OPEN,
        regime_label_at_entry="up_low_vix_normal_open",
    )


# ---------- Temp directories ----------


@pytest.fixture
def tmp_logs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


# ---------- Execution mode fixtures ----------


@pytest.fixture(params=[ExecMode.BACKTEST_CHEAP, ExecMode.BACKTEST_HYBRID])
def backtest_mode(request) -> ExecMode:
    """Parametrize over cheap and hybrid to catch mode-specific regressions."""
    return request.param
