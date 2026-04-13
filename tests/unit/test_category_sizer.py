import pytest
from bullbot.engine import position_sizer


def test_income_sizes_against_income_pool():
    # Bull regime: growth_frac=0.40, income pool = 60% of 50k = 30k
    # 2% of 30k = $600 risk budget, max_loss=500 -> 1 contract
    result = position_sizer.size_position(
        equity=50_000, max_loss_per_contract=500, category="income", regime="bull",
    )
    assert result == 1


def test_growth_sizes_against_growth_pool():
    # Bull regime: growth_frac=0.40, growth pool = 40% of 50k = 20k
    # 2% of 20k = $400 risk budget, max_loss=300 -> 1 contract
    result = position_sizer.size_position(
        equity=50_000, max_loss_per_contract=300, category="growth", regime="bull",
    )
    assert result == 1


def test_growth_pool_shrinks_in_bear():
    # Bear regime: growth_frac=0.10, growth pool = 10% of 50k = 5k
    # 2% of 5k = $100 risk budget, max_loss=300 -> risk budget says 0,
    # but growth override allows 1 since 300 <= 50% of 5k pool
    result = position_sizer.size_position(
        equity=50_000, max_loss_per_contract=300, category="growth", regime="bear",
    )
    assert result == 1

    # max_loss exceeds 50% of bear growth pool -> truly 0 contracts
    result = position_sizer.size_position(
        equity=50_000, max_loss_per_contract=3000, category="growth", regime="bear",
    )
    assert result == 0


def test_default_category_is_income():
    r1 = position_sizer.size_position(equity=50_000, max_loss_per_contract=500)
    r2 = position_sizer.size_position(equity=50_000, max_loss_per_contract=500, category="income", regime="bull")
    assert r1 == r2
