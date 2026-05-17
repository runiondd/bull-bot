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


from bullbot.v2.chains import _iv_proxy, IV_PROXY_MIN, IV_PROXY_MAX


def _synth_iv(*, underlying_bars: list, vix_bars: list) -> float:
    """Synthetic-chain IV = baseline proxy × event-day multiplier, clamped.

    Composes chains._iv_proxy (realized vol × VIX regime) with
    _event_day_iv_multiplier (Grok T1 F3). Both must be applied — the proxy
    captures regime, the bump captures the jump-day theta-crush spike that
    real chains see but proxies miss.
    """
    baseline = _iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    multiplier = _event_day_iv_multiplier(bars=underlying_bars)
    return max(IV_PROXY_MIN, min(IV_PROXY_MAX, baseline * multiplier))


from datetime import date as _date

BACKTEST_STRIKE_BAND_PCT = 0.10
BACKTEST_MIN_DTE = 21
BACKTEST_MAX_DTE = 365


def _strikes_in_band(*, strikes: list[float], spot: float) -> list[float]:
    """Keep strikes within BACKTEST_STRIKE_BAND_PCT (10%) of spot.
    Returns empty list for non-positive spot."""
    if spot <= 0:
        return []
    lo = spot * (1 - BACKTEST_STRIKE_BAND_PCT)
    hi = spot * (1 + BACKTEST_STRIKE_BAND_PCT)
    return [s for s in strikes if lo <= s <= hi]


def _dtes_in_band(*, expiries: list[str], today: _date) -> list[str]:
    """Keep expiries whose DTE from today is in [21, 365]. Malformed
    expiry strings are silently dropped (synth chain skips them)."""
    out: list[str] = []
    for expiry in expiries:
        try:
            exp = _date.fromisoformat(expiry)
        except (TypeError, ValueError):
            continue
        dte = (exp - today).days
        if BACKTEST_MIN_DTE <= dte <= BACKTEST_MAX_DTE:
            out.append(expiry)
    return out


from bullbot.data.synthetic_chain import bs_price
from bullbot.v2.chains import Chain, ChainQuote, _RISK_FREE_RATE


def synthesize(
    *,
    ticker: str,
    asof_ts: int,
    today: _date,
    spot: float,
    underlying_bars: list,
    vix_bars: list,
    expiries: list[str],
    strikes: list[float],
) -> Chain:
    """Produce a synthetic Chain for a backtest replay step.

    Filters input expiries/strikes to BS-pricable bands (21-365 DTE,
    ATM ±10%), computes one synthesized IV per (ticker, asof), then BS-prices
    every (expiry, strike, kind=call/put) combination. Each ChainQuote's
    bid=ask=last=BS_price and source='bs'.

    Empty chain (quotes=[]) is a valid return when filters strip everything.
    """
    in_band_strikes = _strikes_in_band(strikes=strikes, spot=spot)
    in_band_expiries = _dtes_in_band(expiries=expiries, today=today)
    if not in_band_strikes or not in_band_expiries:
        return Chain(ticker=ticker, asof_ts=asof_ts, quotes=[])

    iv = _synth_iv(underlying_bars=underlying_bars, vix_bars=vix_bars)

    quotes: list[ChainQuote] = []
    for expiry in in_band_expiries:
        exp_date = _date.fromisoformat(expiry)
        t_years = (exp_date - today).days / 365.0
        for strike in in_band_strikes:
            for kind in ("call", "put"):
                bs_kind = "C" if kind == "call" else "P"
                price = bs_price(
                    spot=spot, strike=strike, t_years=t_years,
                    vol=iv, r=_RISK_FREE_RATE, kind=bs_kind,
                )
                quotes.append(ChainQuote(
                    expiry=expiry, strike=strike, kind=kind,
                    bid=price, ask=price, last=price,
                    iv=iv, oi=None, source="bs",
                ))

    return Chain(ticker=ticker, asof_ts=asof_ts, quotes=quotes)
