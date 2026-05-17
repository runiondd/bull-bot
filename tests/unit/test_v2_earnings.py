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


def test_fetch_next_earnings_returns_none_when_yfinance_returns_none():
    """ETFs / funds / new IPOs often have get_earnings_dates() return None."""
    fake = _FakeYFTicker(None)
    ev = earnings.fetch_next_earnings(
        ticker="SPY", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert ev is None


def test_fetch_next_earnings_returns_none_when_dataframe_is_empty():
    fake = _FakeYFTicker(pd.DataFrame())
    ev = earnings.fetch_next_earnings(
        ticker="XYZ", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert ev is None


def test_fetch_next_earnings_returns_none_when_yfinance_raises_on_construct():
    def raising_client(symbol):
        raise ConnectionError("simulated yahoo timeout")
    ev = earnings.fetch_next_earnings(
        ticker="AAPL", today=date(2026, 5, 17),
        client=raising_client,
    )
    assert ev is None


def test_fetch_next_earnings_returns_none_when_get_earnings_dates_raises():
    class RaisingTicker:
        def get_earnings_dates(self, limit=12):
            raise ValueError("simulated yfinance parse error")
    ev = earnings.fetch_next_earnings(
        ticker="AAPL", today=date(2026, 5, 17),
        client=lambda symbol: RaisingTicker(),
    )
    assert ev is None


def test_days_to_print_returns_int_for_future_earnings():
    df = _earnings_df("2026-06-01", "2026-02-01")
    fake = _FakeYFTicker(df)
    n = earnings.days_to_print(
        ticker="AAPL", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert n == 15


def test_days_to_print_returns_sentinel_when_no_upcoming_event():
    df = _earnings_df("2026-02-01", "2025-11-01")
    fake = _FakeYFTicker(df)
    n = earnings.days_to_print(
        ticker="AAPL", today=date(2026, 5, 17),
        client=lambda symbol: fake,
    )
    assert n == earnings.DAYS_TO_PRINT_NONE_SENTINEL
    assert n > 14


def test_days_to_print_returns_sentinel_when_yfinance_fails():
    def raising_client(symbol):
        raise ConnectionError("network down")
    n = earnings.days_to_print(
        ticker="AAPL", today=date(2026, 5, 17),
        client=raising_client,
    )
    assert n == earnings.DAYS_TO_PRINT_NONE_SENTINEL
