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


from typing import Callable


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
    `ticker`, or None if no upcoming earnings in yfinance's 12-row window.

    The yfinance DataFrame index is tz-aware (typically America/New_York);
    we strip tz and convert to a plain date for the EarningsEvent.
    """
    if client is None:
        client = _default_yf_client()

    ticker_obj = client(ticker)
    df = ticker_obj.get_earnings_dates(limit=12)
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
