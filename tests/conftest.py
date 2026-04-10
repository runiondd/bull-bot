"""
Shared pytest fixtures for Bull-Bot.

Anything that multiple test files need goes here: sample schemas,
frozen timestamps, temp SQLite dbs, fake LLM clients, etc.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


# ---------- DB fixtures ----------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """Fresh in-memory SQLite connection with the full schema applied."""
    from bullbot.db import migrations

    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.apply_schema(conn)
    yield conn
    conn.close()


# ---------- Fake HTTP client (Unusual Whales) ----------


@dataclass
class FakeUWResponse:
    status: int = 200
    body: Any = None


class FakeUWClient:
    """Minimal stub for the UW HTTP client.

    Register a path with a (status, body) pair via ``register``, then
    call the instance like ``client.get(path)`` — it will return a
    ``FakeUWResponse`` and append an entry to ``call_log``.
    """

    def __init__(self) -> None:
        self._routes: dict[str, FakeUWResponse] = {}
        self.call_log: list[dict] = []

    def register(self, path: str, status: int = 200, body: Any = None) -> None:
        self._routes[path] = FakeUWResponse(status=status, body=body)

    def get(self, path: str, **kwargs) -> FakeUWResponse:
        self.call_log.append({"method": "GET", "path": path, **kwargs})
        return self._routes.get(path, FakeUWResponse(status=404, body=None))


@pytest.fixture
def fake_uw() -> FakeUWClient:
    return FakeUWClient()


# ---------- Fake Anthropic client ----------


class FakeAnthropicClient:
    """Minimal stub that mimics ``anthropic.Anthropic().messages.create()``."""

    @dataclass
    class _Usage:
        input_tokens: int = 0
        output_tokens: int = 0

    @dataclass
    class _Content:
        text: str = ""

    @dataclass
    class _Response:
        content: list
        usage: "FakeAnthropicClient._Usage" = field(
            default_factory=lambda: FakeAnthropicClient._Usage()
        )
        model: str = "claude-test"
        stop_reason: str = "end_turn"

    def __init__(self) -> None:
        self._queue: deque[str] = deque()
        self.call_log: list[dict] = []
        self.messages = self._Messages(self)

    def queue_response(self, text: str) -> None:
        self._queue.append(text)

    class _Messages:
        def __init__(self, parent: "FakeAnthropicClient") -> None:
            self._parent = parent

        def create(self, **kwargs) -> "FakeAnthropicClient._Response":
            self._parent.call_log.append(kwargs)
            text = (
                self._parent._queue.popleft()
                if self._parent._queue
                else ""
            )
            return FakeAnthropicClient._Response(
                content=[FakeAnthropicClient._Content(text=text)],
                usage=FakeAnthropicClient._Usage(),
            )


@pytest.fixture
def fake_anthropic() -> FakeAnthropicClient:
    return FakeAnthropicClient()
