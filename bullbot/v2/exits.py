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


def _is_bullish_target(*, profit_target_price: float, stop_price: float | None) -> bool:
    """A target ABOVE the stop is a bullish position (we want underlying up)."""
    if stop_price is None:
        return profit_target_price > 0
    return profit_target_price > stop_price


def _check_trade_price_triggers(
    conn: sqlite3.Connection, *, position: Position, spot: float, now_ts: int,
) -> ExitAction | None:
    """Close when underlying tags the stored profit_target_price or stop_price.

    Direction (bullish vs bearish) is inferred from profit_target_price vs
    stop_price (bullish: target > stop; bearish: target < stop). Returns
    None when neither trigger fires or when both prices are unset.
    """
    pt = position.profit_target_price
    sp = position.stop_price
    if pt is None and sp is None:
        return None

    bullish = _is_bullish_target(
        profit_target_price=pt if pt is not None else float("inf"),
        stop_price=sp,
    ) if pt is not None else (sp is not None and spot > sp)

    triggered_kind: str | None = None
    triggered_reason: str = ""

    if pt is not None and bullish and spot >= pt:
        triggered_kind = "closed_profit_target"
        triggered_reason = f"spot {spot:.2f} >= profit_target {pt:.2f}"
    elif pt is not None and (not bullish) and spot <= pt:
        triggered_kind = "closed_profit_target"
        triggered_reason = f"spot {spot:.2f} <= profit_target {pt:.2f}"
    elif sp is not None and bullish and spot <= sp:
        triggered_kind = "closed_stop"
        triggered_reason = f"spot {spot:.2f} <= stop {sp:.2f}"
    elif sp is not None and (not bullish) and spot >= sp:
        triggered_kind = "closed_stop"
        triggered_reason = f"spot {spot:.2f} >= stop {sp:.2f}"

    if triggered_kind is None:
        return None

    close_reason = triggered_kind.removeprefix("closed_")
    leg_exit_prices = {leg.id: spot for leg in position.legs if leg.kind == "share"}
    for leg in position.legs:
        if leg.kind != "share":
            leg_exit_prices[leg.id] = 0.0
    positions.close_position(
        conn,
        position_id=position.id,
        closed_ts=now_ts,
        close_reason=close_reason,
        leg_exit_prices=leg_exit_prices,
    )
    return ExitAction(kind=triggered_kind, reason=triggered_reason)


from bullbot.v2.signals import DirectionalSignal

SIGNAL_FLIP_CONFIDENCE = 0.5
_OPPOSITE_DIRECTION = {"bullish": "bearish", "bearish": "bullish"}


def _check_signal_flip(
    conn: sqlite3.Connection, *, position: Position, signal: DirectionalSignal,
    now_ts: int,
) -> ExitAction | None:
    """Close when the current signal flips to the opposite direction with
    confidence >= SIGNAL_FLIP_CONFIDENCE. chop / no_edge are NOT flips —
    those are weakening signals; we don't churn on them."""
    pt = position.profit_target_price
    sp = position.stop_price
    if pt is None and sp is None:
        return None

    position_direction = "bullish" if _is_bullish_target(
        profit_target_price=pt if pt is not None else float("inf"),
        stop_price=sp,
    ) else "bearish"
    expected_flip = _OPPOSITE_DIRECTION.get(position_direction)
    if expected_flip is None:
        return None
    if signal.direction != expected_flip:
        return None
    if signal.confidence < SIGNAL_FLIP_CONFIDENCE:
        return None

    leg_exit_prices = {leg.id: 0.0 for leg in position.legs}
    positions.close_position(
        conn,
        position_id=position.id,
        closed_ts=now_ts,
        close_reason="signal_flip",
        leg_exit_prices=leg_exit_prices,
    )
    return ExitAction(
        kind="closed_signal_flip",
        reason=f"signal flipped to {signal.direction} @ confidence {signal.confidence:.2f}",
    )


from datetime import date as _date


def _check_time_stop(
    conn: sqlite3.Connection, *, position: Position, today: _date, now_ts: int,
) -> ExitAction | None:
    """Close when the nearest option leg's days-to-expiry <= time_stop_dte.
    No-op for share-only positions or when time_stop_dte is unset."""
    if position.time_stop_dte is None:
        return None
    option_legs = [leg for leg in position.legs if leg.kind in ("call", "put")]
    if not option_legs:
        return None
    nearest_dte = min(
        (_date.fromisoformat(leg.expiry) - today).days
        for leg in option_legs
    )
    if nearest_dte > position.time_stop_dte:
        return None
    leg_exit_prices = {leg.id: 0.0 for leg in position.legs}
    positions.close_position(
        conn,
        position_id=position.id,
        closed_ts=now_ts,
        close_reason="time_stop",
        leg_exit_prices=leg_exit_prices,
    )
    return ExitAction(
        kind="closed_time_stop",
        reason=f"nearest leg DTE {nearest_dte} <= time_stop_dte {position.time_stop_dte}",
    )


from bullbot.v2.positions import OptionLeg

CREDIT_PROFIT_TAKE_PCT = 0.50  # close when remaining premium <= 50% of max credit


def _max_credit_received(legs: list[OptionLeg]) -> float:
    """Per-position net credit in dollars (positive when net seller).
    Returns 0.0 when the structure is net-debit (e.g., long premium)."""
    total = 0.0
    for leg in legs:
        if leg.kind == "share":
            continue
        sign = 1.0 if leg.action == "sell" else -1.0
        total += sign * leg.entry_price * leg.qty * 100
    return max(0.0, total)


def _is_credit_structure(legs: list[OptionLeg]) -> bool:
    """True when the position was opened for net credit (CSP, IC,
    bull-put credit spread, bear-call credit spread)."""
    return _max_credit_received(legs) > 0


def _current_credit_outstanding(
    legs: list[OptionLeg], current_leg_prices: dict[int, float],
) -> float:
    """Dollar value of premium still outstanding (what we'd pay to close).
    Mirrors _max_credit_received but uses current prices instead of entry."""
    total = 0.0
    for leg in legs:
        if leg.kind == "share" or leg.id is None:
            continue
        cur = current_leg_prices.get(leg.id)
        if cur is None:
            continue
        sign = 1.0 if leg.action == "sell" else -1.0
        total += sign * cur * leg.qty * 100
    return max(0.0, total)


def _check_credit_profit_take(
    conn: sqlite3.Connection, *, position: Position,
    current_leg_prices: dict[int, float], now_ts: int,
) -> ExitAction | None:
    """Close credit trade-intent positions when remaining premium <= 50% of
    max credit received. Grok review Tier 2 Finding 6: theta is front-loaded,
    holding credit to zero is greedy + gamma-risky."""
    if position.intent != "trade":
        return None
    if not _is_credit_structure(position.legs):
        return None
    max_credit = _max_credit_received(position.legs)
    remaining = _current_credit_outstanding(position.legs, current_leg_prices)
    if remaining > max_credit * CREDIT_PROFIT_TAKE_PCT:
        return None
    leg_exit_prices = {
        leg.id: current_leg_prices.get(leg.id, 0.0) for leg in position.legs
    }
    positions.close_position(
        conn,
        position_id=position.id,
        closed_ts=now_ts,
        close_reason="credit_profit_take",
        leg_exit_prices=leg_exit_prices,
    )
    return ExitAction(
        kind="closed_credit_profit_take",
        reason=(
            f"remaining premium ${remaining:.2f} <= "
            f"{CREDIT_PROFIT_TAKE_PCT:.0%} of max credit ${max_credit:.2f}"
        ),
    )


ASSIGNMENT_BULLISH_ATR_MULT = 2.0
ASSIGNMENT_BEARISH_ATR_MULT = 1.0
ASSIGNMENT_DEFENSIVE_ATR_MULT = 2.0


@dataclass(frozen=True)
class PostAssignmentPlan:
    """Exit-plan kwargs for a newly-opened linked-shares position born from
    CSP assignment. Field names match positions.assign_csp_to_shares kwargs."""
    intent: str
    profit_target_price: float | None
    stop_price: float | None
    time_stop_dte: int | None
    nearest_leg_expiry_dte: int | None


def compute_post_assignment_exit_plan(
    *,
    signal: DirectionalSignal,
    net_basis: float,
    atr_14: float,
) -> PostAssignmentPlan:
    """Derive exit plan for the newly-opened shares position from the current
    Phase A signal at assignment time. Grok review Tier 2 Finding 8.

    Logic:
      - bullish + confidence >= 0.5 -> accumulate, soft stop -2 ATR
      - bearish + confidence >= 0.5 -> trade, hard stop -1 ATR, no profit target
      - chop / no_edge / low-confidence -> accumulate, defensive stop -2 ATR
    """
    is_confident = signal.confidence >= 0.5
    if signal.direction == "bearish" and is_confident:
        stop = net_basis - ASSIGNMENT_BEARISH_ATR_MULT * atr_14
        return PostAssignmentPlan(
            intent="trade", profit_target_price=None, stop_price=stop,
            time_stop_dte=None, nearest_leg_expiry_dte=None,
        )
    if signal.direction == "bullish" and is_confident:
        stop = net_basis - ASSIGNMENT_BULLISH_ATR_MULT * atr_14
        return PostAssignmentPlan(
            intent="accumulate", profit_target_price=None, stop_price=stop,
            time_stop_dte=None, nearest_leg_expiry_dte=None,
        )
    stop = net_basis - ASSIGNMENT_DEFENSIVE_ATR_MULT * atr_14
    return PostAssignmentPlan(
        intent="accumulate", profit_target_price=None, stop_price=stop,
        time_stop_dte=None, nearest_leg_expiry_dte=None,
    )


def _check_accumulate_at_expiry(
    conn: sqlite3.Connection, *, position: Position, signal: DirectionalSignal,
    spot: float, atr_14: float, today: _date, now_ts: int,
) -> ExitAction:
    """Route accumulate-intent positions at the nearest leg's expiry.

    Caller (evaluate) is responsible for confirming nearest_leg_expiry == today.
    Returns one of: assigned_to_shares, called_away, exercised_to_shares,
    expired_worthless. Always advances state — either via positions.assign_csp_to_shares,
    record_event + close_position, or close_position alone.
    """
    # CSP ITM at expiry -> assignment
    short_puts = [
        leg for leg in position.legs
        if leg.kind == "put" and leg.action == "sell"
        and _date.fromisoformat(leg.expiry) == today
        and spot < leg.strike
    ]
    if short_puts:
        csp_leg = short_puts[0]
        original_credit = csp_leg.entry_price * 100.0  # per-contract dollars
        net_basis = csp_leg.strike - (original_credit / 100.0)
        plan = compute_post_assignment_exit_plan(
            signal=signal, net_basis=net_basis, atr_14=atr_14,
        )
        shares_pos = positions.assign_csp_to_shares(
            conn,
            csp_position=position,
            csp_leg_id=csp_leg.id,
            original_credit_per_contract=original_credit,
            occurred_ts=now_ts,
            intent=plan.intent,
            profit_target_price=plan.profit_target_price,
            stop_price=plan.stop_price,
            time_stop_dte=plan.time_stop_dte,
            nearest_leg_expiry_dte=plan.nearest_leg_expiry_dte,
            rationale=f"post-assignment, signal={signal.direction} confidence={signal.confidence:.2f}",
        )
        return ExitAction(
            kind="assigned_to_shares",
            reason=f"CSP @ {csp_leg.strike} assigned, net_basis ${net_basis:.2f}",
            linked_position_id=shares_pos.id,
        )

    # Short call ITM (covered call called away)
    short_calls = [
        leg for leg in position.legs
        if leg.kind == "call" and leg.action == "sell"
        and _date.fromisoformat(leg.expiry) == today
        and spot > leg.strike
    ]
    if short_calls:
        cc_leg = short_calls[0]
        positions.record_event(
            conn,
            position_id=position.id,
            event_kind="called_away",
            occurred_ts=now_ts,
            source_leg_id=cc_leg.id,
            linked_position_id=None,
            original_credit_per_contract=None,
            notes=f"spot {spot} > strike {cc_leg.strike}",
        )
        positions.close_position(
            conn,
            position_id=position.id,
            closed_ts=now_ts,
            close_reason="called_away",
            leg_exit_prices={
                cc_leg.id: 0.0,
                **{leg.id: cc_leg.strike for leg in position.legs if leg.kind == "share"},
            },
        )
        return ExitAction(
            kind="called_away",
            reason=f"CC @ {cc_leg.strike} assigned, shares sold at strike",
        )

    # Long call ITM (accumulate intent — exercise into shares)
    long_calls_itm = [
        leg for leg in position.legs
        if leg.kind == "call" and leg.action == "buy"
        and _date.fromisoformat(leg.expiry) == today
        and spot > leg.strike
    ]
    if long_calls_itm:
        call_leg = long_calls_itm[0]
        share_qty = call_leg.qty * 100
        net_basis = call_leg.strike + call_leg.entry_price  # paid premium per share
        share_leg = positions.OptionLeg(
            action="buy", kind="share", strike=None, expiry=None,
            qty=share_qty, entry_price=call_leg.strike, net_basis=net_basis,
        )
        linked = positions.open_position(
            conn,
            ticker=position.ticker, intent="accumulate", structure_kind="long_shares",
            legs=[share_leg], opened_ts=now_ts,
            profit_target_price=None, stop_price=None, time_stop_dte=None,
            assignment_acceptable=False, nearest_leg_expiry_dte=None,
            rationale=f"exercised from long_call @ {call_leg.strike}",
            linked_position_id=position.id,
        )
        positions.record_event(
            conn,
            position_id=position.id,
            event_kind="exercised",
            occurred_ts=now_ts,
            source_leg_id=call_leg.id,
            linked_position_id=linked.id,
            original_credit_per_contract=None,
            notes=f"exercised at strike {call_leg.strike}",
        )
        positions.close_position(
            conn,
            position_id=position.id,
            closed_ts=now_ts,
            close_reason="exercised",
            leg_exit_prices={call_leg.id: 0.0},
        )
        return ExitAction(
            kind="exercised_to_shares",
            reason=f"long call @ {call_leg.strike} exercised, net_basis ${net_basis:.2f}",
            linked_position_id=linked.id,
        )

    # Anything else expiring today (OTM, etc.) -> worthless
    positions.close_position(
        conn,
        position_id=position.id,
        closed_ts=now_ts,
        close_reason="expired_worthless",
        leg_exit_prices={leg.id: 0.0 for leg in position.legs},
    )
    return ExitAction(kind="expired_worthless", reason="OTM at expiry")


def evaluate(
    conn: sqlite3.Connection, *,
    position: Position,
    signal: DirectionalSignal,
    spot: float,
    atr_14: float,
    today: _date,
    asof_ts: int,
    current_leg_prices: dict[int, float],
) -> ExitAction:
    """Run the full exit-rule pipeline for one open Position.

    Order:
      1. Safety-stop (intent-independent, 15%+ adverse).
      2. intent='trade': price triggers -> signal flip -> time stop -> credit profit-take.
      3. intent='accumulate': route at expiry, else hold.

    Returns ExitAction(kind='hold') when nothing fires.
    """
    safety = _check_safety_stop(conn, position=position, spot=spot, now_ts=asof_ts)
    if safety is not None:
        return safety

    if position.intent == "trade":
        for check in (
            lambda: _check_trade_price_triggers(
                conn, position=position, spot=spot, now_ts=asof_ts,
            ),
            lambda: _check_signal_flip(
                conn, position=position, signal=signal, now_ts=asof_ts,
            ),
            lambda: _check_time_stop(
                conn, position=position, today=today, now_ts=asof_ts,
            ),
            lambda: _check_credit_profit_take(
                conn, position=position,
                current_leg_prices=current_leg_prices, now_ts=asof_ts,
            ),
        ):
            result = check()
            if result is not None:
                return result
        return ExitAction(kind="hold")

    # intent == "accumulate"
    option_legs = [leg for leg in position.legs if leg.kind in ("call", "put")]
    if not option_legs:
        return ExitAction(kind="hold")
    nearest_expiry = min(_date.fromisoformat(leg.expiry) for leg in option_legs)
    if nearest_expiry > today:
        return ExitAction(kind="hold")
    return _check_accumulate_at_expiry(
        conn, position=position, signal=signal, spot=spot, atr_14=atr_14,
        today=today, now_ts=asof_ts,
    )
