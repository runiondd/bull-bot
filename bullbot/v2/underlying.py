"""Rules-based directional-signal generator (Phase A).

Pure function over daily bars. No options data, no LLM. Outputs a
DirectionalSignal that the runner persists to the directional_signals
table. Rules version is "v1" — bumping the version triggers a parallel
write so A/B comparison is possible without overwriting old signals.
"""
from __future__ import annotations

from typing import Protocol

from bullbot.v2.signals import DirectionalSignal

RULES_VERSION = "v1"
LOOKBACK_REQUIRED = 200
HORIZON_DAYS = 30


class _BarLike(Protocol):
    close: float
    high: float
    low: float


def _sma(values: list[float], window: int) -> float:
    if len(values) < window:
        return float("nan")
    return sum(values[-window:]) / window


def _atr(bars: list[_BarLike], window: int = 20) -> float:
    """Simple ATR: mean of (high-low) over the last `window` bars."""
    if len(bars) < window:
        return 0.0
    recent = bars[-window:]
    return sum(b.high - b.low for b in recent) / window


def classify(ticker: str, bars: list[_BarLike], asof_ts: int) -> DirectionalSignal:
    """Return a DirectionalSignal for `ticker` at `asof_ts` from `bars`."""
    if len(bars) < LOOKBACK_REQUIRED:
        return DirectionalSignal(
            ticker=ticker, asof_ts=asof_ts, direction="no_edge",
            confidence=0.0, horizon_days=HORIZON_DAYS,
            rationale=f"insufficient bars ({len(bars)} < {LOOKBACK_REQUIRED})",
            rules_version=RULES_VERSION,
        )
    closes = [b.close for b in bars]
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    spot = closes[-1]
    atr = _atr(bars, 20) or 1e-9

    distance = abs(spot - sma50) / atr
    confidence = min(max(distance / 3.0, 0.0), 1.0)

    if sma50 > sma200 and spot > sma50:
        return DirectionalSignal(
            ticker=ticker, asof_ts=asof_ts, direction="bullish",
            confidence=confidence, horizon_days=HORIZON_DAYS,
            rationale=f"50-SMA {sma50:.2f} > 200-SMA {sma200:.2f} AND spot {spot:.2f} > 50-SMA",
            rules_version=RULES_VERSION,
        )
    if sma50 < sma200 and spot < sma50:
        return DirectionalSignal(
            ticker=ticker, asof_ts=asof_ts, direction="bearish",
            confidence=confidence, horizon_days=HORIZON_DAYS,
            rationale=f"50-SMA {sma50:.2f} < 200-SMA {sma200:.2f} AND spot {spot:.2f} < 50-SMA",
            rules_version=RULES_VERSION,
        )
    return DirectionalSignal(
        ticker=ticker, asof_ts=asof_ts, direction="chop",
        confidence=confidence, horizon_days=HORIZON_DAYS,
        rationale=f"no clear trend (50-SMA {sma50:.2f}, 200-SMA {sma200:.2f}, spot {spot:.2f})",
        rules_version=RULES_VERSION,
    )
