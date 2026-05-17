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

import sqlite3
from dataclasses import dataclass, field
from statistics import median

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


ATM_BAND_PCT = 0.05
IV_RANK_LOOKBACK_DAYS = 252
IV_RANK_MIN_HISTORY_DAYS = 30
IV_RANK_DEFAULT = 0.5


def _iv_rank(
    conn: sqlite3.Connection, *, ticker: str, asof_ts: int, spot: float,
) -> float:
    """IV rank in [0.0, 1.0] for `ticker` as of `asof_ts`.

    Method: per-day median IV across ATM ±5% strikes from v2_chain_snapshots,
    over a 252-day trailing window. Today's IV vs (min, max) of the daily
    medians -> rank.

    Returns IV_RANK_DEFAULT (0.5) when fewer than IV_RANK_MIN_HISTORY_DAYS
    of data exist.
    """
    lookback_start_ts = asof_ts - IV_RANK_LOOKBACK_DAYS * 86400
    lo_strike = spot * (1 - ATM_BAND_PCT)
    hi_strike = spot * (1 + ATM_BAND_PCT)

    rows = conn.execute(
        "SELECT asof_ts, iv FROM v2_chain_snapshots "
        "WHERE ticker=? AND asof_ts BETWEEN ? AND ? "
        "AND strike BETWEEN ? AND ? AND iv IS NOT NULL",
        (ticker, lookback_start_ts, asof_ts, lo_strike, hi_strike),
    ).fetchall()

    # Group IVs by asof_ts and take median per day
    by_day: dict[int, list[float]] = {}
    for r in rows:
        by_day.setdefault(r["asof_ts"], []).append(r["iv"])
    daily_medians = sorted(median(ivs) for ivs in by_day.values())

    if len(daily_medians) < IV_RANK_MIN_HISTORY_DAYS:
        return IV_RANK_DEFAULT

    iv_min = daily_medians[0]
    iv_max = daily_medians[-1]
    if iv_max <= iv_min:
        return IV_RANK_DEFAULT

    today_ivs = by_day.get(asof_ts)
    if not today_ivs:
        return IV_RANK_DEFAULT
    today_iv = median(today_ivs)

    return max(0.0, min(1.0, (today_iv - iv_min) / (iv_max - iv_min)))
