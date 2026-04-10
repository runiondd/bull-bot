"""
Market calendar + time conversion.
All DB timestamps are UTC epoch seconds (integers). Display and business
logic uses Eastern Time.
"""
from __future__ import annotations
from datetime import date, datetime, timezone, timedelta
import pandas_market_calendars as mcal
import pytz
from bullbot import config

_ET = pytz.timezone(config.MARKET_TIMEZONE)
_CAL = mcal.get_calendar("NYSE")


def utc_epoch_now() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


def et_now() -> datetime:
    return datetime.now(tz=_ET)


def utc_to_et(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("utc_to_et requires a tz-aware datetime")
    return dt.astimezone(_ET)


def epoch_to_et(epoch_seconds: int) -> datetime:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).astimezone(_ET)


def is_market_open_now() -> bool:
    now_et = et_now()
    sched = _CAL.schedule(start_date=now_et.date(), end_date=now_et.date())
    if sched.empty:
        return False
    open_et = sched.iloc[0]["market_open"].tz_convert(_ET)
    close_et = sched.iloc[0]["market_close"].tz_convert(_ET)
    return open_et <= now_et <= close_et


def trading_days_between(start: date, end: date) -> int:
    sched = _CAL.schedule(start_date=start, end_date=end)
    return len(sched)


def previous_trading_day(d: date) -> date:
    start = d - timedelta(days=10)
    sched = _CAL.schedule(start_date=start, end_date=d)
    # The schedule index is tz-naive date-level Timestamps (datetime64[us]).
    # Compare using a tz-naive Timestamp for the cutoff date.
    import pandas as pd
    cutoff = pd.Timestamp(d)
    prior = sched[sched.index < cutoff]
    if prior.empty:
        raise ValueError(f"no trading day found before {d}")
    return prior.index[-1].date()


def market_open_et(d: date) -> datetime | None:
    sched = _CAL.schedule(start_date=d, end_date=d)
    if sched.empty:
        return None
    return sched.iloc[0]["market_open"].tz_convert(_ET).to_pydatetime()


def market_close_et(d: date) -> datetime | None:
    sched = _CAL.schedule(start_date=d, end_date=d)
    if sched.empty:
        return None
    return sched.iloc[0]["market_close"].tz_convert(_ET).to_pydatetime()
