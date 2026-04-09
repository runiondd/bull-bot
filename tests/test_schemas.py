"""
Smoke + validation tests for the schemas module.

Goals:
- Every schema imports without circular issues
- Fixtures construct valid instances
- Each schema rejects the most likely LLM hallucinations:
  * extra fields
  * wrong direction / stop / target orientation
  * negative prices
  * counts that don't add up
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from schemas import (
    SCHEMA_VERSION,
    BacktestMetrics,
    BacktestRun,
    ConfigDiff,
    Direction,
    EquitySnapshot,
    EvolverProposal,
    ExecMode,
    Fill,
    IndicatorSnapshot,
    KeyLevels,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PerformanceReport,
    Position,
    PositionStatus,
    PriceLevel,
    ResearchSignal,
    RiskPlan,
    RunStatus,
    SourceSignalRef,
    StrategyConfig,
    StrategyFamily,
    Timeframe,
    TradeProposal,
    WalkForwardWindow,
)
from schemas.evolver import ApprovalStatus, DiffOp
from schemas.regime import (
    RegimeSnapshot,
    SessionPhase,
    SpyTrend,
    VixBucket,
    VolRegime,
)

pytestmark = pytest.mark.unit


# ---------- Module level ----------


def test_schema_version_is_set():
    assert SCHEMA_VERSION
    assert isinstance(SCHEMA_VERSION, str)


# ---------- Fixture smoke ----------


def test_sample_signal_valid(sample_signal: ResearchSignal):
    assert sample_signal.ticker == "TSLA"
    assert sample_signal.direction == Direction.LONG
    assert 0 <= sample_signal.conviction <= 100


def test_sample_proposal_valid(sample_proposal: TradeProposal):
    assert sample_proposal.strategy_family == StrategyFamily.PUT_CREDIT_SPREAD
    assert sample_proposal.risk_plan is not None
    assert sample_proposal.risk_plan.stop_loss < sample_proposal.risk_plan.entry_price


def test_sample_position_valid(sample_position: Position):
    assert sample_position.status == PositionStatus.OPEN


# ---------- Signals ----------


def test_signal_rejects_extra_fields(frozen_bar_ts):
    with pytest.raises(ValidationError):
        ResearchSignal(
            signal_id="x",
            run_id="live",
            agent_name="a",
            agent_version="0.1",
            strategy_version="v0",
            prompt_hash="a" * 16,
            ticker="TSLA",
            timeframe=Timeframe.TF_1D,
            bar_ts=frozen_bar_ts,
            direction=Direction.LONG,
            conviction=50,
            rationale="ok",
            spot_price=100.0,
            hallucinated_field="nope",  # type: ignore[call-arg]
        )


def test_signal_ticker_is_uppercased(frozen_bar_ts):
    s = ResearchSignal(
        signal_id="x",
        run_id="live",
        agent_name="a",
        agent_version="0.1",
        strategy_version="v0",
        prompt_hash="a" * 16,
        ticker="tsla",
        timeframe=Timeframe.TF_1D,
        bar_ts=frozen_bar_ts,
        direction=Direction.LONG,
        conviction=50,
        rationale="ok",
        spot_price=100.0,
    )
    assert s.ticker == "TSLA"


def test_signal_dedupes_preferred_strategies(frozen_bar_ts):
    s = ResearchSignal(
        signal_id="x",
        run_id="live",
        agent_name="a",
        agent_version="0.1",
        strategy_version="v0",
        prompt_hash="a" * 16,
        ticker="TSLA",
        timeframe=Timeframe.TF_1D,
        bar_ts=frozen_bar_ts,
        direction=Direction.LONG,
        conviction=50,
        rationale="ok",
        spot_price=100.0,
        preferred_strategies=[
            StrategyFamily.LONG_EQUITY,
            StrategyFamily.LONG_EQUITY,
            StrategyFamily.LONG_CALL,
        ],
    )
    assert s.preferred_strategies == [StrategyFamily.LONG_EQUITY, StrategyFamily.LONG_CALL]


def test_conviction_out_of_range_rejected(frozen_bar_ts):
    with pytest.raises(ValidationError):
        ResearchSignal(
            signal_id="x",
            run_id="live",
            agent_name="a",
            agent_version="0.1",
            strategy_version="v0",
            prompt_hash="a" * 16,
            ticker="TSLA",
            timeframe=Timeframe.TF_1D,
            bar_ts=frozen_bar_ts,
            direction=Direction.LONG,
            conviction=150,
            rationale="ok",
            spot_price=100.0,
        )


# ---------- Decisions ----------


def test_long_proposal_rejects_inverted_stop_target(frozen_bar_ts):
    with pytest.raises(ValidationError):
        TradeProposal(
            proposal_id="x",
            run_id="live",
            agent_version="0.1",
            strategy_version="v0",
            prompt_hash="a" * 16,
            ticker="TSLA",
            decision_ts=frozen_bar_ts,
            direction=Direction.LONG,
            strategy_family=StrategyFamily.LONG_EQUITY,
            confluence_score=80,
            risk_plan=RiskPlan(
                entry_price=100.0,
                stop_loss=105.0,  # wrong for long
                take_profit=95.0,
                max_loss_usd=500,
                position_size=100,
                risk_reward_ratio=1.0,
            ),
            rationale="bad",
        )


def test_short_proposal_accepts_inverted_orientation(frozen_bar_ts):
    tp = TradeProposal(
        proposal_id="x",
        run_id="live",
        agent_version="0.1",
        strategy_version="v0",
        prompt_hash="a" * 16,
        ticker="TSLA",
        decision_ts=frozen_bar_ts,
        direction=Direction.SHORT,
        strategy_family=StrategyFamily.LONG_PUT,
        confluence_score=65,
        risk_plan=RiskPlan(
            entry_price=100.0,
            stop_loss=105.0,
            take_profit=90.0,
            max_loss_usd=500,
            position_size=100,
            risk_reward_ratio=2.0,
        ),
        rationale="ok",
    )
    assert tp.direction == Direction.SHORT


def test_neutral_proposal_must_have_no_trade(frozen_bar_ts, sample_risk_plan):
    with pytest.raises(ValidationError):
        TradeProposal(
            proposal_id="x",
            run_id="live",
            agent_version="0.1",
            strategy_version="v0",
            prompt_hash="a" * 16,
            ticker="TSLA",
            decision_ts=frozen_bar_ts,
            direction=Direction.NEUTRAL,
            strategy_family=StrategyFamily.LONG_EQUITY,  # should be None
            confluence_score=30,
            risk_plan=sample_risk_plan,
            rationale="pass",
        )


def test_neutral_proposal_valid_without_trade(frozen_bar_ts):
    tp = TradeProposal(
        proposal_id="x",
        run_id="live",
        agent_version="0.1",
        strategy_version="v0",
        prompt_hash="a" * 16,
        ticker="TSLA",
        decision_ts=frozen_bar_ts,
        direction=Direction.NEUTRAL,
        confluence_score=30,
        rationale="pass",
    )
    assert tp.strategy_family is None
    assert tp.risk_plan is None


# ---------- Trading ----------


def test_limit_order_requires_limit_price():
    with pytest.raises(ValidationError):
        Order(
            order_id="o1",
            run_id="live",
            ticker="TSLA",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            # no limit_price
        )


def test_stop_limit_requires_both_prices():
    with pytest.raises(ValidationError):
        Order(
            order_id="o1",
            run_id="live",
            ticker="TSLA",
            side=OrderSide.SELL,
            order_type=OrderType.STOP_LIMIT,
            quantity=10,
            limit_price=100,
            # no stop_price
        )


def test_closed_position_requires_exit_fields(frozen_bar_ts):
    with pytest.raises(ValidationError):
        Position(
            position_id="p",
            run_id="live",
            proposal_id="pr",
            ticker="TSLA",
            strategy_family=StrategyFamily.LONG_EQUITY,
            strategy_version="v0",
            quantity=10,
            entry_price=100,
            entry_ts=frozen_bar_ts,
            stop_loss=95,
            take_profit=110,
            status=PositionStatus.STOPPED,
            # missing exit_price / exit_ts
        )


def test_equity_snapshot_valid(frozen_now):
    snap = EquitySnapshot(
        snapshot_id="eq1",
        run_id="live",
        snapshot_ts=frozen_now,
        total_equity=25_000.0,
        cash=20_000.0,
        margin_used=2_500.0,
        gross_exposure=7_500.0,
        net_exposure=5_000.0,
        open_positions=3,
    )
    assert snap.total_equity == 25_000.0


def test_fill_requires_positive_price():
    with pytest.raises(ValidationError):
        Fill(
            fill_id="f1",
            order_id="o1",
            run_id="live",
            fill_price=0,
            fill_quantity=1,
            filled_at=datetime.now(timezone.utc),
            bar_ts=datetime.now(timezone.utc),
        )


# ---------- Performance ----------


def test_performance_report_counts_consistent(frozen_now):
    rpt = PerformanceReport(
        report_id="r1",
        run_id="live",
        strategy_version="v0",
        period_start=frozen_now - timedelta(days=30),
        period_end=frozen_now,
        bars_observed=100,
        trading_days=20,
        starting_equity=25_000,
        ending_equity=26_500,
        total_return_pct=6.0,
        max_dd_pct=-4.5,
        total_trades=10,
        winning_trades=6,
        losing_trades=4,
        win_rate=0.6,
    )
    assert rpt.total_trades == 10


def test_performance_report_bad_period_rejected(frozen_now):
    with pytest.raises(ValidationError):
        PerformanceReport(
            report_id="r1",
            run_id="live",
            strategy_version="v0",
            period_start=frozen_now,
            period_end=frozen_now - timedelta(days=1),  # backwards
            bars_observed=0,
            trading_days=0,
            starting_equity=25_000,
            ending_equity=25_000,
            total_return_pct=0,
            max_dd_pct=0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0,
        )


# ---------- Config ----------


def test_strategy_config_default_weights_sum_to_one():
    cfg = StrategyConfig(version="v0")
    assert abs(sum(cfg.timeframe_weights.values()) - 1.0) < 1e-6


def test_strategy_config_bad_weights_rejected():
    with pytest.raises(ValidationError):
        StrategyConfig(
            version="v1",
            timeframe_weights={
                Timeframe.TF_1D: 0.5,
                Timeframe.TF_1W: 0.3,
                # missing 0.2 — sum = 0.8
            },
        )


# ---------- Evolver ----------


def test_config_diff_rejects_empty_path_segment():
    with pytest.raises(ValidationError):
        ConfigDiff(path="thresholds..min_confluence", op=DiffOp.SET, rationale="x")


def test_evolver_proposal_valid():
    p = EvolverProposal(
        proposal_id="ep1",
        run_id="bt_123",
        parent_version="v0",
        proposed_version="v1",
        diffs=[
            ConfigDiff(
                path="thresholds.min_confluence_score",
                op=DiffOp.SET,
                old_value=60,
                new_value=65,
                rationale="raise bar in high vix",
            )
        ],
        rationale="Overfitting risk reduction",
        agent_version="0.1.0",
    )
    assert p.status == ApprovalStatus.PENDING


# ---------- Backtest ----------


def test_backtest_run_requires_live_run_id_for_live_mode():
    with pytest.raises(ValidationError):
        BacktestRun(
            run_id="bt_abc",
            mode=ExecMode.LIVE,
            strategy_version="v0",
            timeframes=[Timeframe.TF_1D],
            tickers=["TSLA"],
            backtest_start=datetime.now(timezone.utc) - timedelta(days=10),
            backtest_end=datetime.now(timezone.utc),
            initial_capital=25_000,
            cost_cap_usd=100,
        )


def test_backtest_run_rejects_live_run_id_for_backtest_mode():
    with pytest.raises(ValidationError):
        BacktestRun(
            run_id="live",
            mode=ExecMode.BACKTEST_FULL,
            strategy_version="v0",
            timeframes=[Timeframe.TF_1D],
            tickers=["TSLA"],
            backtest_start=datetime.now(timezone.utc) - timedelta(days=10),
            backtest_end=datetime.now(timezone.utc),
            initial_capital=25_000,
            cost_cap_usd=100,
        )


def test_backtest_run_valid():
    run = BacktestRun(
        run_id="bt_abc",
        mode=ExecMode.BACKTEST_CHEAP,
        strategy_version="v0",
        timeframes=[Timeframe.TF_1D, Timeframe.TF_1W],
        tickers=["TSLA", "SPY"],
        backtest_start=datetime.now(timezone.utc) - timedelta(days=365),
        backtest_end=datetime.now(timezone.utc),
        initial_capital=25_000,
        cost_cap_usd=100,
    )
    assert run.status == RunStatus.PENDING
    assert run.tickers == ["TSLA", "SPY"]


def test_walk_forward_rejects_leakage():
    now = datetime.now(timezone.utc)
    m = BacktestMetrics(
        total_return_pct=5.0,
        max_dd_pct=-3.0,
        win_rate=0.55,
        total_trades=25,
    )
    with pytest.raises(ValidationError):
        WalkForwardWindow(
            window_id="w1",
            run_id="bt_abc",
            scope="daily",
            strategy_version="v0",
            train_start=now - timedelta(days=30),
            train_end=now - timedelta(days=5),
            holdout_start=now - timedelta(days=10),  # overlaps train
            holdout_end=now,
            train_metrics=m,
            holdout_metrics=m,
        )


# ---------- Regime ----------


def test_regime_snapshot_valid(frozen_now):
    r = RegimeSnapshot(
        snapshot_ts=frozen_now,
        run_id="live",
        vix_level=14.2,
        spy_price=520.0,
        spy_ema50=510.0,
        spy_ema200=490.0,
        spy_adx=22.0,
        vix_bucket=VixBucket.LOW,
        spy_trend=SpyTrend.UP,
        vol_regime=VolRegime.COMPRESSED,
        session_phase=SessionPhase.OPEN,
        label="up_low_compressed_open",
    )
    assert r.label == "up_low_compressed_open"
