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


import pandas as pd


class _FakeYFTicker:
    """Mimics yfinance.Ticker minimally — only the get_earnings_dates surface."""
    def __init__(self, dates_df: pd.DataFrame | None):
        self._df = dates_df
    def get_earnings_dates(self, limit: int = 12):
        return self._df


def _earnings_df(*event_strings: str) -> pd.DataFrame:
    """Build a yfinance-shaped earnings DataFrame from ISO date strings."""
    idx = pd.DatetimeIndex([pd.Timestamp(s, tz="America/New_York") for s in event_strings])
    return pd.DataFrame(
        {"EPS Estimate": [None] * len(event_strings),
         "Reported EPS": [None] * len(event_strings),
         "Surprise(%)": [None] * len(event_strings)},
        index=idx,
    )


def test_fetch_next_earnings_returns_soonest_future_event():
    df = _earnings_df("2026-08-01", "2026-07-25", "2026-05-01", "2026-02-01")
    fake = _FakeYFTicker(df)
    ev = earnings.fetch_next_earnings(
        ticker="AAPL", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert ev is not None
    assert ev.ticker == "AAPL"
    assert ev.event_date == date(2026, 7, 25)


def test_fetch_next_earnings_returns_event_dated_today_as_future():
    """Earnings exactly on `today` count as future (days_until == 0)."""
    df = _earnings_df("2026-05-17", "2026-02-01")
    fake = _FakeYFTicker(df)
    ev = earnings.fetch_next_earnings(
        ticker="AAPL", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert ev is not None
    assert ev.event_date == date(2026, 5, 17)


def test_fetch_next_earnings_ignores_past_events_only():
    df = _earnings_df("2026-05-01", "2026-02-01", "2025-11-01")
    fake = _FakeYFTicker(df)
    ev = earnings.fetch_next_earnings(
        ticker="AAPL", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert ev is None  # nothing in the future


def test_fetch_next_earnings_normalizes_ticker_to_uppercase():
    df = _earnings_df("2026-06-01")
    fake = _FakeYFTicker(df)
    ev = earnings.fetch_next_earnings(
        ticker="aapl", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert ev.ticker == "AAPL"
