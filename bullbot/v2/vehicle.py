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

from bullbot.v2.positions import Position
from bullbot.v2.signals import DirectionalSignal

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


def _atr_14(bars: list) -> float:
    """Average True Range over the trailing 14 bars. Returns 0.0 when <15 bars."""
    if len(bars) < ATR_WINDOW + 1:
        return 0.0
    recent = bars[-(ATR_WINDOW + 1):]
    trs: list[float] = []
    for i, b in enumerate(recent):
        if i == 0:
            continue
        prev_close = recent[i - 1].close
        trs.append(max(
            b.high - b.low,
            abs(b.high - prev_close),
            abs(b.low - prev_close),
        ))
    return sum(trs) / ATR_WINDOW


def _rsi_14(bars: list) -> float:
    """Relative Strength Index (14-period, simple moving average of gains/losses).
    Returns 50.0 (neutral) when fewer than 15 bars exist."""
    if len(bars) < 15:
        return 50.0
    closes = [b.close for b in bars[-15:]]
    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-delta)
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _dist_from_20sma_pct(bars: list, *, spot: float) -> float:
    """(spot - SMA_20) / SMA_20. Returns 0.0 when fewer than 20 bars exist
    or when SMA_20 is non-positive."""
    if len(bars) < 20:
        return 0.0
    sma_20 = sum(b.close for b in bars[-20:]) / 20
    if sma_20 <= 0:
        return 0.0
    return (spot - sma_20) / sma_20


LEVELS_BAND_PCT = 0.05


def _structure_levels_for_llm(levels: list, *, spot: float) -> dict:
    """Restructure flat list of Level objects into the design-spec shape:
    {nearest_resistance, nearest_support, all_levels_within_5pct}.

    nearest_resistance = level with smallest (price - spot) where price > spot.
    nearest_support    = level with smallest (spot - price) where price < spot.
    all_levels_within_5pct = filtered list of dicts (price/kind/strength).

    Any of the three can be None / [] when there's no qualifying level.
    """
    above = [lvl for lvl in levels if lvl.price > spot]
    below = [lvl for lvl in levels if lvl.price < spot]
    nearest_resistance = min(above, key=lambda l: l.price - spot) if above else None
    nearest_support = max(below, key=lambda l: l.price) if below else None
    in_band = [
        {"price": lvl.price, "kind": lvl.kind, "strength": lvl.strength}
        for lvl in levels
        if abs(lvl.price - spot) / spot <= LEVELS_BAND_PCT
    ]
    return {
        "nearest_resistance": (
            {"price": nearest_resistance.price, "kind": nearest_resistance.kind,
             "strength": nearest_resistance.strength}
            if nearest_resistance else None
        ),
        "nearest_support": (
            {"price": nearest_support.price, "kind": nearest_support.kind,
             "strength": nearest_support.strength}
            if nearest_support else None
        ),
        "all_levels_within_5pct": in_band,
    }


from datetime import date as _date

MIN_DTE = 7
MAX_STRIKE_DEVIATION_PCT = 0.25


def _check_expiry_min_dte(expiry: str, today: _date) -> SanityResult | None:
    if expiry is None:
        return SanityResult(ok=False, reason="expiry is required")
    try:
        exp = _date.fromisoformat(expiry)
    except (TypeError, ValueError):
        return SanityResult(ok=False, reason=f"expiry {expiry!r} not ISO YYYY-MM-DD")
    if (exp - today).days < MIN_DTE:
        return SanityResult(ok=False, reason=f"expiry {expiry} too soon (< {MIN_DTE} DTE)")
    return None


def _check_moneyness(strike: float, spot: float) -> SanityResult | None:
    if strike is None:
        return SanityResult(ok=False, reason="strike is required")
    if spot <= 0:
        return SanityResult(ok=False, reason=f"invalid spot {spot}")
    if abs(strike - spot) / spot > MAX_STRIKE_DEVIATION_PCT:
        return SanityResult(
            ok=False,
            reason=f"strike {strike} moneyness > {MAX_STRIKE_DEVIATION_PCT:.0%} from spot {spot}",
        )
    return None


def validate_structure_sanity(
    *,
    legs: list[LegSpec],
    spot: float,
    structure_kind: str,
    today: _date,
) -> SanityResult:
    """Dispatch by structure_kind. Returns SanityResult(ok=False, reason=...)
    on any structural violation (wrong leg count, wrong action/kind, bad strikes
    or expiries, broken ratios). Returns SanityResult(ok=True) on pass.

    Grok review Tier 1 Finding 2 — runs BEFORE any chain lookup or risk math.
    """
    if structure_kind in ("long_call", "long_put"):
        if len(legs) != 1:
            return SanityResult(ok=False, reason=f"{structure_kind} requires exactly 1 leg")
        leg = legs[0]
        expected_kind = "call" if structure_kind == "long_call" else "put"
        if leg.action != "buy" or leg.kind != expected_kind:
            return SanityResult(ok=False, reason=f"{structure_kind} requires buy {expected_kind}")
        bad = _check_expiry_min_dte(leg.expiry, today)
        if bad: return bad
        bad = _check_moneyness(leg.strike, spot)
        if bad: return bad
        return SanityResult(ok=True)

    if structure_kind == "csp":
        if len(legs) != 1:
            return SanityResult(ok=False, reason="csp requires exactly 1 leg")
        leg = legs[0]
        if leg.action != "sell" or leg.kind != "put":
            return SanityResult(ok=False, reason="csp requires sell put")
        bad = _check_expiry_min_dte(leg.expiry, today)
        if bad: return bad
        bad = _check_moneyness(leg.strike, spot)
        if bad: return bad
        return SanityResult(ok=True)

    if structure_kind in ("long_shares", "short_shares"):
        if len(legs) != 1:
            return SanityResult(ok=False, reason=f"{structure_kind} requires exactly 1 leg")
        leg = legs[0]
        expected_action = "buy" if structure_kind == "long_shares" else "sell"
        if leg.action != expected_action or leg.kind != "share":
            return SanityResult(ok=False, reason=f"{structure_kind} requires {expected_action} share")
        if leg.strike is not None or leg.expiry is not None:
            return SanityResult(ok=False, reason=f"{structure_kind} requires strike=None and expiry=None")
        return SanityResult(ok=True)

    if structure_kind == "bull_call_spread":
        if len(legs) != 2:
            return SanityResult(ok=False, reason="bull_call_spread requires 2 legs")
        if any(leg.qty_ratio != 1 for leg in legs):
            return SanityResult(ok=False, reason="bull_call_spread requires qty_ratio 1:1")
        if any(leg.kind != "call" for leg in legs):
            return SanityResult(ok=False, reason="bull_call_spread requires both legs to be calls")
        if {leg.action for leg in legs} != {"buy", "sell"}:
            return SanityResult(ok=False, reason="bull_call_spread requires one buy + one sell")
        if legs[0].expiry != legs[1].expiry:
            return SanityResult(ok=False, reason="bull_call_spread requires matching expiries")
        buy = next(l for l in legs if l.action == "buy")
        sell = next(l for l in legs if l.action == "sell")
        if buy.strike >= sell.strike:
            return SanityResult(
                ok=False,
                reason=f"bull_call_spread requires long strike < short strike (got {buy.strike} >= {sell.strike})",
            )
        bad = _check_expiry_min_dte(buy.expiry, today)
        if bad: return bad
        for leg in legs:
            bad = _check_moneyness(leg.strike, spot)
            if bad: return bad
        return SanityResult(ok=True)

    if structure_kind == "bear_put_spread":
        if len(legs) != 2:
            return SanityResult(ok=False, reason="bear_put_spread requires 2 legs")
        if any(leg.qty_ratio != 1 for leg in legs):
            return SanityResult(ok=False, reason="bear_put_spread requires qty_ratio 1:1")
        if any(leg.kind != "put" for leg in legs):
            return SanityResult(ok=False, reason="bear_put_spread requires both legs to be puts")
        if {leg.action for leg in legs} != {"buy", "sell"}:
            return SanityResult(ok=False, reason="bear_put_spread requires one buy + one sell")
        if legs[0].expiry != legs[1].expiry:
            return SanityResult(ok=False, reason="bear_put_spread requires matching expiries")
        buy = next(l for l in legs if l.action == "buy")
        sell = next(l for l in legs if l.action == "sell")
        if buy.strike <= sell.strike:
            return SanityResult(
                ok=False,
                reason=f"bear_put_spread requires long strike > short strike (got {buy.strike} <= {sell.strike})",
            )
        bad = _check_expiry_min_dte(buy.expiry, today)
        if bad: return bad
        for leg in legs:
            bad = _check_moneyness(leg.strike, spot)
            if bad: return bad
        return SanityResult(ok=True)

    if structure_kind == "iron_condor":
        if len(legs) != 4:
            return SanityResult(ok=False, reason="iron_condor requires 4 legs")
        if any(leg.qty_ratio != 1 for leg in legs):
            return SanityResult(ok=False, reason="iron_condor requires qty_ratio 1:1:1:1")
        if len({leg.expiry for leg in legs}) != 1:
            return SanityResult(ok=False, reason="iron_condor requires all legs same expiry")
        calls = [l for l in legs if l.kind == "call"]
        puts = [l for l in legs if l.kind == "put"]
        if len(calls) != 2 or len(puts) != 2:
            return SanityResult(ok=False, reason="iron_condor requires 2 calls + 2 puts")
        if {l.action for l in calls} != {"buy", "sell"}:
            return SanityResult(ok=False, reason="iron_condor call wing requires 1 buy + 1 sell")
        if {l.action for l in puts} != {"buy", "sell"}:
            return SanityResult(ok=False, reason="iron_condor put wing requires 1 buy + 1 sell")
        short_put = next(l for l in puts if l.action == "sell")
        short_call = next(l for l in calls if l.action == "sell")
        long_put = next(l for l in puts if l.action == "buy")
        long_call = next(l for l in calls if l.action == "buy")
        if long_put.strike >= short_put.strike:
            return SanityResult(
                ok=False,
                reason="iron_condor put wing requires long strike < short strike",
            )
        if short_call.strike >= long_call.strike:
            return SanityResult(
                ok=False,
                reason="iron_condor call wing requires short strike < long strike",
            )
        if short_put.strike >= short_call.strike:
            return SanityResult(
                ok=False,
                reason=f"iron_condor wings overlap: short_put {short_put.strike} >= short_call {short_call.strike}",
            )
        bad = _check_expiry_min_dte(legs[0].expiry, today)
        if bad: return bad
        for leg in legs:
            bad = _check_moneyness(leg.strike, spot)
            if bad: return bad
        return SanityResult(ok=True)

    if structure_kind == "butterfly":
        if len(legs) != 3:
            return SanityResult(ok=False, reason="butterfly requires 3 legs")
        if len({leg.kind for leg in legs}) != 1:
            return SanityResult(ok=False, reason="butterfly requires all legs same kind")
        if len({leg.expiry for leg in legs}) != 1:
            return SanityResult(ok=False, reason="butterfly requires all legs same expiry")
        sorted_legs = sorted(legs, key=lambda l: l.strike)
        low, mid, high = sorted_legs
        if low.action != "buy" or mid.action != "sell" or high.action != "buy":
            return SanityResult(ok=False, reason="butterfly requires buy/sell/buy across low/mid/high strikes")
        if low.qty_ratio != 1 or mid.qty_ratio != 2 or high.qty_ratio != 1:
            return SanityResult(
                ok=False,
                reason=f"butterfly requires qty_ratio 1:2:1 (got {low.qty_ratio}:{mid.qty_ratio}:{high.qty_ratio})",
            )
        low_wing = mid.strike - low.strike
        high_wing = high.strike - mid.strike
        if low_wing <= 0 or high_wing <= 0:
            return SanityResult(ok=False, reason="butterfly wings must be positive")
        wing_diff_pct = abs(low_wing - high_wing) / max(low_wing, high_wing)
        if wing_diff_pct > 0.05:
            return SanityResult(
                ok=False,
                reason=f"butterfly wings too asymmetric ({low_wing} vs {high_wing}, {wing_diff_pct:.1%} diff)",
            )
        bad = _check_expiry_min_dte(legs[0].expiry, today)
        if bad: return bad
        for leg in legs:
            bad = _check_moneyness(leg.strike, spot)
            if bad: return bad
        return SanityResult(ok=True)

    if structure_kind == "covered_call":
        if len(legs) != 2:
            return SanityResult(ok=False, reason="covered_call requires 2 legs")
        shares = [l for l in legs if l.kind == "share"]
        calls = [l for l in legs if l.kind == "call"]
        if len(shares) != 1 or len(calls) != 1:
            return SanityResult(ok=False, reason="covered_call requires 1 share leg + 1 call leg")
        share = shares[0]
        call = calls[0]
        if share.action != "buy":
            return SanityResult(ok=False, reason="covered_call share leg must be buy")
        if call.action != "sell":
            return SanityResult(ok=False, reason="covered_call call leg must be sell")
        if share.qty_ratio != call.qty_ratio * 100:
            return SanityResult(
                ok=False,
                reason=f"covered_call requires share qty_ratio = 100 × call qty_ratio (got {share.qty_ratio} vs {call.qty_ratio * 100})",
            )
        bad = _check_expiry_min_dte(call.expiry, today)
        if bad: return bad
        bad = _check_moneyness(call.strike, spot)
        if bad: return bad
        return SanityResult(ok=True)

    # All known structure_kinds have explicit branches above; this fall-through is for any new shape added without an updated validator.
    return SanityResult(ok=False, reason=f"sanity for {structure_kind} not yet implemented")


from bullbot.v2 import risk
from bullbot.v2.positions import OptionLeg

EARNINGS_WHITELIST = {
    "bull_call_spread", "bear_put_spread", "iron_condor",
    "butterfly", "csp", "covered_call",
}
ACCUMULATE_WHITELIST = {"csp", "long_shares", "covered_call"}


def _spec_to_leg(spec: LegSpec, *, qty: int, entry_price: float) -> OptionLeg:
    """Materialize a LegSpec (LLM output) into an OptionLeg (DB-bound) with
    the actual qty (= spec.qty_ratio × unit_count) and a known entry_price."""
    return OptionLeg(
        action=spec.action, kind=spec.kind,
        strike=spec.strike, expiry=spec.expiry,
        qty=qty, entry_price=entry_price,
    )


def _compute_qty_from_ratios(
    *, legs: list[LegSpec], entry_prices: dict[int, float],
    spot: float, nav: float, per_trade_pct: float,
) -> int:
    """Compute the unit-count multiplier such that the total max-loss of the
    structure (legs scaled by qty_ratio × unit_count) fits the per-trade cap.

    Returns 0 if even a single-unit structure exceeds the cap.
    """
    import math
    unit_legs = []
    for idx, spec in enumerate(legs):
        ep = entry_prices.get(idx, 0.0)
        unit_legs.append(_spec_to_leg(spec, qty=spec.qty_ratio, entry_price=ep))
    unit_loss = risk.compute_max_loss(unit_legs, spot=spot)
    if math.isinf(unit_loss) or unit_loss <= 0:
        return 0
    cap_dollars = nav * per_trade_pct
    return int(cap_dollars // unit_loss)


def validate(
    *,
    decision: VehicleDecision,
    spot: float,
    today: _date,
    nav: float,
    per_trade_pct: float,
    per_ticker_pct: float,
    max_open_positions: int,
    current_ticker_concentration_dollars: float,
    current_open_positions: int,
    earnings_window_active: bool,
    entry_prices: dict[int, float],
) -> ValidationResult:
    """Full validation pipeline. Per design §4.6:
      1. structure sanity
      2. earnings/IV window (vehicle whitelist)
      3. intent <-> structure match
      4. qty sizing via per-trade cap
      5. ticker concentration cap
      6. max open positions cap

    Returns ValidationResult(ok=True, sized_legs=[...]) on success;
    ValidationResult(ok=False, reason="...") on first failure.
    """
    # 1. structure sanity
    sanity = validate_structure_sanity(
        legs=decision.legs, spot=spot,
        structure_kind=decision.structure, today=today,
    )
    if not sanity.ok:
        return ValidationResult(ok=False, reason=f"sanity: {sanity.reason}")

    # 2. earnings / IV window
    if earnings_window_active and decision.structure not in EARNINGS_WHITELIST:
        return ValidationResult(
            ok=False,
            reason=f"earnings_window_active + structure '{decision.structure}' not in whitelist",
        )

    # 3. intent <-> structure match
    if decision.intent == "accumulate" and decision.structure not in ACCUMULATE_WHITELIST:
        return ValidationResult(
            ok=False,
            reason=f"intent=accumulate + structure '{decision.structure}' mismatch",
        )

    # 4. qty sizing via risk caps
    unit_count = _compute_qty_from_ratios(
        legs=decision.legs, entry_prices=entry_prices,
        spot=spot, nav=nav, per_trade_pct=per_trade_pct,
    )
    if unit_count <= 0:
        return ValidationResult(
            ok=False,
            reason="per-trade max-loss cap exceeded (qty rounds to 0)",
        )
    sized_legs = [
        _spec_to_leg(spec, qty=spec.qty_ratio * unit_count, entry_price=entry_prices.get(idx, 0.0))
        for idx, spec in enumerate(decision.legs)
    ]

    # 5. ticker concentration cap
    proposed_loss = risk.compute_max_loss(sized_legs, spot=spot)
    new_concentration = current_ticker_concentration_dollars + proposed_loss
    if new_concentration > nav * per_ticker_pct:
        return ValidationResult(
            ok=False,
            reason=f"ticker concentration ${new_concentration:.0f} > cap ${nav * per_ticker_pct:.0f}",
        )

    # 6. max open positions cap
    if current_open_positions + 1 > max_open_positions:
        return ValidationResult(
            ok=False,
            reason=f"max open positions {max_open_positions} reached",
        )

    return ValidationResult(ok=True, reason=None, sized_legs=sized_legs)


def build_llm_context(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    spot: float,
    signal: DirectionalSignal,
    bars: list,
    levels: list,
    days_to_earnings: int,
    earnings_window_active: bool,
    iv_rank: float,
    budget_per_trade_usd: float,
    asof_ts: int,
    per_ticker_concentration_pct: float,
    open_positions_count: int,
    current_position: Position | None = None,
) -> dict:
    """Assemble the rich JSON input the LLM sees on a flat-ticker pick call.
    Composes from caller-supplied bars / levels / scalars. Issues one SQL
    read via `_near_atm_liquidity`; otherwise no I/O. Computes ATR-14,
    RSI-14, and distance-from-20SMA inline from bars so the LLM gets the
    full design-spec indicator set."""
    current_pos_repr = None
    if current_position is not None:
        current_pos_repr = {
            "structure_kind": current_position.structure_kind,
            "intent": current_position.intent,
            "days_held": (asof_ts - current_position.opened_ts) // 86400,
            "nearest_leg_expiry_dte": current_position.nearest_leg_expiry_dte,
            "profit_target_price": current_position.profit_target_price,
            "stop_price": current_position.stop_price,
        }
    return {
        "ticker": ticker,
        "spot": spot,
        "signal": {
            "direction": signal.direction,
            "confidence": signal.confidence,
            "horizon_days": signal.horizon_days,
        },
        "iv_rank": iv_rank,
        "iv_percentile": iv_rank,  # placeholder: separate calc may diverge later
        "atr_14": _atr_14(bars),
        "rsi_14": _rsi_14(bars),
        "dist_from_20sma_pct": _dist_from_20sma_pct(bars, spot=spot),
        "levels": _structure_levels_for_llm(levels, spot=spot),
        "days_to_earnings": days_to_earnings,
        "earnings_window_active": earnings_window_active,
        "large_move_count_90d": _large_move_count_90d(bars),
        "near_atm_liquidity": _near_atm_liquidity(
            conn, ticker=ticker, asof_ts=asof_ts, spot=spot,
        ),
        "budget_per_trade_usd": budget_per_trade_usd,
        "current_position": current_pos_repr,
        "recent_picks_this_ticker": [],  # populated by C.5 runner from v2_positions history
        "portfolio_state": {
            "open_positions": open_positions_count,
            "ticker_concentration_pct": per_ticker_concentration_pct,
        },
    }
