"""
Market and ticker regime signals.

Computes structured signal dataclasses from raw bar data. All functions
return None on insufficient data rather than raising.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from bullbot import config
from bullbot.features.indicators import iv_rank as _iv_rank
from bullbot.features.indicators import iv_percentile as _iv_percentile
from bullbot.features.indicators import sma as _sma


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketSignals:
    vix_level: float
    vix_percentile: float
    vix_term_slope: float
    spy_trend: str              # 'up' | 'down' | 'flat'
    spy_momentum: float
    breadth_score: float
    sector_momentum: dict       # {etf: float}
    risk_appetite: str          # 'risk_on' | 'neutral' | 'risk_off'
    realized_vs_implied: float


@dataclass(frozen=True)
class TickerSignals:
    ticker: str
    iv_rank: float
    iv_percentile: float
    sector_relative: float
    vol_regime: str             # 'low' | 'moderate' | 'high'
    sector_etf: Optional[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_closes(bars: list[dict]) -> list[float]:
    """Return close prices in chronological order."""
    return [b["close"] for b in bars]


def _annualized_vol_20d(closes: list[float]) -> float | None:
    """20-day annualized realized volatility from daily log returns."""
    if len(closes) < 21:
        return None
    window = closes[-21:]
    log_returns = [math.log(window[i] / window[i - 1]) for i in range(1, len(window))]
    mean_r = sum(log_returns) / len(log_returns)
    var = sum((r - mean_r) ** 2 for r in log_returns) / (len(log_returns) - 1)
    return math.sqrt(var) * math.sqrt(252)


def _rate_of_change(closes: list[float], period: int) -> float | None:
    """Percent rate of change over `period` bars."""
    if len(closes) < period + 1:
        return None
    return ((closes[-1] - closes[-(period + 1)]) / closes[-(period + 1)]) * 100.0


# ---------------------------------------------------------------------------
# compute_market_signals
# ---------------------------------------------------------------------------

def compute_market_signals(
    vix_bars: list[dict],
    spy_bars: list[dict],
    sector_bars: dict[str, list[dict]],
    hyg_bars: list[dict],
    tlt_bars: list[dict],
) -> MarketSignals | None:
    """Compute market-wide regime signals from bar data.

    Returns None if any primary series has fewer than 60 bars.
    """
    vix_closes = _extract_closes(vix_bars)
    spy_closes = _extract_closes(spy_bars)
    hyg_closes = _extract_closes(hyg_bars)
    tlt_closes = _extract_closes(tlt_bars)

    if len(vix_closes) < 60 or len(spy_closes) < 60:
        return None

    # --- VIX ---
    vix_level = vix_closes[-1]

    # Percentile vs 252d history (cap to available)
    history_window = vix_closes[-252:]
    below = sum(1 for v in history_window if v <= vix_level)
    vix_pct = 100.0 * below / len(history_window)

    # Term slope: 5d SMA / 20d SMA
    vix_sma5 = _sma(vix_closes, 5)
    vix_sma20 = _sma(vix_closes, 20)
    if vix_sma5 is not None and vix_sma20 is not None and vix_sma20 != 0:
        vix_term_slope = vix_sma5 / vix_sma20
    else:
        vix_term_slope = 1.0

    # --- SPY ---
    sma50 = _sma(spy_closes, 50)
    sma200 = _sma(spy_closes, 200)
    if sma50 is not None and sma200 is not None:
        if sma50 > sma200:
            spy_trend = "up"
        elif sma50 < sma200:
            spy_trend = "down"
        else:
            spy_trend = "flat"
    else:
        spy_trend = "flat"

    spy_momentum = _rate_of_change(spy_closes, 20) or 0.0

    # --- Breadth: % of SECTOR_ETFS above their 50d SMA ---
    above_count = 0
    sector_momentum: dict[str, float] = {}
    for etf in config.SECTOR_ETFS:
        etf_bars = sector_bars.get(etf, [])
        etf_closes = _extract_closes(etf_bars)
        mom = _rate_of_change(etf_closes, 20) or 0.0
        sector_momentum[etf] = mom
        if len(etf_closes) >= 50:
            etf_sma50 = _sma(etf_closes, 50)
            if etf_sma50 is not None and etf_closes[-1] > etf_sma50:
                above_count += 1

    breadth_score = 100.0 * above_count / len(config.SECTOR_ETFS)

    # --- Risk appetite: HYG/TLT ratio 20d change ---
    if len(hyg_closes) >= 21 and len(tlt_closes) >= 21 and tlt_closes[-21] != 0 and tlt_closes[-1] != 0:
        ratio_now = hyg_closes[-1] / tlt_closes[-1]
        ratio_20d_ago = hyg_closes[-21] / tlt_closes[-21]
        ratio_change = (ratio_now - ratio_20d_ago) / ratio_20d_ago if ratio_20d_ago != 0 else 0.0
        if ratio_change > 0.01:
            risk_appetite = "risk_on"
        elif ratio_change < -0.01:
            risk_appetite = "risk_off"
        else:
            risk_appetite = "neutral"
    else:
        risk_appetite = "neutral"

    # --- Realized vs Implied: SPY 20d annualized realized vol minus VIX ---
    realized_vol = _annualized_vol_20d(spy_closes)
    if realized_vol is not None:
        realized_vs_implied = realized_vol * 100.0 - vix_level
    else:
        realized_vs_implied = 0.0

    return MarketSignals(
        vix_level=vix_level,
        vix_percentile=vix_pct,
        vix_term_slope=vix_term_slope,
        spy_trend=spy_trend,
        spy_momentum=spy_momentum,
        breadth_score=breadth_score,
        sector_momentum=sector_momentum,
        risk_appetite=risk_appetite,
        realized_vs_implied=realized_vs_implied,
    )


# ---------------------------------------------------------------------------
# compute_ticker_signals
# ---------------------------------------------------------------------------

def compute_ticker_signals(
    ticker: str,
    ticker_bars: list[dict],
    iv_history: list[float],
    current_iv: float | None,
    sector_etf_bars: list[dict] | None,
) -> TickerSignals | None:
    """Compute per-ticker regime signals.

    Returns None if ticker_bars has fewer than 20 bars.
    """
    closes = _extract_closes(ticker_bars)
    if len(closes) < 20:
        return None

    # --- IV rank / percentile ---
    if iv_history and current_iv is not None:
        ivr = _iv_rank(current_iv, iv_history)
        ivp = _iv_percentile(current_iv, iv_history)
    else:
        ivr = 50.0
        ivp = 50.0

    # --- Sector ETF lookup ---
    sector_etf = config.TICKER_SECTOR_MAP.get(ticker, None)

    # --- Sector relative return ---
    if sector_etf is not None:
        sector_closes = _extract_closes(sector_etf_bars)
        if len(sector_closes) >= 21 and len(closes) >= 21:
            ticker_roc = _rate_of_change(closes, 20) or 0.0
            sector_roc = _rate_of_change(sector_closes, 20) or 0.0
            sector_relative = ticker_roc - sector_roc
        else:
            sector_relative = 0.0
    else:
        sector_relative = 0.0

    # --- Vol regime: 20d realized vol percentile vs rolling history ---
    # Compute rolling 20d realized vols across the available bars
    vol_history: list[float] = []
    for start in range(0, len(closes) - 20):
        window = closes[start: start + 21]
        v = _annualized_vol_20d(window)
        if v is not None:
            vol_history.append(v)

    current_vol = _annualized_vol_20d(closes)
    if current_vol is not None and vol_history:
        vol_pct = 100.0 * sum(1 for v in vol_history if v <= current_vol) / len(vol_history)
        if vol_pct < 33.0:
            vol_regime = "low"
        elif vol_pct < 67.0:
            vol_regime = "moderate"
        else:
            vol_regime = "high"
    else:
        vol_regime = "moderate"

    return TickerSignals(
        ticker=ticker,
        iv_rank=ivr,
        iv_percentile=ivp,
        sector_relative=sector_relative,
        vol_regime=vol_regime,
        sector_etf=sector_etf,
    )
