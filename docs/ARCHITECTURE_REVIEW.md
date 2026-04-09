# Architecture Review

**Reviewer:** Claude (second pass)
**Reviewed document:** `docs/ARCHITECTURE.md` v1.0
**Date:** 2026-04-09

I re-read `ARCHITECTURE.md` pretending I was going to build and operate this for 3 months. I asked "what breaks, what's ambiguous, what's missing?" Notes below are organized by severity.

---

## 1. Must-fix before build (blockers)

### 1.1 Confluence scoring is underspecified
The architecture says "compute confluence from 5 signals" but doesn't define the math. A reviewer can't predict what a 70 score means vs. a 40. Needs an explicit formula:

```
direction_weight[tf] = timeframe_weights[tf] × (1 if signal.direction != "neutral" else 0)
directional_mass    = sum(signal.conviction × direction_weight[tf] for agreeing timeframes)
opposing_mass       = sum(signal.conviction × direction_weight[tf] for opposing timeframes)
confluence_score    = clamp(100 × (directional_mass - opposing_mass) / max_possible_mass, -100, 100)
```

Positive score = net long bias, negative = net short bias, magnitude = strength. The decision agent uses `abs(score)` for the size multiplier and `sign(score)` for direction.

**Action:** Add §4.3a "Confluence math" to ARCHITECTURE.md before build.

### 1.2 Signal staleness rules missing
The decision agent runs on "latest signal per ticker/timeframe" — but what if the 15m agent hasn't run in 2 hours because of an error? Should we use that stale signal, or ignore it?

**Decision needed:**
- 15m signals older than 1 hour → treated as neutral
- 1h signals older than 3 hours → neutral
- 4h signals older than 8 hours → neutral
- daily signals older than 2 trading days → neutral
- weekly signals older than 2 weeks → neutral

**Action:** Add to §4.3 and enforce in the signal-loading code.

### 1.3 Multi-leg position P&L not specified
Credit spreads have two legs. The ledger schema shows `legs_json` but doesn't define the structure. Marking a spread to market requires:
- Current mid price of each leg
- Net debit/credit
- Days held
- Max profit / max loss (set at entry, used for % of max profit exit rule)

**Action:** Define a `Leg` dataclass in the data layer and document it in §3.1.

### 1.4 No options expiration data model
The architecture mentions "auto-close at expiry" but doesn't describe how the system knows when expiry is. Expiry dates come from the options contract, which is part of the leg. Needs to be stored on each position so the nightly pass can find positions expiring today.

**Action:** Add `expiry_date` as a first-class column on `positions_open` and `positions_closed` (nullable for non-option positions).

### 1.5 Margin tracking incomplete
Dan said $25k paper + 2x margin = $50k buying power. The architecture mentions `margin_used` in `daily_equity` but doesn't specify:
- How margin used is computed per strategy (short put CSP: collateral × 100 minus cash; credit spread: max loss × 100; long options: no margin)
- How margin interest is charged (daily at 8%/365 on (margin_used - cash))
- Whether margin interest is added to `pnl_net` or shown separately

**Action:** Add §3.1a "Margin accounting rules" with per-strategy formulas.

---

## 2. Should-fix before build (strong recommendations)

### 2.1 Polygon options endpoint specifics unknown
The plan assumes Polygon provides historical options chains and greeks. In practice, this depends on the subscription tier and whether we need end-of-day or intraday option quotes. I built `polygon_client.py` with snapshot and chain methods, but I don't know if Dan's plan actually returns populated bid/ask/greeks, especially on less-liquid options.

**Mitigation:** Smoke test the Polygon options endpoints against Dan's API key as step 1 of the build. If they return empty or throttle aggressively, add Black-Scholes fallback greeks using underlying price + VIX-derived IV + contract strike/expiry.

### 2.2 Unusual Whales endpoint paths are guessed
The `uw_client.py` stub uses my best guess at endpoint paths (`/stock/{ticker}/stock-state`, `/stock/{ticker}/flow-alerts`, etc.). These need to be verified against the actual UW API documentation.

**Mitigation:** Before building the research agents (which depend on UW data), do an API-validation pass: fetch each endpoint with a real key and adjust the client.

### 2.3 Extended hours and VWAP anchoring
The schedule runs research during extended hours (4 AM to 8 PM ET). But VWAP is conventionally anchored to the regular session start (9:30 AM) and is meaningless in pre-market. If I feed pre-market VWAP to a 15m agent, it might make bad calls.

**Resolution:** Compute two VWAPs: `vwap_session` (anchored to 9:30 AM ET reset, meaningful only in regular hours) and `vwap_rolling` (rolling N-bar VWAP, works across all sessions). Pre-market and post-market agents use rolling; regular-session agents use session.

**Action:** Document this in §3.1 `tech_features` schema and §4.2 research agent inputs.

### 2.4 Crypto ETF weekend gaps
IBIT and BSOL track crypto that moves 24/7, but the ETFs trade only during market hours. A huge weekend BTC move will appear as a Monday open gap. The architecture doesn't call out how the bot should handle this.

**Resolution:** 
- Flag Monday opens on IBIT/BSOL where the gap is > 3% in the daily report
- Skip decision passes for crypto ETFs in the first 30 minutes after Monday open (let the gap settle)
- Consider feeding crypto spot price (from Polygon if available, or a separate crypto API) to the crypto ETF research agents as an extra signal

**Action:** Add §4.2a "Crypto ETF special handling" or defer with an explicit note.

### 2.5 Trading calendar hard-coded vs. library
I said "hard-coded holiday list in v1." That's fragile (CME changes early-close days occasionally). Better approach: use `pandas_market_calendars` library which maintains NYSE calendar with early closes.

**Action:** Add `pandas_market_calendars` to requirements.txt and use it.

### 2.6 Reconciliation doesn't handle signal staleness after long downtime
If the bot is down for 4 hours during market hours, the reconcile script marks positions but doesn't invalidate stale signals. The first decision pass after restart could use 4-hour-old signals as if they were fresh.

**Resolution:** `reconcile.py` clears all signals older than 1 hour from the latest-signal view and logs the invalidation.

### 2.7 Signal table is append-only but queries use "latest per ticker/tf"
The decision agent wants "latest signal per (ticker, timeframe)". A naive query on an append-only table gets slow fast. Needs an index on `(ticker, timeframe, ts DESC)` plus a materialized view or a second "latest_signals" table that the research agent upserts into alongside the append.

**Action:** Add to §3.1 — `latest_signals` table with PK (ticker, timeframe), updated by upsert, used by the decision agent.

### 2.8 Decision agent context window with 25 tickers × 5 timeframes
125 signals + portfolio state + strategy config could get large. Need to verify it fits in Sonnet's context window comfortably and, if not, batch by ticker or by sector.

**Mitigation:** First pass sends all 125 signals in one call. Measure actual token count in the smoke test. If > 40k input tokens, batch.

### 2.9 The "talk to Claude" interaction model assumes sync works
The architecture handwaves "reports sync via Dropbox/iCloud." Dan needs to pick one and I need to test that reports written on the dedicated machine actually show up on the main machine within a few minutes.

**Action:** Document the sync option explicitly in §8 with setup steps and a sync-latency SLO.

---

## 3. Nice-to-have (not blockers but should be logged)

### 3.1 Portfolio-level Greeks rollup
Net delta / gamma / theta / vega across all open positions is useful risk info. Not critical for v1 but worth logging so we can add it.

### 3.2 Benchmark selection per asset class
I default to SPY as the benchmark. But for crypto ETFs (IBIT/BSOL), comparing to SPY isn't fair. Should compare IBIT to BTC (via BITB or direct price), BSOL to SOL. Commodity ETFs should compare to their underlying commodity.

### 3.3 Strategy sandbox mode
It would be valuable to run the evolver's proposed strategy against the past 4 weeks' signals in backtest mode before going live. "Would this new config have made more money over the same period?" The plan has the attribution data but not the sandbox.

### 3.4 Multi-agent parallelism
Running 5 research agents serially for 25 tickers × 5 timeframes = 125 calls. If each takes 3 seconds, that's 6 minutes per full pass. Acceptable but not great. Parallelizing with a simple asyncio worker pool would cut this to ~30 seconds. Deferring to v2.

### 3.5 Cost dashboard
Track LLM spend per day / per agent / per model and plot it. Catches runaway costs early.

---

## 4. Consistency issues I found

### 4.1 `ticker_eligibility` is mentioned but not defined for all tickers
Section 6.2 shows ticker_eligibility with NVDA and HIMS examples but doesn't specify it for every ticker. For v1, I should generate a complete default for all 27 symbols based on their price range.

**Action:** Populate the v1 `strategy_config.json` with eligibility for every ticker.

### 4.2 `runtime_state.json` mentioned in §8 but not in the config layer
Section 8 mentions "Claude sets a flag in runtime_state.json" to pause trading. This file doesn't exist in the architecture yet. Needs to be added as a runtime-owned file the decision agent reads at the start of every pass.

**Action:** Add §6.4 `runtime_state.json` — runtime flags (pause_trading, force_close_all, notes).

### 4.3 The weekly agent runs on Sonnet but is listed in the "Haiku routine" cost estimate
The cost sanity check in `PLAN.md` §5 treats all research as Haiku. Weekly should be Sonnet — small impact (1 call/week × 27 tickers = 27 Sonnet calls per week, negligible), but worth correcting.

### 4.4 Crash recovery promises "replays missed performance passes" but the details are vague
What does "replay" mean? Does it compute the skipped daily performance rows? If so, where do the prices come from (historical bars, end-of-day snapshots)? Needs to be nailed down.

**Resolution:** Reconcile uses end-of-day close from Polygon's daily bars to fill in `daily_marks` and `daily_equity` for any missed dates. Decision history is NOT replayed.

---

## 5. Security and privacy notes

### 5.1 API keys in `.env` — good, but should be 0600 permissions
Standard practice. The install doc should include `chmod 600 .env` explicitly.

### 5.2 No telemetry leaving the bot
The bot does not phone home, does not send data to Anthropic for training (API calls are separate), and does not expose any HTTP endpoints. Good.

### 5.3 Sync folder concerns
If Dan uses iCloud or Dropbox, reports are synced to Apple/Dropbox servers. Dan should be aware that his paper P&L and decision rationale will live in that provider's cloud. Not a big deal (it's paper, no real trading), but worth a note in the install doc.

---

## 6. Summary of required changes to ARCHITECTURE.md

Before I move to the work plan, these changes need to be folded in:

1. Add §4.3a "Confluence math" with the explicit formula
2. Add §4.3b "Signal staleness rules"
3. Add `Leg` dataclass spec in §3.1
4. Add `expiry_date` column to position tables in §3.1
5. Add §3.1a "Margin accounting rules"
6. Add `latest_signals` table in §3.1
7. Add `vwap_session` and `vwap_rolling` distinction in §3.1 and §4.2
8. Add §4.2a "Crypto ETF special handling"
9. Add §6.4 `runtime_state.json`
10. Replace hard-coded holiday list with `pandas_market_calendars` in §5.1 and requirements.txt
11. Clarify reconcile replay semantics in §5.2
12. Populate complete v1 `ticker_eligibility` in the strategy config example

None of these are architectural rewrites — they're clarifications and additions. They don't change the component topology or data flow.
