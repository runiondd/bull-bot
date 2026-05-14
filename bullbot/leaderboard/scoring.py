"""Score-A: annualized return on buying-power-held.

The single comparable metric for ranking strategies on the leaderboard.
Computed as `pnl / max_bp_held` (raw return on capital used) then scaled
by `365 / days_held` to annualize, so a 30-day options spread and a 2-year
equity hold rank on the same axis.
"""
from __future__ import annotations


def compute_score_a(pnl: float, max_bp_held: float, days_held: float) -> float:
    """Annualized return on max buying-power held during the trade.

    Returns 0.0 for non-positive `max_bp_held` (avoids div-by-zero on
    legacy rows where BP wasn't tracked) and for non-positive `days_held`
    (a zero-duration trade can't be annualized).
    """
    if max_bp_held <= 0 or days_held <= 0:
        return 0.0
    raw_return = pnl / max_bp_held
    return raw_return * (365.0 / days_held)
