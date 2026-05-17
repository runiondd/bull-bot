"""Unit tests for bullbot.v2.earnings — yfinance earnings-date lookup."""
from __future__ import annotations

from datetime import date

import pytest

from bullbot.v2 import earnings


def test_earningsevent_rejects_non_date_event_date():
    with pytest.raises(TypeError, match="event_date must be a date"):
        earnings.EarningsEvent(ticker="AAPL", event_date="2026-07-25")


def test_earningsevent_normalizes_ticker_to_uppercase():
    ev = earnings.EarningsEvent(ticker="aapl", event_date=date(2026, 7, 25))
    assert ev.ticker == "AAPL"


def test_earningsevent_days_until_returns_positive_for_future_event():
    ev = earnings.EarningsEvent(ticker="AAPL", event_date=date(2026, 6, 1))
    assert ev.days_until(today=date(2026, 5, 17)) == 15


def test_earningsevent_days_until_returns_zero_for_today():
    ev = earnings.EarningsEvent(ticker="AAPL", event_date=date(2026, 5, 17))
    assert ev.days_until(today=date(2026, 5, 17)) == 0


def test_earningsevent_days_until_returns_negative_for_past_event():
    ev = earnings.EarningsEvent(ticker="AAPL", event_date=date(2026, 5, 10))
    assert ev.days_until(today=date(2026, 5, 17)) == -7
