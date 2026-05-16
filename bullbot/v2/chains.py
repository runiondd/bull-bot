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
