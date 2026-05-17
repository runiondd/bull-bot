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
