"""Deterministic exit-rule evaluator for v2 Phase C.

Public entry: evaluate(conn, position, signal, spot, atr_14, today, asof_ts).
Routes by Position.intent ('trade' vs 'accumulate') and returns an ExitAction
describing what (if anything) happened. For accumulate-intent positions whose
nearest leg expires today, may also invoke positions.assign_csp_to_shares or
positions.record_event to advance the wheel state machine.

All P&L and stop math uses OptionLeg.effective_basis() (Grok review Tier 1
Finding 1) so positions born from assignment compare against net_basis, not
the raw strike.
"""
from __future__ import annotations

from dataclasses import dataclass

ACTION_KINDS = (
    "hold",
    # trade-intent exits
    "closed_profit_target",
    "closed_stop",
    "closed_signal_flip",
    "closed_time_stop",
    "closed_credit_profit_take",
    "closed_safety_stop",
    # accumulate-intent at-expiry transitions
    "assigned_to_shares",
    "called_away",
    "exercised_to_shares",
    "expired_worthless",
)


@dataclass(frozen=True)
class ExitAction:
    kind: str
    reason: str = ""
    linked_position_id: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise ValueError(f"kind must be one of {ACTION_KINDS}; got {self.kind!r}")


from bullbot.v2.positions import Position


def _position_pnl_pct(*, position: Position, spot: float) -> float:
    """Net-basis-aware unrealized P&L percent for a share-only position.

    Returns 0.0 for option-only or multi-leg positions — those are handled
    by the intent-specific exit paths, not by the safety-stop.

    For long shares: (spot - basis) / basis.
    For short shares: (basis - spot) / basis.

    `basis` is `OptionLeg.effective_basis()` — net_basis when non-None
    (assigned shares carry net_basis = strike - csp_credit/100), else
    entry_price (Grok review Tier 1 Finding 1).
    """
    share_legs = [leg for leg in position.legs if leg.kind == "share"]
    if not share_legs or len(position.legs) != 1:
        return 0.0
    leg = share_legs[0]
    basis = leg.effective_basis()
    if basis <= 0:
        return 0.0
    if leg.action == "buy":
        return (spot - basis) / basis
    # leg.action == "sell" (short shares)
    return (basis - spot) / basis


import sqlite3

from bullbot.v2 import positions

SAFETY_STOP_PCT = 0.15  # 15% adverse from effective basis


def _check_safety_stop(
    conn: sqlite3.Connection, *, position: Position, spot: float, now_ts: int,
) -> ExitAction | None:
    """Force-close a share-only position whose loss exceeds SAFETY_STOP_PCT
    of effective basis. Returns None when not triggered.

    Independent of intent — even an accumulate position will be liquidated
    on a 15%+ adverse gap. Option-only positions are not subject to this
    rule (risk.py's per-trade cap already bounded their downside at entry).
    """
    pnl_pct = _position_pnl_pct(position=position, spot=spot)
    if pnl_pct == 0.0:
        return None
    if pnl_pct > -SAFETY_STOP_PCT:
        return None
    leg = position.legs[0]
    positions.close_position(
        conn,
        position_id=position.id,
        closed_ts=now_ts,
        close_reason="safety_stop",
        leg_exit_prices={leg.id: spot},
    )
    return ExitAction(
        kind="closed_safety_stop",
        reason=f"pnl {pnl_pct:.1%} exceeds {SAFETY_STOP_PCT:.0%} safety stop",
    )
