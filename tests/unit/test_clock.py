"""Market calendar + time conversion tests."""
from datetime import datetime, timezone

import pytest
from freezegun import freeze_time

from bullbot import clock


def test_utc_epoch_now_returns_int():
    ts = clock.utc_epoch_now()
    assert isinstance(ts, int)
    assert ts > 1_600_000_000

def test_et_now_has_timezone():
    dt = clock.et_now()
    assert dt.tzinfo is not None
    assert "New_York" in str(dt.tzinfo) or "EST" in str(dt.tzinfo) or "EDT" in str(dt.tzinfo)

def test_utc_to_et_conversion():
    utc = datetime(2024, 6, 14, 20, 0, 0, tzinfo=timezone.utc)
    et = clock.utc_to_et(utc)
    assert et.hour == 16
    assert et.minute == 0

def test_epoch_to_et():
    et = clock.epoch_to_et(1718395200)
    assert et.year == 2024
    assert et.month == 6
    assert et.day == 14
    assert et.hour == 16

@freeze_time("2024-06-14 15:30:00", tz_offset=0)
def test_is_market_open_during_rth():
    assert clock.is_market_open_now() is True

@freeze_time("2024-06-14 22:00:00", tz_offset=0)
def test_is_market_closed_after_hours():
    assert clock.is_market_open_now() is False

@freeze_time("2024-06-15 15:30:00", tz_offset=0)
def test_is_market_closed_weekend():
    assert clock.is_market_open_now() is False

@freeze_time("2024-07-04 15:30:00", tz_offset=0)
def test_is_market_closed_holiday():
    assert clock.is_market_open_now() is False

def test_trading_days_between_standard_week():
    from datetime import date
    n = clock.trading_days_between(date(2024, 6, 10), date(2024, 6, 14))
    assert n == 5

def test_trading_days_between_with_holiday():
    from datetime import date
    n = clock.trading_days_between(date(2024, 7, 1), date(2024, 7, 5))
    assert n == 4

def test_previous_trading_day_skips_weekend():
    from datetime import date
    prev = clock.previous_trading_day(date(2024, 6, 10))
    assert prev == date(2024, 6, 7)
