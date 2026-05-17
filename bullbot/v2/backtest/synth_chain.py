"""Historical chain synthesizer for the v2 backtest harness.

Given bars + VIX + asof date + (expiries, strikes), produce a
bullbot.v2.chains.Chain whose quotes are Black-Scholes-priced from a
regime-aware IV proxy that includes the Grok review Tier 1 Finding 3
event-day bump (1.75x on bars with |return| >= 3% OR TR >= 3 x ATR_14,
decaying linearly back to 1.0x over 5 trading days).

Constraints to keep BS error bounded:
  - Strike range restricted to ATM +/- 10%.
  - DTE restricted to 21 - 365 days.
Vehicle agent declares restricted mode in backtest context so it won't
pick legs outside these bounds.
"""
from __future__ import annotations

EVENT_DAY_RETURN_PCT = 0.03
EVENT_DAY_TR_MULT = 3.0
EVENT_DAY_BUMP_MULT = 1.75
EVENT_DAY_DECAY_BARS = 5
ATR_WINDOW = 14


def _event_day_iv_multiplier(*, bars: list, lookback: int = EVENT_DAY_DECAY_BARS) -> float:
    """Return a multiplier in [1.0, EVENT_DAY_BUMP_MULT] reflecting the
    most recent qualifying event in the last `lookback` trading days.

    Event qualifier: |close-to-close return| >= EVENT_DAY_RETURN_PCT
    OR true_range >= EVENT_DAY_TR_MULT * ATR_14.

    Decay: 1.0 + 0.75 * max((lookback - days_since_event) / lookback, 0).
    Most recent event wins (recency dominates BS-pricing impact).

    Returns 1.0 when bars too short to compute ATR or no qualifying event.
    """
    if len(bars) < ATR_WINDOW + 1:
        return 1.0
    # Compute trailing TRs (need prev_close)
    trs: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b.high - b.low)
            continue
        prev_close = bars[i - 1].close
        trs.append(max(
            b.high - b.low,
            abs(b.high - prev_close),
            abs(b.low - prev_close),
        ))
    atr_14 = sum(trs[-ATR_WINDOW:]) / ATR_WINDOW
    if atr_14 <= 0:
        atr_14 = float("inf")  # disable TR rule when baseline vol is zero

    # Scan the last `lookback` bars for events; track most recent (smallest age).
    most_recent_event_age: int | None = None
    for age in range(lookback):
        idx = -1 - age  # age=0 -> idx=-1 (most recent bar)
        if abs(idx) > len(bars):
            break
        if idx == -len(bars):
            continue  # no prev_close on the first bar
        b = bars[idx]
        prev_close = bars[idx - 1].close
        ret = abs(b.close - prev_close) / prev_close if prev_close > 0 else 0.0
        tr = trs[idx]
        if ret >= EVENT_DAY_RETURN_PCT or tr >= EVENT_DAY_TR_MULT * atr_14:
            most_recent_event_age = age
            break  # we want the most recent — loop ascends from age=0

    if most_recent_event_age is None:
        return 1.0
    decay = max((lookback - most_recent_event_age) / lookback, 0.0)
    return 1.0 + (EVENT_DAY_BUMP_MULT - 1.0) * decay
