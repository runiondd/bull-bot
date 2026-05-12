"""
Technical indicators. All pure functions over lists of floats — no I/O,
no classes, no state. Every function returns `None` when insufficient
data rather than raising.
"""

from __future__ import annotations

from statistics import mean, pstdev


def sma(values: list[float], period: int) -> float | None:
    """Simple moving average of the LAST `period` values."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float | None:
    """Exponential moving average (pandas-compatible, adjust=False)."""
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    ema_val = values[0]
    for v in values[1:]:
        ema_val = alpha * v + (1 - alpha) * ema_val
    return ema_val


def rsi(values: list[float], period: int = 14) -> float | None:
    """Wilder's RSI (pandas-compatible: ewm seeded from first bar, alpha=1/period)."""
    if len(values) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    alpha = 1.0 / period
    avg_gain = gains[0]
    avg_loss = losses[0]
    for i in range(1, len(gains)):
        avg_gain = alpha * gains[i] + (1 - alpha) * avg_gain
        avg_loss = alpha * losses[i] + (1 - alpha) * avg_loss
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float | None:
    """Average true range (Wilder smoothing)."""
    if len(highs) < period + 1 or not (len(highs) == len(lows) == len(closes)):
        return None
    trs: list[float] = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    atr_val = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
    return atr_val


def bollinger(
    values: list[float],
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[float, float, float] | None:
    """Returns (upper, middle, lower) over the last `period` values."""
    if len(values) < period:
        return None
    window = values[-period:]
    m = mean(window)
    sd = pstdev(window)
    return (m + num_std * sd, m, m - num_std * sd)


def iv_rank(current_iv: float, history: list[float]) -> float:
    """IV rank: where does current IV sit in [min, max] of history?"""
    if not history:
        return 0.0
    lo = min(history)
    hi = max(history)
    if hi == lo:
        return 50.0
    return 100.0 * (current_iv - lo) / (hi - lo)


def iv_percentile(current_iv: float, history: list[float]) -> float:
    """IV percentile: what fraction of historical IVs were <= current?"""
    if not history:
        return 0.0
    count = sum(1 for h in history if h <= current_iv)
    return 100.0 * count / len(history)


def cagr(equity_curve: list[float], days: int) -> float:
    """Compound annual growth rate over `days` calendar days.

    Guards against two degenerate equity-curve cases that produce non-real
    results from ``a ** b``:

      * ``start <= 0``: undefined growth from zero/negative capital → 0.0
      * ``end   <= 0``: lost everything (or worse) → -1.0 (-100%)

    Without the ``end <= 0`` guard, ``negative ** fractional`` returns a
    Python ``complex``, which then crashes plateau classification with
    ``TypeError: '>=' not supported between instances of 'complex' and 'float'``
    (see iteration_failures id=18, TSLA 2026-04-10).
    """
    if len(equity_curve) < 2 or days <= 0:
        return 0.0
    start, end = equity_curve[0], equity_curve[-1]
    if start <= 0:
        return 0.0
    if end <= 0:
        return -1.0
    years = days / 365.0
    return (end / start) ** (1.0 / years) - 1.0


def sortino(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """Sortino ratio: excess return divided by downside deviation."""
    if len(returns) < 2:
        return 0.0
    excess = [r - risk_free_rate for r in returns]
    mean_excess = sum(excess) / len(excess)
    downside = [min(0.0, e) ** 2 for e in excess]
    downside_dev = (sum(downside) / len(downside)) ** 0.5
    if downside_dev == 0:
        return float("inf") if mean_excess > 0 else 0.0
    return mean_excess / downside_dev
