"""LLM-picked entry-decision agent for v2 Phase C.

Public entry: pick(conn, ticker, signal, spot, ..., client=None) -> VehicleDecision.
Internally:
  1. build_llm_context — assemble the rich JSON input the LLM sees
  2. _call_llm — send to Haiku, get JSON back, parse to VehicleDecision
  3. validate — structure sanity + risk caps + earnings + intent match
  4. _compute_qty_from_ratios — scale LLM's qty_ratios via risk.size_position

The LLM picks SHAPE (structure_kind + leg ratios + strikes + expiries +
exit plan). We compute SIZE (actual contract qty) deterministically via
risk.py — prevents the LLM from rounding up against the risk cap.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DECISIONS = ("open", "pass")
INTENTS = ("trade", "accumulate")

STRUCTURE_KINDS = (
    "long_call", "long_put",
    "bull_call_spread", "bear_put_spread",
    "iron_condor", "butterfly",
    "covered_call", "csp",
    "long_shares", "short_shares",
)
# Note: 'calendar' and 'diagonal' deferred to C.7 (Grok review Tier 3 cut).


@dataclass
class LegSpec:
    """One leg as returned by the LLM — has qty_ratio (relative weight),
    not absolute qty. risk.size_position scales to actual contracts later."""
    action: str            # 'buy' | 'sell'
    kind: str              # 'call' | 'put' | 'share'
    strike: float | None
    expiry: str | None     # 'YYYY-MM-DD' or None for shares
    qty_ratio: int


@dataclass
class VehicleDecision:
    decision: str          # 'open' | 'pass'
    intent: str            # 'trade' | 'accumulate'
    structure: str         # one of STRUCTURE_KINDS
    legs: list[LegSpec]
    exit_plan: dict        # {profit_target_price, stop_price, time_stop_dte, assignment_acceptable}
    rationale: str

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(
                f"decision must be one of {DECISIONS}; got {self.decision!r}"
            )
        if self.intent not in INTENTS:
            raise ValueError(
                f"intent must be one of {INTENTS}; got {self.intent!r}"
            )
        if self.structure not in STRUCTURE_KINDS:
            raise ValueError(
                f"structure must be one of {STRUCTURE_KINDS}; got {self.structure!r}"
            )


@dataclass(frozen=True)
class SanityResult:
    ok: bool
    reason: str | None = None


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None = None
    sized_legs: list = field(default_factory=list)
