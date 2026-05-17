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


TOUCH_PCT = 0.005  # within 0.5% counts as a "touch" for strength scoring


def _find_swing_extrema(bars: list, n_confirm: int = 3) -> list[Level]:
    """Find local high / low peaks with `n_confirm` bars on each side strictly
    less / greater than the candidate. Returns a list of Level objects with
    kind='swing_high' or 'swing_low' and strength scaled by touch count.

    The last `n_confirm` bars cannot be classified (no right-side confirmation).
    """
    out: list[Level] = []
    if len(bars) < 2 * n_confirm + 1:
        return out

    for i in range(n_confirm, len(bars) - n_confirm):
        cand_high = bars[i].high
        cand_low = bars[i].low
        is_swing_high = all(
            bars[j].high < cand_high
            for j in range(i - n_confirm, i + n_confirm + 1) if j != i
        )
        is_swing_low = all(
            bars[j].low > cand_low
            for j in range(i - n_confirm, i + n_confirm + 1) if j != i
        )
        if is_swing_high:
            touches = sum(
                1 for b in bars
                if abs(b.high - cand_high) / cand_high <= TOUCH_PCT
            ) - 1
            strength = min(1.0, max(touches, 0) / 5.0)
            out.append(Level(price=cand_high, kind="swing_high", strength=strength))
        if is_swing_low:
            touches = sum(
                1 for b in bars
                if abs(b.low - cand_low) / cand_low <= TOUCH_PCT
            ) - 1
            strength = min(1.0, max(touches, 0) / 5.0)
            out.append(Level(price=cand_low, kind="swing_low", strength=strength))
    return out


from statistics import mean

SMA_WINDOWS = (20, 50, 200)


def _sma_levels(bars: list) -> list[Level]:
    """For each window in (20, 50, 200), if enough bars exist, emit a Level
    at the current SMA value with kind sma_<window> and strength scaled by
    window length.

    Longer windows = stronger dynamic S/R (institutional algos watch 200-day
    closer than 20-day).
    """
    out: list[Level] = []
    for w in SMA_WINDOWS:
        if len(bars) < w:
            continue
        sma_value = mean(b.close for b in bars[-w:])
        strength = min(1.0, w / 200.0)
        out.append(Level(price=sma_value, kind=f"sma_{w}", strength=strength))
    return out


ROUND_NUMBER_BAND_PCT = 0.02
ROUND_NUMBER_STRENGTH = 0.3


def _round_step(spot: float) -> float:
    """Step size for round-number candidates, scaled by spot magnitude."""
    if spot < 50.0:
        return 1.0
    if spot < 200.0:
        return 5.0
    if spot < 1000.0:
        return 10.0
    return 50.0


def _round_number_levels(*, spot: float) -> list[Level]:
    """Emit Levels at round-number prices within ROUND_NUMBER_BAND_PCT (2%) of spot.
    Step size scales with spot magnitude (see _round_step)."""
    if spot <= 0:
        return []
    step = _round_step(spot)
    band = spot * ROUND_NUMBER_BAND_PCT
    # Find the nearest multiple of `step` at or below spot
    floor_mult = (spot // step) * step
    out: list[Level] = []
    # Walk ±2 steps and keep anything inside the band
    for k in (-2, -1, 0, 1, 2):
        candidate = floor_mult + k * step
        if candidate <= 0:
            continue
        if abs(candidate - spot) <= band:
            out.append(Level(price=candidate, kind="round_number",
                             strength=ROUND_NUMBER_STRENGTH))
    return out


DEDUP_BAND_PCT = 0.005  # 0.5% — levels within this are considered duplicates

_KIND_PRIORITY = {
    "swing_high": 0,
    "swing_low": 1,
    "sma_200": 2,
    "sma_50": 3,
    "sma_20": 4,
    "round_number": 5,
}


def _dedup_levels(input_levels: list[Level]) -> list[Level]:
    """Collapse levels within DEDUP_BAND_PCT (0.5%) of each other.

    Strategy: sort by price, sweep forward, group adjacent close-priced
    levels into clusters. For each cluster, keep the level with the highest
    strength (ties broken by _KIND_PRIORITY).
    """
    if not input_levels:
        return []

    sorted_levels = sorted(input_levels, key=lambda lvl: lvl.price)
    clusters: list[list[Level]] = [[sorted_levels[0]]]
    for lvl in sorted_levels[1:]:
        prev = clusters[-1][-1]
        if abs(lvl.price - prev.price) / prev.price <= DEDUP_BAND_PCT:
            clusters[-1].append(lvl)
        else:
            clusters.append([lvl])

    out: list[Level] = []
    for cluster in clusters:
        best = max(
            cluster,
            key=lambda lvl: (lvl.strength, -_KIND_PRIORITY[lvl.kind]),
        )
        out.append(best)
    return out
