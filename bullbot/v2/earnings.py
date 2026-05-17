"""Earnings-date lookup for v2 Phase C.

Two public entry points:
- fetch_next_earnings(ticker, today, client=None) -> EarningsEvent | None
  Returns the soonest future earnings event (or None if none found within
  yfinance's 12-event window).
- earnings_window_active(ticker, today, iv_rank, client=None) -> bool
  True when days_to_earnings <= 14 OR iv_rank > 0.75 (Grok review Tier 2 #7).

Yahoo client is injected as a callable for testability — mirrors the pattern
in bullbot/v2/chains.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class EarningsEvent:
    ticker: str
    event_date: date

    def __post_init__(self) -> None:
        if not isinstance(self.event_date, date):
            raise TypeError(
                f"event_date must be a date; got {type(self.event_date).__name__}"
            )
        # Normalize ticker symbol to uppercase to match the rest of the v2 codebase.
        self.ticker = self.ticker.upper()

    def days_until(self, *, today: date) -> int:
        """Integer day count from `today` to `event_date`. Positive = future,
        zero = today, negative = past."""
        return (self.event_date - today).days


import logging
from typing import Callable

_log = logging.getLogger(__name__)


def _default_yf_client():
    """Lazy yfinance import — keeps tests independent of yfinance availability.
    Mirrors bullbot/v2/chains.py:_default_yf_client."""
    import yfinance as yf
    return lambda symbol: yf.Ticker(symbol)


def fetch_next_earnings(
    *,
    ticker: str,
    today: date,
    client: Callable[[str], object] | None = None,
) -> EarningsEvent | None:
    """Return the soonest future earnings event (event_date >= today) for
    `ticker`, or None if no upcoming earnings or any failure.

    Failure modes that yield None:
      - Yahoo client construct raises (network error, bad ticker)
      - get_earnings_dates raises (yfinance parse error, schema change)
      - yfinance returns None (ETFs, funds, new IPOs)
      - DataFrame empty
      - DataFrame contains only past events
    """
    if client is None:
        client = _default_yf_client()

    try:
        ticker_obj = client(ticker)
        df = ticker_obj.get_earnings_dates(limit=12)
    except Exception as exc:  # noqa: BLE001 — Yahoo can raise anything
        _log.warning("fetch_next_earnings: yfinance failed for %s: %s", ticker, exc)
        return None

    if df is None or df.empty:
        return None

    future_dates = [
        ts.date() for ts in df.index
        if ts.date() >= today
    ]
    if not future_dates:
        return None

    soonest = min(future_dates)
    return EarningsEvent(ticker=ticker, event_date=soonest)


DAYS_TO_PRINT_NONE_SENTINEL = 999  # large enough that any `<= N` check returns False


def days_to_print(
    *,
    ticker: str,
    today: date,
    client: Callable[[str], object] | None = None,
) -> int:
    """Days from `today` until the next upcoming earnings event.

    Returns DAYS_TO_PRINT_NONE_SENTINEL (999) when no upcoming earnings
    can be found (no events in yfinance window, all past, or fetch failure).
    The sentinel lets callers do `if days_to_print(...) <= 14` without
    branching on None.
    """
    ev = fetch_next_earnings(ticker=ticker, today=today, client=client)
    if ev is None:
        return DAYS_TO_PRINT_NONE_SENTINEL
    return ev.days_until(today=today)
