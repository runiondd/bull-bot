"""Deterministic risk math for v2 Phase C.

Two responsibilities:
1. compute_max_loss(legs, spot) — worst-case dollar loss for any list[OptionLeg]
   atomic structure, used by the validation pipeline (vehicle.validate) and
   by size_position to compute qty.
2. Three hard caps + cap-evaluation helpers, fed from config and called by
   vehicle.validate before persisting a position.

Multipliers: options are quoted per-share but contract size is 100. Premium of
$2.50 on 1 contract = $250 cash. Shares are 1:1.

The safety-stop max loss for outright share legs uses SHARE_SAFETY_STOP_PCT
(default 15%) — the same number used by exits.evaluate's safety net (design
§4.7). When config raises that cap, both this module and exits.evaluate
read the new value.
"""
from __future__ import annotations

import math

from bullbot.v2.positions import OptionLeg

CONTRACT_MULTIPLIER = 100
SHARE_SAFETY_STOP_PCT = 0.15


def compute_max_loss(legs: list[OptionLeg], *, spot: float) -> float:
    """Worst-case dollar loss of holding `legs` to expiry (or to safety-stop
    for share legs).

    Returns float('inf') for structures with theoretically unbounded loss
    (naked short call, naked short shares with no defined stop).

    spot is used for share-leg safety-stop sizing and for matching credit
    legs against intrinsic-value at strike crosses.
    """
    if len(legs) == 1:
        return _single_leg_max_loss(legs[0], spot=spot)
    raise NotImplementedError(
        "multi-leg max_loss arrives in Task 7"
    )


def _single_leg_max_loss(leg: OptionLeg, *, spot: float) -> float:
    if leg.kind == "share":
        # 15% safety-stop on share entry (design §4.7). Same for long and
        # short — short shares have unbounded upside risk but the safety
        # stop caps it for sizing purposes.
        return leg.entry_price * leg.qty * SHARE_SAFETY_STOP_PCT
    # Option legs
    premium_dollars = leg.entry_price * leg.qty * CONTRACT_MULTIPLIER
    if leg.action == "buy":
        # Long premium — max loss is the premium paid.
        return premium_dollars
    # action == "sell"
    if leg.kind == "put":
        # Naked short put (CSP). Max loss = (strike − credit) × 100 × qty
        # — the price you'd pay if the stock went to zero, net of the credit.
        return max(0.0, (leg.strike - leg.entry_price) * CONTRACT_MULTIPLIER * leg.qty)
    if leg.kind == "call":
        # Naked short call — theoretically unbounded.
        return math.inf
    return math.inf
