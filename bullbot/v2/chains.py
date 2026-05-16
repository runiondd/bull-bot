"""Live option-chain integration for v2 Phase C.

Two public entry points:
- fetch_chain(ticker, asof, client=None): pull a Yahoo chain, cache rows
  into v2_chain_snapshots, return a Chain.
- price_leg(leg, spot, iv, today, conn=None): return (mid_price, source)
  for a single OptionLeg, trying Yahoo (cached snapshot or fresh fetch)
  before falling back to Black-Scholes.

BS math is reused from bullbot.data.synthetic_chain — do not re-implement.
"""
from __future__ import annotations

from dataclasses import dataclass

VALID_KINDS = ("call", "put")
VALID_SOURCES = ("yahoo", "bs")


@dataclass
class ChainQuote:
    """A single (expiry, strike, kind) quote with both market and model fields."""

    expiry: str            # 'YYYY-MM-DD'
    strike: float
    kind: str              # 'call' | 'put'
    bid: float | None
    ask: float | None
    last: float | None
    iv: float | None
    oi: int | None
    source: str            # 'yahoo' | 'bs'

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}; got {self.kind!r}")
        if self.source not in VALID_SOURCES:
            raise ValueError(f"source must be one of {VALID_SOURCES}; got {self.source!r}")

    def mid_price(self) -> float | None:
        """Bid-ask midpoint, or last price if either bid or ask is missing,
        or None if no prices are available."""
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        if self.last is not None:
            return self.last
        return None


@dataclass
class Chain:
    """A collection of ChainQuotes for one (ticker, asof_ts)."""

    ticker: str
    asof_ts: int
    quotes: list[ChainQuote]

    def find_quote(self, *, expiry: str, strike: float, kind: str) -> ChainQuote | None:
        """Linear lookup. Chains are O(few hundred) entries in practice, so
        a hash index would be premature optimization."""
        for q in self.quotes:
            if q.expiry == expiry and q.strike == strike and q.kind == kind:
                return q
        return None


from statistics import median

from bullbot.data.synthetic_chain import realized_vol

IV_PROXY_MIN = 0.05   # 5% — floor; lower than this and BS produces nonsense
IV_PROXY_MAX = 3.00   # 300% — ceiling; higher than this almost always means bad inputs


def _iv_proxy(*, underlying_bars: list, vix_bars: list) -> float:
    """Annualized IV estimate when Yahoo gives no IV for a strike.

    Formula:  realized_vol_30(underlying) * (vix_today / median(vix_last_60))
    Bounded to [IV_PROXY_MIN, IV_PROXY_MAX].

    Bars expected to be ordered oldest-first with a `.close` attribute (matches
    the shape that bullbot.v2.runner._load_bars and the bars table both produce).
    Falls back gracefully when either series is too short for its respective
    sub-computation:
        - underlying < 31 bars → realized_vol returns its 0.30 default
        - vix < 60 bars        → regime multiplier defaults to 1.0
    """
    rv = realized_vol(underlying_bars, window=30)
    if len(vix_bars) < 60:
        multiplier = 1.0
    else:
        vix_today = vix_bars[-1].close
        vix_baseline = median(b.close for b in vix_bars[-60:])
        multiplier = vix_today / vix_baseline if vix_baseline > 0 else 1.0
    iv = rv * multiplier
    return max(IV_PROXY_MIN, min(IV_PROXY_MAX, iv))


from datetime import date

from bullbot.data.synthetic_chain import bs_price
from bullbot.v2.positions import OptionLeg

try:
    from bullbot.config import RISK_FREE_RATE as _RISK_FREE_RATE  # type: ignore[attr-defined]
except (ImportError, AttributeError):
    _RISK_FREE_RATE = 0.045  # 4.5% — matches v1 synthetic chain default


def _price_leg_bs(
    *,
    leg: OptionLeg,
    spot: float,
    iv: float,
    today: date,
) -> float:
    """Per-share Black-Scholes price for one OptionLeg.

    Returns spot for share legs (no time value), max(intrinsic, 0) for expired
    options, and the standard BS formula otherwise. The returned value is in
    per-share dollars — callers multiply by qty * 100 (for option legs) or
    qty (for share legs) to get position-level dollar value.
    """
    if leg.kind == "share":
        return spot
    expiry_date = date.fromisoformat(leg.expiry)
    t_years = max(0.0, (expiry_date - today).days / 365.0)
    bs_kind = "C" if leg.kind == "call" else "P"
    return bs_price(
        spot=spot, strike=leg.strike, t_years=t_years,
        vol=iv, r=_RISK_FREE_RATE, kind=bs_kind,
    )
