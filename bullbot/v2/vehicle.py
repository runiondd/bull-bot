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


LARGE_MOVE_RETURN_PCT = 0.03
LARGE_MOVE_TR_MULT = 3.0
LARGE_MOVE_LOOKBACK = 90
ATR_WINDOW = 14


def _large_move_count_90d(bars: list) -> int:
    """Count of bars in the trailing 90 with |return| >= 3% OR TR >= 3 × ATR_14.
    Returns 0 when fewer than ATR_WINDOW bars exist."""
    if len(bars) < ATR_WINDOW + 1:
        return 0
    recent = bars[-LARGE_MOVE_LOOKBACK:]
    # Compute true range per bar (need prev close)
    trs: list[float] = []
    for i, b in enumerate(recent):
        if i == 0:
            trs.append(b.high - b.low)
            continue
        prev_close = recent[i - 1].close
        trs.append(max(
            b.high - b.low,
            abs(b.high - prev_close),
            abs(b.low - prev_close),
        ))
    atr_14 = sum(trs[-ATR_WINDOW:]) / ATR_WINDOW
    if atr_14 <= 0:
        atr_14 = float("inf")  # disable the TR rule when baseline volatility is zero

    count = 0
    for i, b in enumerate(recent):
        if i == 0:
            continue
        prev_close = recent[i - 1].close
        ret = abs(b.close - prev_close) / prev_close if prev_close > 0 else 0.0
        if ret >= LARGE_MOVE_RETURN_PCT or trs[i] >= LARGE_MOVE_TR_MULT * atr_14:
            count += 1
    return count


def _near_atm_liquidity(
    conn: sqlite3.Connection, *, ticker: str, asof_ts: int, spot: float,
) -> dict:
    """For all v2_chain_snapshots rows at (ticker, asof_ts) with strike within
    ATM ±5%: sum oi, compute mean bid-ask spread as % of mid, return nearest expiry.

    Empty dict-style result with zeros / None when no data."""
    lo = spot * (1 - ATM_BAND_PCT)
    hi = spot * (1 + ATM_BAND_PCT)
    rows = conn.execute(
        "SELECT expiry, bid, ask, oi FROM v2_chain_snapshots "
        "WHERE ticker=? AND asof_ts=? AND strike BETWEEN ? AND ?",
        (ticker, asof_ts, lo, hi),
    ).fetchall()
    if not rows:
        return {
            "total_oi_within_5pct": 0,
            "spread_avg_pct": None,
            "nearest_expiry": None,
        }
    total_oi = sum(int(r["oi"] or 0) for r in rows)
    spreads = []
    for r in rows:
        if r["bid"] is None or r["ask"] is None:
            continue
        mid = (r["bid"] + r["ask"]) / 2
        if mid <= 0:
            continue
        spreads.append((r["ask"] - r["bid"]) / mid)
    spread_avg = sum(spreads) / len(spreads) if spreads else None
    nearest_expiry = min(r["expiry"] for r in rows)
    return {
        "total_oi_within_5pct": total_oi,
        "spread_avg_pct": spread_avg,
        "nearest_expiry": nearest_expiry,
    }
