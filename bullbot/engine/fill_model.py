"""
Options fill simulator.

Convention: open fills return `net_cash_flow` where NEGATIVE means credit
(we received money) and POSITIVE means debit (we paid). This is the same
convention as brokerage order tickets.

Short legs fill at `mid - 0.01`, long legs at `mid + 0.01`. This is "mid
worse-by-one-tick" and bakes in both half-spread and standard slippage in
a single conservative number.
"""

from __future__ import annotations

from typing import Any

from bullbot import config
from bullbot.data.schemas import Leg


TICK = 0.01


class FillRejected(Exception):
    """Raised when a leg cannot fill (zero liquidity or too-wide spread)."""


def _validate_chain_row(row: dict[str, Any]) -> tuple[float, float]:
    bid = float(row.get("nbbo_bid") or 0)
    ask = float(row.get("nbbo_ask") or 0)
    if bid <= 0 or ask <= 0:
        raise FillRejected(f"zero liquidity: bid={bid} ask={ask}")
    if ask <= bid:
        raise FillRejected(f"inverted spread: bid={bid} ask={ask}")
    mid = (bid + ask) / 2
    if mid == 0 or (ask - bid) / mid > config.MIN_SPREAD_FRAC:
        raise FillRejected(
            f"spread too wide: {(ask - bid):.3f} > {config.MIN_SPREAD_FRAC} * mid {mid:.3f}"
        )
    return bid, ask


def simulate_leg_open(leg: Leg, chain_row: dict[str, Any]) -> float:
    """Return the per-contract fill price for opening `leg`."""
    bid, ask = _validate_chain_row(chain_row)
    mid = (bid + ask) / 2
    if leg.side == "short":
        return mid - TICK
    return mid + TICK


def simulate_leg_close(leg: Leg, chain_row: dict[str, Any]) -> float:
    """Return the per-contract fill price for closing `leg` (opposite side)."""
    bid, ask = _validate_chain_row(chain_row)
    mid = (bid + ask) / 2
    if leg.side == "short":
        return mid + TICK
    return mid - TICK


def commission(contracts: int, n_legs: int) -> float:
    """Total commission for a multi-leg order."""
    return contracts * n_legs * config.COMMISSION_PER_CONTRACT_USD


def simulate_open_multi_leg(
    legs: list[Leg],
    chain_rows: dict[str, dict[str, Any]],
    contracts: int,
) -> tuple[float, list[dict[str, Any]]]:
    """
    Simulate opening a multi-leg order.

    Returns (net_cash_flow, filled_legs) where:
    - net_cash_flow is NEGATIVE for credit received, POSITIVE for debit paid
    - filled_legs is a list of dicts [{option_symbol, side, qty, fill_price}]

    Raises FillRejected if any leg can't fill.
    """
    net = 0.0
    filled: list[dict[str, Any]] = []
    for leg in legs:
        row = chain_rows.get(leg.option_symbol)
        if row is None:
            raise FillRejected(f"no chain data for {leg.option_symbol}")
        price = simulate_leg_open(leg, row)
        qty = leg.quantity * contracts
        sign = 1 if leg.side == "long" else -1
        net += sign * price * qty
        filled.append(
            {
                "option_symbol": leg.option_symbol,
                "side": leg.side,
                "qty": qty,
                "fill_price": price,
            }
        )
    # 100× multiplier: options quoted per share, traded per contract (100 shares)
    return net * 100, filled


def simulate_close_multi_leg(
    legs: list[Leg],
    chain_rows: dict[str, dict[str, Any]],
    contracts: int,
) -> tuple[float, list[dict[str, Any]]]:
    """Close a multi-leg position. Same conventions as open but opposite sides."""
    net = 0.0
    filled: list[dict[str, Any]] = []
    for leg in legs:
        row = chain_rows.get(leg.option_symbol)
        if row is None:
            raise FillRejected(f"no chain data for {leg.option_symbol}")
        price = simulate_leg_close(leg, row)
        qty = leg.quantity * contracts
        sign = 1 if leg.side == "short" else -1
        net += sign * price * qty
        filled.append(
            {
                "option_symbol": leg.option_symbol,
                "side": leg.side,
                "qty": qty,
                "fill_price": price,
            }
        )
    return net * 100, filled


def mark_position(
    legs: list[Leg],
    chain_rows: dict[str, dict[str, Any]],
    contracts: int,
) -> float:
    """Mark-to-market a position using the current mid (no slippage)."""
    total = 0.0
    for leg in legs:
        row = chain_rows.get(leg.option_symbol)
        if row is None:
            continue
        bid = float(row.get("nbbo_bid") or 0)
        ask = float(row.get("nbbo_ask") or 0)
        if bid <= 0 or ask <= 0:
            continue
        mid = (bid + ask) / 2
        qty = leg.quantity * contracts
        sign = 1 if leg.side == "long" else -1
        total += sign * mid * qty
    return total * 100
