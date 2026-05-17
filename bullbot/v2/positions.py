"""Position / leg primitives for v2 Phase C — vehicle agent.

OptionLeg models a single leg of any atomic options structure. Multi-leg
structures (verticals, ICs, etc.) are represented as list[OptionLeg].

net_basis is non-None only on legs born from an assignment or exercise event
(see record_event in this module and the assignment/exercise paths in
exits.py shipped in C.3). When non-None, P&L and exit-plan targets are
computed against net_basis instead of entry_price.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

VALID_ACTIONS = ("buy", "sell")
VALID_KINDS = ("call", "put", "share")


@dataclass
class OptionLeg:
    action: str
    kind: str
    strike: float | None
    expiry: str | None
    qty: int
    entry_price: float
    net_basis: float | None = None
    id: int | None = None
    position_id: int | None = None
    exit_price: float | None = None

    def __post_init__(self) -> None:
        if self.action not in VALID_ACTIONS:
            raise ValueError(f"action must be one of {VALID_ACTIONS}; got {self.action!r}")
        if self.kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}; got {self.kind!r}")
        if self.kind == "share":
            if self.strike is not None or self.expiry is not None:
                raise ValueError("share leg must have strike=None and expiry=None")
        else:
            if self.strike is None or self.expiry is None:
                raise ValueError("option leg must have non-None strike and expiry")

    def effective_basis(self) -> float:
        """Return net_basis if set, else entry_price.

        Used by exits.py + risk.py whenever a P&L or stop-target needs to be
        computed in basis-aware terms (assigned shares carry net_basis; freshly
        opened shares carry only entry_price).
        """
        return self.net_basis if self.net_basis is not None else self.entry_price


@dataclass
class Position:
    ticker: str
    intent: str
    structure_kind: str
    opened_ts: int
    nearest_leg_expiry_dte: int | None
    legs: list[OptionLeg]
    id: int | None = None
    exit_plan_version: int = 1
    profit_target_price: float | None = None
    stop_price: float | None = None
    time_stop_dte: int | None = None
    assignment_acceptable: bool = False
    exit_plan_extra_json: str | None = None
    closed_ts: int | None = None
    close_reason: str | None = None
    linked_position_id: int | None = None
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.intent not in ("trade", "accumulate"):
            raise ValueError(
                f"intent must be 'trade' or 'accumulate'; got {self.intent!r}"
            )


def open_position(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    intent: str,
    structure_kind: str,
    legs: list[OptionLeg],
    opened_ts: int,
    profit_target_price: float | None,
    stop_price: float | None,
    time_stop_dte: int | None,
    assignment_acceptable: bool,
    nearest_leg_expiry_dte: int | None,
    rationale: str,
    linked_position_id: int | None = None,
    exit_plan_extra_json: str | None = None,
) -> Position:
    """Insert a new position and its legs in one transaction. Returns the
    Position with `id` populated on it and on each leg."""
    if not legs:
        raise ValueError("at least one leg required")

    cur = conn.execute(
        "INSERT INTO v2_positions "
        "(ticker, intent, structure_kind, exit_plan_version, "
        "profit_target_price, stop_price, time_stop_dte, "
        "assignment_acceptable, nearest_leg_expiry_dte, exit_plan_extra_json, "
        "opened_ts, linked_position_id, rationale) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticker, intent, structure_kind,
            profit_target_price, stop_price, time_stop_dte,
            1 if assignment_acceptable else 0,
            nearest_leg_expiry_dte, exit_plan_extra_json,
            opened_ts, linked_position_id, rationale,
        ),
    )
    pid = cur.lastrowid
    for leg in legs:
        leg_cur = conn.execute(
            "INSERT INTO v2_position_legs "
            "(position_id, action, kind, strike, expiry, qty, entry_price, net_basis) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pid, leg.action, leg.kind, leg.strike, leg.expiry,
                leg.qty, leg.entry_price, leg.net_basis,
            ),
        )
        leg.id = leg_cur.lastrowid
        leg.position_id = pid
    conn.commit()

    return Position(
        id=pid, ticker=ticker, intent=intent, structure_kind=structure_kind,
        opened_ts=opened_ts, nearest_leg_expiry_dte=nearest_leg_expiry_dte,
        legs=legs, profit_target_price=profit_target_price, stop_price=stop_price,
        time_stop_dte=time_stop_dte, assignment_acceptable=assignment_acceptable,
        exit_plan_extra_json=exit_plan_extra_json,
        linked_position_id=linked_position_id, rationale=rationale,
    )


def load_position(conn: sqlite3.Connection, position_id: int) -> Position | None:
    """Read a position + its legs back. Returns None if no such position."""
    row = conn.execute(
        "SELECT * FROM v2_positions WHERE id=?", (position_id,)
    ).fetchone()
    if row is None:
        return None
    leg_rows = conn.execute(
        "SELECT * FROM v2_position_legs WHERE position_id=? ORDER BY id",
        (position_id,),
    ).fetchall()
    legs = [
        OptionLeg(
            id=lr["id"],
            position_id=lr["position_id"],
            action=lr["action"],
            kind=lr["kind"],
            strike=lr["strike"],
            expiry=lr["expiry"],
            qty=lr["qty"],
            entry_price=lr["entry_price"],
            net_basis=lr["net_basis"],
            exit_price=lr["exit_price"],
        )
        for lr in leg_rows
    ]
    return Position(
        id=row["id"],
        ticker=row["ticker"],
        intent=row["intent"],
        structure_kind=row["structure_kind"],
        exit_plan_version=row["exit_plan_version"],
        profit_target_price=row["profit_target_price"],
        stop_price=row["stop_price"],
        time_stop_dte=row["time_stop_dte"],
        assignment_acceptable=bool(row["assignment_acceptable"]) if row["assignment_acceptable"] is not None else False,
        nearest_leg_expiry_dte=row["nearest_leg_expiry_dte"],
        exit_plan_extra_json=row["exit_plan_extra_json"],
        opened_ts=row["opened_ts"],
        closed_ts=row["closed_ts"],
        close_reason=row["close_reason"],
        linked_position_id=row["linked_position_id"],
        rationale=row["rationale"] or "",
        legs=legs,
    )


VALID_CLOSE_REASONS = (
    "profit_target", "stop", "time_stop", "signal_flip",
    "credit_profit_take", "assigned", "called_away", "exercised",
    "expired_worthless", "safety_stop", "manual",
)


def open_for_ticker(conn: sqlite3.Connection, ticker: str) -> Position | None:
    """Return the single open position for this ticker, or None.

    Phase C invariant: at most one open position per ticker at a time.
    """
    row = conn.execute(
        "SELECT id FROM v2_positions "
        "WHERE ticker=? AND closed_ts IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if row is None:
        return None
    return load_position(conn, row["id"])


def open_count(conn: sqlite3.Connection) -> int:
    """Count positions that are currently open (closed_ts IS NULL)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM v2_positions WHERE closed_ts IS NULL"
    ).fetchone()
    return int(row["n"])


def close_position(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    closed_ts: int,
    close_reason: str,
    leg_exit_prices: dict[int, float],
) -> None:
    """Mark a position closed with the given reason and per-leg exit prices.

    leg_exit_prices maps leg.id -> per-contract (or per-share) exit price.
    Realized P&L computation lives in risk.py / runner_c.py — this helper only
    persists the exit fields.
    """
    if close_reason not in VALID_CLOSE_REASONS:
        raise ValueError(
            f"close_reason must be one of {VALID_CLOSE_REASONS}; got {close_reason!r}"
        )
    conn.execute(
        "UPDATE v2_positions SET closed_ts=?, close_reason=? WHERE id=?",
        (closed_ts, close_reason, position_id),
    )
    for leg_id, exit_price in leg_exit_prices.items():
        conn.execute(
            "UPDATE v2_position_legs SET exit_price=? WHERE id=?",
            (exit_price, leg_id),
        )
    conn.commit()


VALID_EVENT_KINDS = (
    "assigned", "called_away", "exercised", "expired_worthless",
)


def record_event(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    event_kind: str,
    occurred_ts: int,
    source_leg_id: int | None,
    linked_position_id: int | None,
    original_credit_per_contract: float | None,
    notes: str | None,
) -> int:
    """Insert a v2_position_events row. Returns the event id.

    The trigger conditions for these events live in exits.py (C.3). This
    helper only persists the row when something else asks it to.
    """
    if event_kind not in VALID_EVENT_KINDS:
        raise ValueError(
            f"event_kind must be one of {VALID_EVENT_KINDS}; got {event_kind!r}"
        )
    cur = conn.execute(
        "INSERT INTO v2_position_events "
        "(position_id, linked_position_id, event_kind, occurred_ts, "
        "source_leg_id, original_credit_per_contract, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            position_id, linked_position_id, event_kind, occurred_ts,
            source_leg_id, original_credit_per_contract, notes,
        ),
    )
    conn.commit()
    return cur.lastrowid


def assign_csp_to_shares(
    conn: sqlite3.Connection,
    *,
    csp_position: Position,
    csp_leg_id: int,
    original_credit_per_contract: float,
    occurred_ts: int,
    intent: str,
    profit_target_price: float | None,
    stop_price: float | None,
    time_stop_dte: int | None,
    nearest_leg_expiry_dte: int | None,
    rationale: str,
) -> Position:
    """Simulate CSP assignment: close the CSP, open a linked long-shares
    position with the basis-adjusted net_basis, and record the event.

    Per Grok review Tier 1 Finding 1:
        net_basis = strike - (original_credit_per_contract / 100)
        share_qty = csp_qty * 100

    The post-assignment exit plan (intent, target, stop, dte) is supplied by
    the caller — derivation from the current signal lives in
    exits.compute_post_assignment_exit_plan in C.3.
    """
    csp_leg = next((leg for leg in csp_position.legs if leg.id == csp_leg_id), None)
    if csp_leg is None or csp_leg.kind != "put" or csp_leg.action != "sell":
        raise ValueError("csp_leg_id must reference a short-put leg on csp_position")
    strike = csp_leg.strike
    csp_qty = csp_leg.qty

    net_basis = strike - (original_credit_per_contract / 100.0)
    share_qty = csp_qty * 100

    share_leg = OptionLeg(
        action="buy", kind="share", strike=None, expiry=None,
        qty=share_qty, entry_price=strike, net_basis=net_basis,
    )
    shares_pos = open_position(
        conn,
        ticker=csp_position.ticker,
        intent=intent,
        structure_kind="long_shares",
        legs=[share_leg],
        opened_ts=occurred_ts,
        profit_target_price=profit_target_price,
        stop_price=stop_price,
        time_stop_dte=time_stop_dte,
        assignment_acceptable=False,
        nearest_leg_expiry_dte=nearest_leg_expiry_dte,
        rationale=rationale,
        linked_position_id=csp_position.id,
    )

    close_position(
        conn,
        position_id=csp_position.id,
        closed_ts=occurred_ts,
        close_reason="assigned",
        leg_exit_prices={csp_leg_id: 0.0},
    )

    record_event(
        conn,
        position_id=csp_position.id,
        event_kind="assigned",
        occurred_ts=occurred_ts,
        source_leg_id=csp_leg_id,
        linked_position_id=shares_pos.id,
        original_credit_per_contract=original_credit_per_contract,
        notes=None,
    )

    return shares_pos
