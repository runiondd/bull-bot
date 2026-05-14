from bullbot.backtest.walkforward import BacktestMetrics, FoldMetrics, aggregate


def test_metrics_has_realized_pnl_max_bp_days_held_fields():
    # Empty metrics should default the three new fields to 0.0.
    m = BacktestMetrics(
        pf_is=1.0, pf_oos=1.0, sharpe_is=0.0, max_dd_pct=0.0, trade_count=0,
    )
    assert m.realized_pnl == 0.0
    assert m.max_bp_held == 0.0
    assert m.days_held == 0.0


def test_aggregate_sums_realized_pnl_across_folds():
    folds = [
        FoldMetrics(
            pf_is=1.2, pf_oos=1.1,
            trade_count_is=3, trade_count_oos=2,
            max_dd_pct=0.10,
            oos_pnls=[100.0, -20.0, 50.0],  # sum = 130
        ),
        FoldMetrics(
            pf_is=1.5, pf_oos=1.3,
            trade_count_is=2, trade_count_oos=4,
            max_dd_pct=0.05,
            oos_pnls=[10.0, 5.0, -3.0, 8.0],  # sum = 20
        ),
    ]
    m = aggregate(folds, category="income")
    assert m.realized_pnl == 150.0  # 130 + 20
    # Stub fields remain 0.0 until follow-up task
    assert m.max_bp_held == 0.0
    assert m.days_held == 0.0
