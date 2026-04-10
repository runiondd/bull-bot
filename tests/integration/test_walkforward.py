"""Walk-forward harness tests."""
from dataclasses import dataclass
from datetime import datetime, timezone

from bullbot.backtest import walkforward


def _seed_bars(db_conn, ticker="SPY", n_days=500):
    base_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    for i in range(n_days):
        ts = base_ts + i * 86400
        price = 500.0 + i * 0.3 + (i % 7) * 0.5
        db_conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, ?, ?, ?, ?, ?)",
            (ticker, ts, price, price + 2, price - 1, price + 0.5, 1_000_000),
        )


def test_compute_folds_respects_min_max():
    folds = walkforward.compute_folds(total_days=252 * 2, train_frac=0.7, step_days=30, min_folds=3, max_folds=5)
    assert 3 <= len(folds) <= 5


def test_compute_folds_returns_non_overlapping_test_windows():
    folds = walkforward.compute_folds(total_days=500, train_frac=0.7, step_days=30, min_folds=3, max_folds=5)
    for fold in folds:
        assert fold.train_start < fold.train_end <= fold.test_start < fold.test_end


def test_profit_factor_metric_simple():
    pnls = [100.0, -50.0, 200.0, -30.0]
    assert abs(walkforward.profit_factor(pnls) - 3.75) < 1e-9


def test_profit_factor_all_losses_returns_zero():
    assert walkforward.profit_factor([-10.0, -5.0, -20.0]) == 0.0


def test_profit_factor_no_trades_returns_zero():
    assert walkforward.profit_factor([]) == 0.0


def test_aggregate_metrics_combines_folds():
    fold_metrics = [
        walkforward.FoldMetrics(pf_is=1.2, pf_oos=1.1, trade_count_is=30, trade_count_oos=12, max_dd_pct=0.05),
        walkforward.FoldMetrics(pf_is=1.4, pf_oos=1.3, trade_count_is=25, trade_count_oos=10, max_dd_pct=0.06),
    ]
    agg = walkforward.aggregate(fold_metrics)
    assert agg.trade_count == 22
    assert agg.pf_oos > 0
