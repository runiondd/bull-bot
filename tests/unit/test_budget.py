"""Unit tests for bullbot.risk.budget."""
from __future__ import annotations

import pytest

from bullbot.risk import budget


def test_per_trade_budget_income_default():
    # Income account uses INITIAL_CAPITAL_USD ($50k) × 2% = $1000
    assert budget.per_trade_budget_usd(category="income") == pytest.approx(1000.0)


def test_per_trade_budget_growth_default():
    # Growth account uses GROWTH_CAPITAL_USD ($215k) × 2% = $4300
    assert budget.per_trade_budget_usd(category="growth") == pytest.approx(4300.0)


def test_per_trade_budget_respects_override_pct():
    # If Dan wants to expand risk tolerance to 5%, the budget moves with it.
    assert budget.per_trade_budget_usd(category="income", max_loss_pct=0.05) == pytest.approx(2500.0)


def test_per_trade_budget_unknown_category_defaults_to_income():
    # Unknown category should fall back to income capital, never raise.
    assert budget.per_trade_budget_usd(category="bogus") == pytest.approx(1000.0)
