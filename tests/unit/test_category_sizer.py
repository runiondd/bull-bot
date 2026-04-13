import pytest
from bullbot.engine import position_sizer


def test_income_uses_full_equity():
    # Income: full pool, 2% of 50k = $1000, max_loss=500 -> 2 contracts (capped at 3)
    result = position_sizer.size_position(
        equity=50_000, max_loss_per_contract=500, category="income", regime="bull",
    )
    assert result == 2


def test_growth_bull_uses_full_equity():
    # Growth bull: util=1.0, pool=215k, 2% of 215k = $4300, max_loss=4000 -> 1
    result = position_sizer.size_position(
        equity=215_000, max_loss_per_contract=4000, category="growth", regime="bull",
    )
    assert result == 1


def test_growth_chop_uses_half_equity():
    # Growth chop: util=0.50, pool=107.5k, 2% = $2150, max_loss=4000 -> 0
    # but growth override: 4000 <= 0.50 * 107.5k = 53.75k -> 1
    result = position_sizer.size_position(
        equity=215_000, max_loss_per_contract=4000, category="growth", regime="chop",
    )
    assert result == 1


def test_growth_bear_scales_down():
    # Growth bear: util=0.25, pool=53.75k, 2% = $1075, max_loss=15000 -> 0
    # growth override: 15000 <= 0.50 * 53.75k = 26.875k -> 1
    result = position_sizer.size_position(
        equity=215_000, max_loss_per_contract=15_000, category="growth", regime="bear",
    )
    assert result == 1

    # max_loss exceeds 50% of bear pool -> 0
    result = position_sizer.size_position(
        equity=215_000, max_loss_per_contract=30_000, category="growth", regime="bear",
    )
    assert result == 0


def test_backtest_uses_full_equity():
    # Backtest: full equity regardless of regime
    result = position_sizer.size_position(
        equity=215_000, max_loss_per_contract=4000, category="growth", regime="bear",
        run_id="bt:is:abc",
    )
    assert result == 1


def test_default_category_is_income():
    r1 = position_sizer.size_position(equity=50_000, max_loss_per_contract=500)
    r2 = position_sizer.size_position(equity=50_000, max_loss_per_contract=500, category="income", regime="bull")
    assert r1 == r2
