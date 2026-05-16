"""Position / leg primitives for v2 Phase C — vehicle agent.

OptionLeg models a single leg of any atomic options structure. Multi-leg
structures (verticals, ICs, etc.) are represented as list[OptionLeg].

net_basis is non-None only on legs born from an assignment or exercise event
(see record_event in this module and the assignment/exercise paths in
exits.py shipped in C.3). When non-None, P&L and exit-plan targets are
computed against net_basis instead of entry_price.
"""
from __future__ import annotations

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
