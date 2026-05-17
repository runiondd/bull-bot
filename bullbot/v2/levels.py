"""Support/resistance level computation for v2 Phase C.

Pure function over a list of bars — no DB, no I/O, no LLM. Returns a list
of Level objects ranked by absolute distance to the most recent close.

The vehicle agent (C.3) feeds the top-N nearest_resistance / nearest_support
levels into the LLM context. The exits.py module (C.3) compares the current
spot to stored profit_target_price / stop_price values that are themselves
derived from these levels at entry time.
"""
from __future__ import annotations

from dataclasses import dataclass

VALID_KINDS = (
    "swing_high", "swing_low",
    "sma_20", "sma_50", "sma_200",
    "round_number",
)


@dataclass(frozen=True)
class Level:
    """A single price level with provenance and a [0, 1] strength score."""

    price: float
    kind: str
    strength: float

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}; got {self.kind!r}")
        if not (0.0 <= self.strength <= 1.0):
            raise ValueError(f"strength must be in [0.0, 1.0]; got {self.strength}")

    def distance_to(self, *, spot: float) -> float:
        """Absolute dollar distance from this level to `spot`."""
        return abs(self.price - spot)

    def distance_pct_to(self, *, spot: float) -> float:
        """Absolute percent distance from this level to `spot` (using spot as denom)."""
        return abs(self.price - spot) / spot

    def is_above(self, *, spot: float) -> bool:
        """True if this level sits above `spot` (resistance side)."""
        return self.price > spot
