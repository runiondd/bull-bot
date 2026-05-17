# Bull-Bot v2 Phase C — Vehicle Agent + Backtest Harness — Design

**Date:** 2026-05-16
**Status:** Reviewed & revised — approved with changes (Grok 2026-05-16)
**Author:** Dan Runion with Claude (Opus 4.7)
**Prior phases shipped:** Phase A (rules-based directional signal), Phase B (paper-share dispatcher)

---

## Review Response Summary

External review complete. Verdict: **approve with changes**. Full finding-by-finding response is in `2026-05-16-phase-c-vehicle-agent-grok-review-response.md`. Four Tier 1 changes are now binding on this design and are integrated below:

1. **Net basis on CSP assignment** (§4.3 schema, §4.7 exit-rule evaluator) — assigned shares store `net_basis = strike − (csp_credit / 100)`; exit plans for the linked shares are computed against `net_basis`, not the raw strike.
2. **Post-LLM structure sanity validation** (§4.5 vehicle.py, §4.6 validation step 1) — `validate_structure_sanity(legs, spot, structure_kind)` runs before risk sizing; rejects inverted strikes, broken ratios, reversed expiries, etc.
3. **Event-day IV bump in backtest synthesizer** (§4.9 synth_chain.py) — on historical bars with |return| ≥ 3% OR true range ≥ 3× ATR, inflate IV proxy by 1.75× decaying linearly back to 1.0× over 5 trading days.
4. **Promote core exit-plan fields to real columns + add version** (§4.3 schema) — `profit_target_price`, `stop_price`, `time_stop_dte`, `assignment_acceptable`, `nearest_leg_expiry_dte`, `exit_plan_version` become first-class columns on `v2_positions`; `exit_plan_extra_json` retained for forward-compatible additions.

Also incorporated: credit-structure profit-take rule, earnings trigger expanded to `(days_to_earnings ≤ 14 OR iv_rank > 0.75)`, post-assignment exit-plan derivation from current signal, LLM context fields for event history and near-ATM liquidity, and calendars/diagonals cut from Phase C menu (deferred to C.7 follow-up).

---

## 1. Goal

Replace v2's current share-only paper trading with an LLM-picked options-and-shares agent. The agent receives a directional signal from Phase A plus a rich technical context bundle, and returns a specific atomic options structure (or shares, or pass) with a defined exit plan. The same daily run also evaluates exit conditions on held positions deterministically. Ships with a backtest harness that replays the agent over historical bars before any new forward trades.

End-state visible result: Dan opens the dashboard and sees, per ticker, today's vehicle pick with rationale, currently held positions with leg-level mark-to-market, exit plans, days held, P&L, and a backtest report showing how this agent's logic would have performed over the last 2 years.

## 2. Why this design

Phase A emits a daily directional signal (bullish/bearish/chop/no_edge + confidence) per UNIVERSE ticker. Phase B turned that into long/short shares with a 10% stop and signal-flip exit. That works as a proof of concept but ignores the actual reason a swing trader uses options: defined-risk exposure, leverage with capped downside, basis-lowering via short premium, and time-decay capture. Phase C makes the vehicle choice explicit.

The vehicle agent is an LLM (Haiku) because the input space — direction + confidence + IV environment + support/resistance proximity + mean-reversion + budget + liquidity + earnings proximity + existing position — is too high-dimensional for a clean deterministic table to remain readable past 50 lines. Haiku is cheap enough (~$0.001/call × ~30 tickers/day = ~$0.03/day) that the cost is negligible compared to the brittleness of a hand-coded selector.

Exit decisions stay deterministic. The trade thesis (target, stop, intent) is set at entry by the agent. The same agent does NOT re-evaluate held positions daily — that's a separate phase. Keeping exits deterministic per the stored exit plan makes backtests reproducible and keeps daily LLM cost bounded.

## 3. Scope

**In scope (Phase C):**
- Generic option-leg primitive: `OptionLeg(action, kind, strike, expiry, qty)` plus `net_basis` for legs born from assignment/exercise. Any in-scope atomic structure is a `list[OptionLeg]`.
- Atomic structure menu: long calls/puts, vertical spreads (bull call, bear put), iron condors, butterflies, covered calls, cash-secured puts, long shares, short shares. (Calendars and diagonals deferred to C.7 — see §7.)
- Wheel-like sequencing across trades: position lifecycle states (open / assigned / exercised / closed) with linked-position chaining (e.g., CSP assigned → covered call against acquired shares → called away → recycle). Assigned-shares legs carry a `net_basis` reflecting the original credit received; all subsequent P&L and exit targets compute against `net_basis`.
- Two trade intents: `trade` (TA-driven, exit on profit-target / stop / time / signal flip / credit profit-take) and `accumulate` (basis-lowering, exit on assignment / exercise / expiry). Positions born from assignment get a fresh exit plan derived from the current Phase A signal at assignment time.
- Post-LLM structure-sanity validation (`validate_structure_sanity`) as the first guardrail in the validation pipeline.
- Support/resistance computation as a pure deterministic Python module — swing pivots + moving-average confluence + round-number snap.
- Yahoo options-chain integration (`yfinance.Ticker.option_chain`) with Black-Scholes fallback when chain is unavailable.
- Three hard risk caps: per-trade max-loss percent of NAV (default 2%), per-ticker concentration percent (default 15%), total open positions count (default 12).
- Earnings + high-IV handling: when `days_to_earnings ≤ 14 OR iv_rank > 0.75`, vehicle whitelist restricted to defined-risk + short-premium structures (verticals, ICs, butterflies, CSPs, covered calls). Long premium banned in this window.
- Backtest harness: replays the full agent loop over historical bars with BS-synthesized chains. Chain synthesizer includes event-day IV bump (1.75× decaying over 5 days on bars with |return| ≥ 3% or true range ≥ 3× ATR) to bound credit-strategy optimism. Produces per-trade ledger, equity curve, per-vehicle attribution, per-regime attribution.
- Dashboard surface: new V2 Positions tab, extension of V2 Signals tab with today's vehicle pick, new Backtest tab with latest report.

**Out of scope (deferred to later phases):**
- Calendar and diagonal structures (deferred to **C.7** — see §7 — pending multi-expiry chain handling and cross-expiry sanity rules).
- Per-direction or per-sector portfolio caps (Phase D risk layer).
- Daily drawdown circuit-breaker (Phase D risk layer).
- LLM-driven exit decisions on held positions (would-be Phase D agent-loop expansion).
- Real broker integration (the bot is paper for the foreseeable future).
- Vol-trading regime detection that would justify standalone iron condors / butterflies as a primary strategy (those structures are available to the agent but the bot does not have a "range-bound regime" signal yet).
- Historical chain data subscription (using BS synthesis with event-day bump for backtest; manual chain snapshots from Dan when needed for calibration).

## 4. Architecture

### 4.1 Package layout

```
bullbot/v2/
├── signals.py         existing — DirectionalSignal (Phase A)
├── underlying.py      existing — rules-based signal generator (Phase A)
├── trades.py          existing — Trade dataclass + helpers (Phase B — will be wrapped/extended, not deleted)
├── trader.py          existing — Phase B share-only dispatcher (kept for back-compat; Phase C runner wraps it)
├── levels.py          NEW — compute_sr(bars) -> list[Level]
├── chains.py          NEW — Yahoo chain fetch + BS fallback per leg
├── earnings.py        NEW — yfinance earnings dates + days-to-print helper
├── positions.py       NEW — OptionLeg, Position, lifecycle states, linked-position chaining
├── risk.py            NEW — three deterministic caps + sizing math
├── vehicle.py         NEW — LLM (Haiku) call, JSON output schema, validation, qty sizing
├── exits.py           NEW — per-intent exit-rule evaluator on held positions
├── runner_c.py        NEW — daily Phase C dispatcher; calls trader.py for backward-compat share path when needed
└── backtest/
    ├── __init__.py
    ├── synth_chain.py NEW — BS pricing using bars + realized-vol IV proxy + VIX regime adjustment
    ├── runner.py      NEW — replay bars + agent + chain synthesizer
    └── report.py      NEW — per-trade ledger CSV + equity curve PNG + attribution tables
```

### 4.2 Daily forward-run sequence

```
1. Bar refresh (existing Phase A path).
2. For each ticker in UNIVERSE:
   a. signals.generate(ticker) -> DirectionalSignal (Phase A, unchanged).
   b. levels.compute_sr(bars) -> [Level(price, type, strength), ...].
   c. earnings.days_to_print(ticker) -> int.
   d. positions.open_for(ticker) -> Position | None.
   e. If position exists:
        exits.evaluate(position, signal, spot, today) -> Action(hold | close | trigger_linked).
        Execute action. Persist. Skip to next ticker.
   f. If flat:
        ctx = build_llm_context(signal, levels, earnings, spot, iv_rank, budget, positions_open_count, recent_picks)
        pick = vehicle.pick(ctx)  # Haiku call
        if pick.decision == "open":
            validated = vehicle.validate(pick, chain, risk_caps)
            if validated.ok:
                position = positions.open(validated)
                persist + log fill.
3. mark_to_market(open_positions):
   for each leg:
     try Yahoo chain price; on miss, BS price; record source.
   write v2_position_mtm row.
4. Dashboard regen (existing daily job extended with new tabs).
```

### 4.3 Schema additions

All in `cache/bullbot.db`, all new tables prefixed `v2_`.

```sql
-- Replaces v2_paper_trades (Phase B). Migration path: copy v2_paper_trades rows
-- into v2_positions + v2_position_legs with single-share-leg representation.

CREATE TABLE v2_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    intent TEXT NOT NULL CHECK(intent IN ('trade', 'accumulate')),
    structure_kind TEXT NOT NULL,  -- 'long_call', 'bull_call_spread', etc., for dashboard/attribution

    -- Exit plan fields (Grok review Tier 1 Finding 4: promoted from opaque JSON to real columns
    -- so they can be queried, indexed, and migrated cleanly. exit_plan_extra_json retained as
    -- a versioned escape hatch for forward-compatible additions.)
    exit_plan_version INTEGER NOT NULL DEFAULT 1,
    profit_target_price REAL,          -- underlying price at which intent=trade closes for profit
    stop_price REAL,                   -- underlying price at which intent=trade closes for loss
    time_stop_dte INTEGER,             -- close intent=trade when nearest leg DTE <= this value
    assignment_acceptable INTEGER,     -- 0/1; intent=accumulate signals true
    nearest_leg_expiry_dte INTEGER,    -- computed at entry from legs; enables cheap time-based queries
    exit_plan_extra_json TEXT,         -- future-extension fields; null for v1 plans

    opened_ts INTEGER NOT NULL,
    closed_ts INTEGER,
    close_reason TEXT,                 -- 'profit_target', 'stop', 'time_stop', 'signal_flip',
                                       -- 'credit_profit_take', 'assigned', 'called_away', 'exercised',
                                       -- 'expired_worthless', 'safety_stop', 'manual'
    linked_position_id INTEGER,        -- e.g., covered call links to the CSP that got assigned
    rationale TEXT,                    -- LLM's <=200-char justification
    FOREIGN KEY (linked_position_id) REFERENCES v2_positions(id)
);

CREATE TABLE v2_position_legs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('buy', 'sell')),
    kind TEXT NOT NULL CHECK(kind IN ('call', 'put', 'share')),
    strike REAL,                       -- null for shares
    expiry TEXT,                       -- 'YYYY-MM-DD'; null for shares
    qty INTEGER NOT NULL,              -- contracts for options, shares for kind='share'
    entry_price REAL NOT NULL,         -- per-contract premium or per-share price
    -- Grok review Tier 1 Finding 1: when a leg is born from an assignment or exercise event
    -- (i.e., the linked-shares position created from a CSP assignment), net_basis stores the
    -- cost-adjusted share basis = strike - (csp_credit_per_contract / 100). Null for legs
    -- born from a direct open. P&L and exit-plan targets are computed against net_basis
    -- when non-null, otherwise against entry_price.
    net_basis REAL,
    exit_price REAL,
    FOREIGN KEY (position_id) REFERENCES v2_positions(id)
);

-- New table (Grok review Tier 1 Finding 1): captures assignment / exercise / called-away
-- lifecycle events with explicit link back to the originating leg so the basis math is auditable.
CREATE TABLE v2_position_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,         -- the position the event happened TO (e.g., the CSP)
    linked_position_id INTEGER,           -- the position the event CREATED (e.g., new shares)
    event_kind TEXT NOT NULL CHECK(event_kind IN ('assigned', 'called_away', 'exercised', 'expired_worthless')),
    occurred_ts INTEGER NOT NULL,
    source_leg_id INTEGER,                -- the leg that triggered the event (e.g., the short put)
    original_credit_per_contract REAL,    -- captured at event time so basis math stays reproducible
    notes TEXT,
    FOREIGN KEY (position_id) REFERENCES v2_positions(id),
    FOREIGN KEY (linked_position_id) REFERENCES v2_positions(id),
    FOREIGN KEY (source_leg_id) REFERENCES v2_position_legs(id)
);

CREATE TABLE v2_position_mtm (
    position_id INTEGER NOT NULL,
    asof_ts INTEGER NOT NULL,
    mtm_value REAL NOT NULL,           -- total position $ value at asof_ts
    source TEXT NOT NULL CHECK(source IN ('yahoo', 'bs', 'mixed')),
    PRIMARY KEY (position_id, asof_ts),
    FOREIGN KEY (position_id) REFERENCES v2_positions(id)
);

CREATE TABLE v2_chain_snapshots (
    ticker TEXT NOT NULL,
    asof_ts INTEGER NOT NULL,
    expiry TEXT NOT NULL,
    strike REAL NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('call', 'put')),
    bid REAL, ask REAL, last REAL, iv REAL, oi INTEGER,
    source TEXT NOT NULL CHECK(source IN ('yahoo', 'bs')),
    PRIMARY KEY (ticker, asof_ts, expiry, strike, kind)
);
```

### 4.4 Support/resistance module (`levels.py`)

Pure function: `compute_sr(bars: list[Bar], lookback: int = 60) -> list[Level]`.

`Level(price: float, kind: str, strength: float)` where kind in `{swing_high, swing_low, sma_20, sma_50, sma_200, round_number}` and strength in [0, 1] based on touch count + recency.

Algorithm:
1. Find swing highs/lows in last `lookback` bars (local extrema with N-bar confirmation, N=3).
2. Add current values of 20/50/200 SMA as dynamic levels.
3. Snap round numbers within 2% of spot (e.g., $100, $105, $110 for a $103 stock).
4. Deduplicate levels within 0.5% of each other; keep max strength.
5. Sort by distance to spot.

No external dependencies beyond what Phase A bar refresh already provides.

### 4.5 Vehicle agent module (`vehicle.py`)

**Responsibilities:**
1. Build the LLM input JSON from gathered context.
2. Call Haiku.
3. Parse and JSON-schema-validate the response.
4. Run `validate_structure_sanity(legs, spot, structure_kind) -> SanityResult` — pure-Python cheap check that the legs returned are a coherent instance of the declared structure (see §4.6).
5. Hand off to risk sizing.

**Input JSON to Haiku:**
```json
{
  "ticker": "AAPL",
  "spot": 185.42,
  "signal": {"direction": "bullish", "confidence": 0.72, "horizon_days": 30},
  "iv_rank": 0.34,
  "iv_percentile": 0.42,
  "atr_14": 3.21,
  "rsi_14": 58.2,
  "dist_from_20sma_pct": 0.018,
  "levels": {
    "nearest_resistance": {"price": 192.0, "strength": 0.8, "kind": "swing_high"},
    "nearest_support": {"price": 178.5, "strength": 0.6, "kind": "sma_50"},
    "all_levels_within_5pct": [...]
  },
  "days_to_earnings": 23,
  "earnings_window_active": false,
  "large_move_count_90d": 4,
  "near_atm_liquidity": {"total_oi_within_5pct": 12430, "spread_avg_pct": 0.018, "nearest_monthly_expiry": "2026-06-19"},
  "budget_per_trade_usd": 1500,
  "current_position": null,
  "recent_picks_this_ticker": [
    {"date": "2026-05-10", "structure": "bull_call_spread", "outcome": "closed_profit_target"},
    ...
  ],
  "portfolio_state": {"open_positions": 7, "ticker_concentration_pct": 0.0}
}
```

`earnings_window_active` is `true` when `days_to_earnings ≤ 14 OR iv_rank > 0.75` (Grok review Tier 2 Finding 7). When active, the LLM's `structure` choice must come from the earnings/high-IV whitelist enforced in §4.6.

`large_move_count_90d` (Grok review Tier 2 Finding 5) is the count of trailing-90-bar days with `|return| ≥ 3% OR true range ≥ 3 × ATR_14`. Surfaces jump risk to the agent so it can prefer defined-risk on twitchy names.

`near_atm_liquidity` (also Tier 2 Finding 5) is a cheap liquidity flag computed from the nearest two monthly expiries within ±5% of spot: total open interest summed and average bid-ask spread as percent of mid. Lets the agent avoid picking strikes the chain can't honor.

**Output JSON from Haiku (schema-validated):**
```json
{
  "decision": "open" | "pass",
  "intent": "trade" | "accumulate",
  "structure": "long_call" | "long_put" | "bull_call_spread" | "bear_put_spread" | "covered_call" | "csp" | "long_shares" | "short_shares" | "iron_condor" | "butterfly",
  "legs": [
    {"action": "buy"|"sell", "kind": "call"|"put"|"share", "strike": float|null, "expiry": "YYYY-MM-DD"|null, "qty_ratio": int}
  ],
  "exit_plan": {
    "profit_target_price": float | null,
    "stop_price": float | null,
    "time_stop_dte": int | null,
    "assignment_acceptable": bool
  },
  "rationale": "<=200 chars"
}
```

The `structure` enum drops `calendar` and `diagonal` (Grok review Tier 3 cut) — these require multi-expiry leg handling and have the largest BS-pricing error of any structure in scope. Deferred to follow-up sub-step C.7.

The LLM picks structure + strikes + expiry + qty_ratio (relative ratios between legs, e.g., 1:1 for a vertical, 1:2:1 for a butterfly). Actual quantity is computed by us using `risk.size_position(legs, max_loss_pct, nav)` to enforce caps. This split — LLM picks shape, we compute size — prevents the agent from rounding up against the risk cap.

### 4.6 Validation (`vehicle.validate`)

Validation runs in this order. Failure at any step skips the ticker for the day with a typed action log.

1. **Structure sanity (Grok review Tier 1 Finding 2):** `validate_structure_sanity(legs, spot, structure_kind)` confirms the legs form a valid instance of the declared structure. This is the highest-ROI guardrail and runs FIRST so we never spend chain-lookup or risk-sizing effort on nonsense. Per-structure checks:
   - `bull_call_spread`: 2 legs, both calls, same expiry, qty_ratio 1:1, long strike < short strike, both within reasonable moneyness band.
   - `bear_put_spread`: 2 legs, both puts, same expiry, qty_ratio 1:1, long strike > short strike.
   - `iron_condor`: 4 legs (2 calls + 2 puts), same expiry, sell strikes inside buy strikes, symmetric or asymmetric but no overlapping wings.
   - `butterfly`: 3 strikes, qty_ratio 1:2:1 with middle strike sold, equidistant body wings (within 5% tolerance for asymmetric variants).
   - `csp`: 1 short put leg.
   - `covered_call`: 1 long-share leg + 1 short-call leg, share qty = 100 × contract qty.
   - `long_call` / `long_put` / `long_shares` / `short_shares`: 1 leg, kind + action match the declared structure.
   - Moneyness sanity for ALL structures: no strike more than 25% from spot.
   - Expiry sanity: every option leg's expiry ≥ today + 7 days.
   - Failure → log `vehicle_invalid_structure` with raw LLM JSON; skip ticker.
2. Every leg's strike + expiry exists in current chain (Yahoo) OR is within BS-pricable range (ATM ±10%, 21-365 DTE).
3. `risk.compute_max_loss(legs)` ≤ `per_trade_cap_usd`. Failure → `skipped_max_loss_cap`.
4. Ticker concentration after adding this position ≤ `per_ticker_cap_pct`. Failure → `skipped_ticker_concentration`.
5. `positions.open_count() + 1` ≤ `total_positions_cap`. Failure → `skipped_max_positions`.
6. **Earnings / high-IV window (Grok review Tier 2 Finding 7):** if `days_to_earnings ≤ 14 OR iv_rank > 0.75`, structure ∈ {bull_call_spread, bear_put_spread, iron_condor, butterfly, csp, covered_call}. Failure → `skipped_earnings_or_high_iv`. (Broader than the original 7-day rule — IV-rank > 0.75 catches non-earnings vol spikes where long-premium has poor expectancy regardless of an upcoming print.)
7. If `intent == "accumulate"`, structure ∈ {csp, long_shares, long_call (deep ITM ≥0.8 delta only), covered_call}. Failure → `skipped_intent_structure_mismatch`.

### 4.7 Exit-rule evaluator (`exits.py`)

Per-tick on held positions. All targets are evaluated against the leg's `net_basis` when non-null (positions born from assignment/exercise) and against `entry_price` otherwise — see Finding 1 below.

- **`intent == "trade"`:**
  - Underlying tags `profit_target_price` → close, reason `profit_target`.
  - Underlying tags `stop_price` → close, reason `stop`.
  - Signal flips opposite direction with confidence ≥ 0.5 → close, reason `signal_flip`.
  - Days to nearest leg expiry ≤ `time_stop_dte` → close, reason `time_stop`.
  - **Credit-structure profit-take** (Grok review Tier 2 Finding 6): if the position is a net-credit structure (CSP, bear call credit spread, bull put credit spread, iron condor, butterfly held for credit), close when `remaining_premium ≤ 0.50 × max_credit_received` (or `remaining_premium ≤ 0.10 × spread_width` for narrow credit verticals where 50% leaves too little). Theta decay is front-loaded; holding credit to zero is greedy and gamma-risky. Close reason: `credit_profit_take`.

- **`intent == "accumulate"`:**
  - Hold to expiry. At expiry, evaluate moneyness per leg.
  - **Short put (CSP) ITM at expiry — assignment path (Grok review Tier 1 Finding 1, net basis):**
    1. Compute `net_basis = strike − (original_credit_per_contract / 100)`.
    2. Open a new linked long-shares position with `entry_price = strike` and `net_basis = net_basis` on the share leg.
    3. Close the CSP with reason `assigned`.
    4. Write a `v2_position_events` row capturing the original credit so the basis math is auditable.
    5. **Post-assignment exit-plan derivation (Grok review Tier 2 Finding 8):** the newly-opened shares position does NOT inherit a generic "hold until called away" plan; instead it gets a fresh exit plan derived from the current Phase A signal via `compute_post_assignment_exit_plan(signal, net_basis, atr_14)`:
       - signal == `bullish` with confidence ≥ 0.5 → stays `accumulate`; default exit = "hold until called away or signal flips bearish"; soft stop at `net_basis − 2 × ATR` for safety.
       - signal == `bearish` with confidence ≥ 0.5 → switch to `trade` intent; hard stop at `net_basis − 1 × ATR`; profit target left null (forced-liquidation path).
       - signal == `chop` / `no_edge` → stays `accumulate`; defensive stop at `net_basis − 2 × ATR`; covered call eligible on next tick.
  - **Short call (CC) ITM at expiry — called-away path:** close the linked shares at the strike; close the CC with reason `called_away`. Write `v2_position_events` row. Realized P&L on shares is computed against `net_basis`.
  - **Long ITM call at expiry — exercise path:** open new linked long-shares position at `entry_price = strike`. The shares' `net_basis = strike + (premium_paid_per_contract / 100)`. Close the call with reason `exercised`. Write `v2_position_events` row.
  - Otherwise (OTM at expiry) → close, reason `expired_worthless`.

- **Stop-loss safety net (independent of intent):** underlying gaps ≥ 15% adverse from entry (or from `net_basis` when non-null) → emergency close, reason `safety_stop`.

### 4.8 Chains module (`chains.py`)

Two entry points:

```python
def fetch_chain(ticker: str, asof: date | None = None) -> Chain | None:
    """Yahoo chain fetch. None on failure. Caches per (ticker, date) in v2_chain_snapshots."""

def price_leg(leg: OptionLeg, spot: float, iv: float | None, today: date) -> tuple[float, str]:
    """Returns (mid_price, source). Tries Yahoo first, falls back to BS. source in {'yahoo','bs'}."""
```

BS pricer is standard: handles American-style for early exercise on deep-ITM short positions only at expiry (no continuous early-ex modeling — paper trading, doesn't matter).

IV input for BS: if Yahoo gave a recent IV for nearby strike, use it. Otherwise compute realized vol of underlying over trailing 30 bars and scale by current VIX / median(VIX last 60) as a crude regime multiplier.

### 4.9 Backtest harness (`backtest/`)

Goal: given 2 years of bars per ticker, replay the full Phase C agent loop and produce a report.

**runner.py:**
```python
def backtest(ticker: str, start: date, end: date, starting_nav: float) -> BacktestResult:
    """Replay Phase C over historical bars. Returns trade ledger + equity curve."""
```

Per simulated day:
1. Slice bars up to that day.
2. Call signals.generate, levels.compute_sr, earnings.days_to_print (use stored historical earnings dates).
3. Synthesize chain via `synth_chain.synthesize(ticker, asof, expiries, strikes)`.
4. Call vehicle.pick with full context (Haiku call — yes, this means backtest hits the API; budget cap on backtest = $5/run, cached by input-hash so reruns are free).
5. Validate, open/close positions same as forward path.
6. Mark to market using synthesized chain at end-of-day.

**Backtest constraints to keep BS error bounded:**
- Strike range restricted to ATM ±10% (BS-correctness sweet spot).
- DTE restricted to 21-365 days.
- Vehicles outside this constraint (deep OTM credit spreads, weekly expiries) are NOT picked in backtest — agent's input declares restricted mode.

**Event-day IV bump (Grok review Tier 1 Finding 3):** the baseline IV proxy (realized-vol-30d × VIX regime multiplier) under-prices vol on jump days, which makes credit structures look artificially good in backtest. `synth_chain.synthesize` detects historical bars where `|close-to-close return| ≥ 3% OR true range ≥ 3 × trailing-20-bar ATR` and inflates the IV proxy on those bars by 1.75× (tunable in `[1.5, 2.0]`). The bump persists for 5 trading days post-event, decaying linearly back to 1.0× over that window. This bounds credit-strategy optimism without any paid data feed. When manual chain snapshots from Dan are available, they become the calibration set for tuning the multiplier and decay window per ticker.

**report.py outputs:**
- `backtest_trades.csv` — per-trade ledger.
- `equity_curve.png` — cumulative P&L over time, with SPY buy-and-hold benchmark overlay.
- `vehicle_attribution.csv` — per-structure win rate, avg P&L, total contribution to equity.
- `regime_attribution.csv` — performance bucketed by VIX-tertile and SPY-trend regime.
- `validation_summary.txt` — confusion matrix of BS-vs-real where Dan-provided manual chain snapshots exist.

### 4.10 Dashboard surface

Three additions to existing dashboard:
1. **V2 Signals tab** (existing) extended: column "Today's vehicle pick" showing yesterday's pick that opened today, rationale, max-loss $.
2. **V2 Positions tab** (new): list each open position (ticker, structure, leg summary, days held, MtM, unrealized P&L, intent, exit plan, source of MtM).
3. **Backtest tab** (new): embeds latest backtest report (auto-regen weekly via cron); equity curve PNG, attribution tables, last-run timestamp.

## 5. Error handling

| Failure | Response |
|---|---|
| Yahoo chain fetch timeout/empty | BS fallback for pricing; log `chain_fallback_bs` per leg |
| LLM returns invalid JSON | Skip ticker that day; log `vehicle_llm_invalid_json`; metric incremented |
| LLM picks strike not in chain | Snap to nearest available strike; log `strike_snapped` |
| Sizing fails per-trade cap | Emit `skipped_max_loss_cap`; do not retry with smaller qty (the agent picked the structure for the loss budget, smaller qty changes the trade thesis) |
| Sizing fails ticker concentration | Emit `skipped_ticker_concentration`; pass on this ticker today |
| Sizing fails total-positions cap | Emit `skipped_max_positions`; pass; tomorrow may have room after exits |
| Earnings window violation | Emit `skipped_earnings_window`; pass |
| Backtest LLM cost cap exceeded | Halt backtest; emit partial report flagged as incomplete |
| Position MtM Yahoo + BS both fail (e.g., underlying delisted) | Mark position with `mtm_unavailable`; do not exit on missing data |

## 6. Testing strategy

**Unit tests:**
- `levels.compute_sr` — fixture bars with known swing points, assert correct levels emitted.
- `chains.price_leg` — BS pricer against textbook examples (known input → known output to 4 decimals).
- `risk.compute_max_loss` — every structure type with sample legs.
- `vehicle.validate_structure_sanity` — table-driven per-structure: bull call spread with inverted strikes rejected, butterfly with non-symmetric wings flagged, iron condor with overlapping wings rejected, single-leg structures with wrong kind rejected, options with expiry < today+7 rejected, strikes > 25% from spot rejected. Each failure mode returns the correct typed reason. (Grok review Tier 1 Finding 2.)
- `vehicle.validate` — each downstream failure mode produces correct action enum, including `skipped_earnings_or_high_iv` triggering on both `days_to_earnings ≤ 14` and `iv_rank > 0.75` independently.
- `exits.evaluate` — table-driven for each (intent × trigger) combination, including the new `credit_profit_take` trigger.
- `exits.compute_post_assignment_exit_plan` — verifies the three signal branches (bullish stays accumulate, bearish flips to trade, chop/no_edge stays accumulate with defensive stop) produce the documented exit plans. (Grok review Tier 2 Finding 8.)
- `earnings.days_to_print` — mock yfinance, assert windowing.
- `positions` — leg serialization round-trip, linked-position chain integrity, `net_basis` preserved across serialization.
- `positions.csp_assignment` — opens CSP with $2.00 credit, simulates assignment at $100 strike, asserts the linked shares leg has `entry_price = 100.00` and `net_basis = 98.00`, asserts the `v2_position_events` row captures `original_credit_per_contract = 200.00`. Subsequent stop-target test on the linked shares is computed against $98.00. (Grok review Tier 1 Finding 1.)
- `backtest.synth_chain.event_day_iv_bump` — fixture bars with a known >3% move, assert IV proxy on that day is inflated by ~1.75× over the baseline regime calculation, and that the bump decays linearly to 1.0× by day 6 post-event. (Grok review Tier 1 Finding 3.)

**Integration tests:**
- Full daily-run path with: mocked Yahoo chain, canned LLM response (fixture JSON), fixture bars. End-to-end open/MtM/close flow.
- Wheel scenario: CSP assigned (with credit captured in event row) → linked shares opened with `net_basis` set → exit plan derived from current signal → CC opened automatically → CC called away → linked shares closed with P&L computed against `net_basis` → cycle ends. Assert `linked_position_id` chain and `v2_position_events` rows read correctly throughout.

**Backtest acceptance:**
- 2-year replay on at least 3 tickers (SPY, AAPL, TSLA) completes without exceptions.
- Equity curve is monotonic-in-time (strictly later asof_ts = strictly later index — guards against time-travel bugs).
- Number of trades > 0 per ticker (sanity that agent did SOMETHING).
- Per-vehicle attribution table is non-empty.
- On at least one ticker with a known historical jump (e.g., MSTR on a crypto-spike day in the replay window), assert `synth_chain` produced inflated IV vs the no-bump baseline. (Grok review Tier 1 Finding 3 calibration check.)

## 7. Phase order within Phase C

Each sub-step shippable independently, mergeable to main:
- **C.0** — schema migration (including `v2_position_events` table, promoted exit-plan columns, `net_basis` column) + `positions.py` (with assignment/exercise event support) + `risk.py` (data model).
- **C.1** — `chains.py` (Yahoo + BS).
- **C.2** — `levels.py`.
- **C.3** — `earnings.py` + `vehicle.py` (including `validate_structure_sanity` per-structure rules) + `exits.py` (including `credit_profit_take` rule and `compute_post_assignment_exit_plan`).
- **C.4** — `backtest/` (including `synth_chain.py` event-day IV bump and calibration check).
- **C.5** — `runner_c.py` + dashboard tabs.
- **C.6** — ship to pasture + verify live.
- **C.7 (deferred follow-up, NOT part of Phase C ship):** add calendar + diagonal vehicles. Requires multi-expiry chain handling pass, cross-expiry `validate_structure_sanity` rules, and BS-error analysis on multi-expiry leg combinations before re-enabling them in the structure enum. (Grok review Tier 3 cut.)

## 8. Open questions / known unknowns

- IV proxy methodology for backtest synthesis (realized vol × VIX regime, now with event-day bump per Tier 1 Finding 3) is still approximate. The event-day bump bounds the most obvious credit-strategy optimism, but per-ticker calibration of the multiplier (1.5–2.0 range) and decay window (currently 5 trading days) will need manual chain snapshots to refine. Plan to revisit once first 10+ snapshots are available.
- LLM-picked structures could over-concentrate in covered calls / CSPs simply because they appear "safer" in prompt context. Per-vehicle attribution in backtest will reveal this; if so, prompt-engineering to encourage exploration.
- No explicit handling of dividend ex-dates affecting short calls. Phase C accepts dividend-induced early-assignment risk as a known unmodeled gap; will revisit if it materially impacts results.
- Per-direction (don't be 100% bullish) and per-sector caps deliberately out of scope. If backtest reveals these matter, add in Phase D.

**Resolved during review (no longer open):**
- ~~Schema fragility of `exit_plan_json`~~ — promoted core fields to real columns + added version + `nearest_leg_expiry_dte` in §4.3.
- ~~Net basis on CSP assignment~~ — `net_basis` column + `v2_position_events` table + post-assignment exit-plan derivation in §4.3 and §4.7.
- ~~Post-LLM structure-sanity validation~~ — `validate_structure_sanity` runs first in the validation pipeline in §4.6.
- ~~Event-day jump risk in synth chains~~ — 1.75× IV bump with 5-day linear decay in §4.9.
- ~~Earnings rule too narrow~~ — expanded trigger to `(days_to_earnings ≤ 14 OR iv_rank > 0.75)` in §4.6 step 6.
- ~~Credit structures held to zero~~ — `credit_profit_take` exit rule added in §4.7.
- ~~Calendars/diagonals in initial menu~~ — cut from §3 / §4.5 enum; deferred to C.7 in §7.
