"""
Regime classification snapshots.

Regime labels come from simple deterministic rules (VIX level, SPY
trend, ADX, session phase). No ML model — just configurable buckets
so the evolver can slice performance by regime without training
a classifier.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, model_validator

from schemas.common import BaseSchema


class VixBucket(str, Enum):
    LOW = "low"           # < 15
    NORMAL = "normal"     # 15-22
    ELEVATED = "elevated" # 22-30
    HIGH = "high"         # 30+


class SpyTrend(str, Enum):
    UP = "up"             # SPY > EMA50 > EMA200, slope positive
    SIDEWAYS = "sideways" # no clean structure
    DOWN = "down"         # SPY < EMA50 < EMA200, slope negative


class VolRegime(str, Enum):
    """Rolling realized vol bucket, independent of VIX."""

    COMPRESSED = "compressed"  # bottom quartile
    NORMAL = "normal"
    EXPANDED = "expanded"      # top quartile


class SessionPhase(str, Enum):
    """For intraday signals — where in the trading day we are."""

    PRE_MARKET = "pre_market"
    OPEN = "open"                  # 9:30-10:30
    MID_MORNING = "mid_morning"    # 10:30-12:00
    LUNCH = "lunch"                # 12:00-14:00
    AFTERNOON = "afternoon"        # 14:00-15:30
    CLOSE = "close"                # 15:30-16:00
    AFTER_HOURS = "after_hours"
    CLOSED = "closed"


class RegimeSnapshot(BaseSchema):
    """
    A complete regime label for one timestamp.

    Cheap to compute, cached per bar timestamp. The composite `label`
    field is what gets joined onto signals/positions for breakdowns.
    """

    snapshot_ts: datetime
    run_id: str = Field(..., min_length=1, max_length=64)

    # Inputs
    vix_level: float | None = Field(default=None, ge=0)
    spy_price: float | None = Field(default=None, gt=0)
    spy_ema50: float | None = Field(default=None, gt=0)
    spy_ema200: float | None = Field(default=None, gt=0)
    spy_adx: float | None = Field(default=None, ge=0, le=100)
    realized_vol_20: float | None = Field(default=None, ge=0)
    realized_vol_percentile: float | None = Field(default=None, ge=0, le=100)
    relative_volume: float | None = Field(default=None, ge=0)

    # Classified buckets
    vix_bucket: VixBucket | None = None
    spy_trend: SpyTrend | None = None
    vol_regime: VolRegime | None = None
    session_phase: SessionPhase | None = None

    # Composite label, e.g. "up_low_vix_compressed_open"
    # This is the join key for regime breakdowns — intentionally a flat
    # string so the evolver can group by it without decomposing.
    label: str = Field(..., min_length=1, max_length=64)

    @model_validator(mode="after")
    def _label_not_empty(self) -> "RegimeSnapshot":
        if not self.label.strip():
            raise ValueError("label cannot be empty")
        return self
