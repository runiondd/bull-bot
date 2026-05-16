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
    (naked short call, naked short shares, or any multi-leg combo without a
    matching shape rule below). validate_structure_sanity (C.3) rejects
    nonsense at LLM output time, so this fallback only fires if a multi-leg
    structure slipped through validation — which means we should refuse to
    size it.
    """
    if len(legs) == 1:
        return _single_leg_max_loss(legs[0], spot=spot)
    return _multi_leg_max_loss(legs, spot=spot)


def _multi_leg_max_loss(legs: list[OptionLeg], *, spot: float) -> float:
    if _is_vertical_debit_spread(legs):
        return _vertical_debit_max_loss(legs)
    if _is_vertical_credit_spread(legs):
        return _vertical_credit_max_loss(legs)
    if _is_iron_condor(legs):
        return _iron_condor_max_loss(legs)
    if _is_long_butterfly(legs):
        return _long_butterfly_max_loss(legs)
    if _is_covered_call(legs):
        return _covered_call_max_loss(legs, spot=spot)
    return math.inf


def _is_vertical_debit_spread(legs: list[OptionLeg]) -> bool:
    if len(legs) != 2:
        return False
    if any(l.kind == "share" for l in legs):
        return False
    if legs[0].kind != legs[1].kind:
        return False
    if {l.action for l in legs} != {"buy", "sell"}:
        return False
    if legs[0].expiry != legs[1].expiry:
        return False
    buy = next(l for l in legs if l.action == "buy")
    sell = next(l for l in legs if l.action == "sell")
    # Bull call: long lower strike. Bear put: long higher strike.
    if legs[0].kind == "call":
        return buy.strike < sell.strike and (
            buy.entry_price * buy.qty > sell.entry_price * sell.qty
        )
    return buy.strike > sell.strike and (
        buy.entry_price * buy.qty > sell.entry_price * sell.qty
    )


def _vertical_debit_max_loss(legs: list[OptionLeg]) -> float:
    buy = next(l for l in legs if l.action == "buy")
    sell = next(l for l in legs if l.action == "sell")
    # Per-contract net debit in price units. Multiplied by CONTRACT_MULTIPLIER
    # (100) and then by contract qty to get dollar max-loss.
    net_debit_per_contract = buy.entry_price - sell.entry_price
    qty = min(buy.qty, sell.qty)
    return net_debit_per_contract * CONTRACT_MULTIPLIER * qty


def _is_vertical_credit_spread(legs: list[OptionLeg]) -> bool:
    if len(legs) != 2:
        return False
    if any(l.kind == "share" for l in legs):
        return False
    if legs[0].kind != legs[1].kind:
        return False
    if {l.action for l in legs} != {"buy", "sell"}:
        return False
    if legs[0].expiry != legs[1].expiry:
        return False
    buy = next(l for l in legs if l.action == "buy")
    sell = next(l for l in legs if l.action == "sell")
    # Bull put credit: short higher strike. Bear call credit: short lower strike.
    if legs[0].kind == "put":
        return sell.strike > buy.strike and (
            sell.entry_price * sell.qty > buy.entry_price * buy.qty
        )
    # call credit
    return sell.strike < buy.strike and (
        sell.entry_price * sell.qty > buy.entry_price * buy.qty
    )


def _vertical_credit_max_loss(legs: list[OptionLeg]) -> float:
    buy = next(l for l in legs if l.action == "buy")
    sell = next(l for l in legs if l.action == "sell")
    width = abs(buy.strike - sell.strike)
    credit_per_contract = sell.entry_price - buy.entry_price
    qty = min(buy.qty, sell.qty)
    return (width - credit_per_contract) * CONTRACT_MULTIPLIER * qty


def _is_iron_condor(legs: list[OptionLeg]) -> bool:
    if len(legs) != 4:
        return False
    if any(l.kind == "share" for l in legs):
        return False
    calls = [l for l in legs if l.kind == "call"]
    puts = [l for l in legs if l.kind == "put"]
    if len(calls) != 2 or len(puts) != 2:
        return False
    if {l.expiry for l in legs} != {legs[0].expiry}:
        return False
    if {l.action for l in calls} != {"buy", "sell"}:
        return False
    if {l.action for l in puts} != {"buy", "sell"}:
        return False
    return True


def _iron_condor_max_loss(legs: list[OptionLeg]) -> float:
    calls = sorted(
        [l for l in legs if l.kind == "call"], key=lambda l: l.strike,
    )
    puts = sorted(
        [l for l in legs if l.kind == "put"], key=lambda l: l.strike,
    )
    short_call = next(l for l in calls if l.action == "sell")
    long_call = next(l for l in calls if l.action == "buy")
    short_put = next(l for l in puts if l.action == "sell")
    long_put = next(l for l in puts if l.action == "buy")
    call_width = long_call.strike - short_call.strike
    put_width = short_put.strike - long_put.strike
    total_credit = (
        (short_call.entry_price - long_call.entry_price)
        + (short_put.entry_price - long_put.entry_price)
    )
    qty = min(l.qty for l in legs)
    max_wing = max(call_width, put_width)
    return (max_wing - total_credit) * CONTRACT_MULTIPLIER * qty


def _is_long_butterfly(legs: list[OptionLeg]) -> bool:
    if len(legs) != 3:
        return False
    if any(l.kind == "share" for l in legs):
        return False
    if len({l.kind for l in legs}) != 1:
        return False
    if len({l.expiry for l in legs}) != 1:
        return False
    sorted_legs = sorted(legs, key=lambda l: l.strike)
    return (
        sorted_legs[0].action == "buy" and sorted_legs[0].qty == 1
        and sorted_legs[1].action == "sell" and sorted_legs[1].qty == 2
        and sorted_legs[2].action == "buy" and sorted_legs[2].qty == 1
    )


def _long_butterfly_max_loss(legs: list[OptionLeg]) -> float:
    sorted_legs = sorted(legs, key=lambda l: l.strike)
    low, mid, high = sorted_legs
    net_debit = (
        low.entry_price * low.qty
        - mid.entry_price * mid.qty
        + high.entry_price * high.qty
    )
    return net_debit * CONTRACT_MULTIPLIER


def _is_covered_call(legs: list[OptionLeg]) -> bool:
    if len(legs) != 2:
        return False
    shares = [l for l in legs if l.kind == "share"]
    calls = [l for l in legs if l.kind == "call"]
    if len(shares) != 1 or len(calls) != 1:
        return False
    share = shares[0]
    call = calls[0]
    return (
        share.action == "buy"
        and call.action == "sell"
        and share.qty == call.qty * CONTRACT_MULTIPLIER
    )


def _covered_call_max_loss(legs: list[OptionLeg], *, spot: float) -> float:
    share = next(l for l in legs if l.kind == "share")
    call = next(l for l in legs if l.kind == "call")
    share_safety_loss = share.entry_price * share.qty * SHARE_SAFETY_STOP_PCT
    call_credit = call.entry_price * call.qty * CONTRACT_MULTIPLIER
    return max(0.0, share_safety_loss - call_credit)


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
