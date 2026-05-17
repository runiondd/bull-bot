# Bull-Bot v2 Phase C — Grok Review Response

**Date:** 2026-05-16
**Status:** Grok review incorporated
**Original design:** `2026-05-16-phase-c-vehicle-agent-design.md`
**Reviewer:** Grok (xAI), external review per `2026-05-16-phase-c-vehicle-agent-review-prompt.md`
**Overall verdict from Grok:** approve with changes

This document records, finding by finding, how the external review was incorporated into the design.

---

## Tier 1 — Findings that would invalidate the design (all addressed)

### Finding 1 — Net basis on CSP assignment

> When a CSP is assigned into the linked long-shares position (accumulate intent), the shares entry_price must reflect net basis = strike − (original CSP credit ÷ 100), not the raw strike. This is critical for the MSTR/IBIT wheel thesis. Wrong basis corrupts P&L, exit plans, dashboard, and all backtest attribution.

**Addressed in:**
- **§4.3 schema** — `v2_position_legs` gains a `net_basis` column (nullable, populated only on legs born from assignment/exercise events). New `v2_position_events` table records the assignment/exercise event with original-leg link and the basis adjustment.
- **§4.7 exit-rule evaluator** — Assignment path now: (a) computes `net_basis = strike − (csp_credit_per_contract / 100)`, (b) opens the linked long-shares position with `entry_price = net_basis`, (c) writes a `v2_position_events` row capturing the original CSP credit. Exit-plan target/stop for the new shares position is computed relative to `net_basis`, not the raw assignment strike.
- **§6 testing** — Adds an explicit test case: CSP opened with $2.00 credit, assigned at $100 strike → linked shares position has `entry_price = $98.00`; subsequent profit-target on the linked shares is computed against $98.00.

### Finding 2 — Post-LLM structure sanity validation

> vehicle.validate currently only checks strike existence, risk caps, earnings whitelist, etc. It does NOT verify that the legs the LLM returned actually form a valid instance of the declared structure (e.g. long strike < short strike on a bull call spread, correct ratios on butterfly, no inverted expiries on calendars/diagonals, sensible moneyness).

**Addressed in:**
- **§4.5 vehicle.py** — New explicit responsibility: `validate_structure_sanity(legs, spot, structure_kind) -> SanityResult`. Documented as a pure-Python function called immediately after LLM JSON parse, before risk sizing.
- **§4.6 validation** — Promoted to step 1 in the validation pipeline (runs before strike-existence, max-loss, concentration, etc.). On failure logs `vehicle_invalid_structure` with the raw LLM JSON and skips the ticker that day.
- **§7 phase order** — Now explicitly named as part of C.3 deliverable.
- **§6 testing** — Adds per-structure sanity tests: bull call spread with inverted strikes, butterfly with non-symmetric wings, calendar with reversed expiries, iron condor with overlapping wings, etc.

### Finding 3 — Event-day / jump risk in backtest synthesizer

> The BS synth (realized-vol-30d × VIX regime, ATM±10%, 21-365 DTE) does not inject jumps or vol spikes for non-earnings catalysts (MSTR/IBIT crypto moves, product launches, etc.). Credit structures will look artificially good in backtest.

**Addressed in:**
- **§4.9 backtest harness, synth_chain.py** — New rule explicitly added: detect historical bars with absolute return ≥ 3% OR true range ≥ 3× trailing-20-bar ATR. For those bars, multiply the IV proxy by 1.75× (with a 1.5–2.0× tunable). The bump persists for 5 trading days post-event, decaying linearly to 1.0×. This bounds credit-strategy optimism without any paid data feed.
- **§6 testing** — Adds backtest acceptance test: on at least one ticker with a known historical jump (e.g., MSTR on a crypto-spike day, TSLA on an earnings-adjacent product day), assert synth_chain produces an inflated IV vs the baseline regime calculation. Manual chain snapshots when available become the calibration set.

### Finding 4 — exit_plan_json schema fragility

> exit_plan is an opaque JSON blob with no version column and the four core fields (profit_target_price, stop_price, time_stop_dte, assignment_acceptable) are not real columns. After months of positions this will become painful to query and migrate.

**Addressed in:**
- **§4.3 schema** — `v2_positions` table revised: `profit_target_price`, `stop_price`, `time_stop_dte`, `assignment_acceptable`, and `nearest_leg_expiry_dte` promoted to first-class nullable columns. `exit_plan_extra_json` retained as a versioned escape hatch for future fields. `exit_plan_version INTEGER NOT NULL DEFAULT 1` added.
- `nearest_leg_expiry_dte` is computed at entry from the leg list and stored alongside, so time-based queries don't need to scan legs.
- **§4.7 exit-rule evaluator** — All reads switched from `exit_plan_json["..."]` to direct column reads.

---

## Tier 2 — High-value improvements (all addressed)

### Finding 5 — Missing LLM context: large-move history + liquidity flag

> LLM context is missing "event risk" / large-move history and rough liquidity signal for proposed strikes. Add large_move_count_last_90d (or similar) and a cheap liquidity flag (OI or volume on nearby strikes).

**Addressed:** §4.5 input JSON gains `large_move_count_90d` (count of bars with |return| ≥ 3% OR true range ≥ 3× ATR in trailing 90 bars) and `near_atm_liquidity` (sum of open interest across strikes within ±5% of spot for nearest two monthly expiries). Both computed from data already on hand — no new dependency.

### Finding 6 — Credit-structure profit-take

> For credit structures in "trade" intent, add a professional "close at 50% of max credit" / "close when premium left < 0.10 × width" rule. Theta decay is front-loaded; holding to zero is greedy and gamma-risky.

**Addressed:** §4.7 exit-rule evaluator. For `intent == "trade"` AND structure is a net-credit position (e.g., bear put credit spread, iron condor, CSP held as trade-not-accumulate), a profit-take rule fires when `remaining_premium ≤ 0.50 × max_credit` for verticals/ICs, or `remaining_premium ≤ 0.10 × spread_width` for narrow credit verticals. Close reason: `credit_profit_take`. Documented as deterministic, not LLM-decided.

### Finding 7 — Earnings trigger expanded

> Make the long-premium ban trigger on (days_to_earnings <= 14 OR iv_rank > 0.75) instead of hard 7 days.

**Addressed:** §3 scope summary, §4.6 validation step 5, and §4.5 LLM input ("earnings_window_active": bool). Trigger condition is now `days_to_earnings ≤ 14 OR iv_rank > 0.75`. When active, vehicle whitelist restricted to {bull_call_spread, bear_put_spread, iron_condor, butterfly, csp, covered_call}. Validation failure type: `skipped_earnings_or_high_iv`.

### Finding 8 — Wheel state-machine gap (assigned-shares exit plan)

> After CSP assignment to shares, the new shares position stays in "accumulate" and only exits on called-away. A strong bearish signal while holding the shares does not allow liquidation. Clarify the transition — the assigned shares should probably get a fresh exit_plan derived from the signal at assignment time.

**Addressed:** §4.7 exit-rule evaluator, assignment branch. At assignment time, the newly-opened linked shares position is given a fresh exit plan derived from the current Phase A signal:
- If signal is `bullish` with confidence ≥ 0.5 → stays `accumulate` intent, exit plan = "hold until called away or signal flips bearish".
- If signal is `bearish` with confidence ≥ 0.5 → switches to `trade` intent on the shares with stop set at net_basis − 1 × ATR (forced liquidation path).
- If signal is `chop`/`no_edge` → defaults to `accumulate` with a defensive stop at net_basis − 2 × ATR.

The transition rule is deterministic. Documented as `compute_post_assignment_exit_plan(signal, net_basis, atr) -> ExitPlan`.

---

## Tier 3 — Flagged but not necessarily fixed (acceptance / non-acceptance)

### Cut calendars and diagonals from initial menu — ACCEPTED

> Consider cutting calendars and diagonals from the initial Phase C menu (they are the hardest for the LLM and the most fragile under BS). Keep verticals, IC, butterfly, CSP, CC, long call/put, shares. Add the exotics later as C.7.

**Accepted.** Removed from §3 scope and from the §4.5 LLM output `structure` enum. Phase C menu is now: long_call, long_put, bull_call_spread, bear_put_spread, iron_condor, butterfly, covered_call, csp, long_shares, short_shares. **§7 phase order** gains a new sub-step:
- **C.7 (deferred follow-up):** Add calendar + diagonal vehicles. Requires a multi-expiry chain handling pass and validate_structure_sanity rules for cross-expiry legs. Out of Phase C scope.

### Honest deferrals on dividends + per-direction/sector caps — KEPT

> The honesty about "no dividend ex-date modeling" and "no per-direction/sector caps" is correct — keep them explicitly as accepted Phase C gaps.

**Kept.** §8 open questions unchanged on these two items.

### File layout fine — NO CHANGE

**No change.**

---

## Things Grok got right (endorsements)

Grok's endorsements (recorded for posterity; no doc change):

- Deterministic exits + LLM only at entry decision.
- LLM proposes shape + ratios; risk.py computes qty.
- First-class accumulate + wheel + deep-ITM LEAPS support for MSTR/IBIT.
- Earnings whitelist banning long premium while allowing defined-risk / short premium.
- Same Haiku code path + caching in backtest as in live.
- Yahoo + BS fallback with source tagging on every MtM / chain row.
- Phased C.0–C.6 sub-steps with independent merge points.

---

## Summary of design state

All four Tier 1 findings are now reflected in the main design document. All four Tier 2 improvements are incorporated. One Tier 3 cut (calendars/diagonals → C.7) is applied; the other two Tier 3 items required no doc change.

Design is now ready for implementation planning.
