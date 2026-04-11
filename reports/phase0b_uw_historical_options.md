# Phase 0b — UW Historical Options Data Validation

**Run:** 2026-04-10
**Scripts:** `scripts/validate_uw_historical_options.py`, `scripts/validate_uw_historical_options_expired.py`
**Purpose:** Resolve the single biggest open question in the Bull-Bot v3 design — does UW return usable historical options data for backtesting strategies against expired contracts?

## Executive summary

**The options backtest thesis is viable on the current UW tier, with three workarounds.**

| Capability | Status | Notes |
|---|---|---|
| Chain discovery (`/option-chains?date=X`) for past dates >7 trading days back | ❌ Gated | HTTP 403 `historic_data_access_missing`. Requires tier upgrade (email `dev@unusualwhales.com`) |
| Per-contract daily history (`/option-contract/{id}/historic`) for expired contracts | ✅ Unrestricted | Returns full lifetime of any contract, including contracts expired 22+ months ago |
| Per-contract `nbbo_bid` / `nbbo_ask` on historical daily rows | ✅ 100% populated across all tested contracts |
| Per-contract `implied_volatility` on historical daily rows | ⚠️ Populated recently; sparse on older rows | Fresh contracts: ~99%. 22-months-expired: 30%. Workaround via Black-Scholes inversion |
| Per-contract greeks (delta/gamma/theta/vega) in `/historic` response | ❌ Not in schema | Workaround: compute analytically from IV + spot + strike + time-to-expiry |
| Per-contract intraday minute bars (`/option-contract/{id}/intraday?date=X`) | ⚠️ Works but sparse for illiquid strikes (6 ticks on test) | Sufficient for v1 daily-timeframe strategies; defer full validation until intraday strategies are needed |

## Probe results

### Probe 1 — chains_snapshot (2024-06-14, 22 months back)

```
HTTP 403
Body: {"code":"historic_data_access_missing",
       "message":"The earliest date currently available to you is 2026-03-30
                  (7 trading days) so 2024-06-14 in query param date will not
                  return historical data. If you wish to access full historic
                  data please email dev@unusualwhales.com with your use case."}
```

**Finding:** `/option-chains?date=X` is hard-gated to the trailing 7 trading days on our tier.

### Probe 2 — chains_snapshot (2026-04-06, 4 trading days back)

```
HTTP 200
13,392 total symbols on 2026-04-06
37 distinct expiries (2026-04-06..2028-12-15)
Target expiry 2026-04-17: 269 puts + 269 calls
```

**Finding:** The endpoint itself works perfectly within the 7-day window.

### Probe 3 — historic_daily on `SPY260417P00666000` (currently live)

```
HTTP 200
55 daily rows, range 2026-01-21 .. 2026-04-09
nbbo_bid populated: 55/55 (100%)
nbbo_ask populated: 55/55 (100%)
implied_volatility populated: 55/55 (100%)
```

**Finding:** `/historic` returned ~2.5 months of data going well past the 7-day chain-discovery gate — for a contract that the probe endpoint told us we couldn't look up on most of those dates. The `/historic` endpoint does NOT respect the 7-day gate.

### Probe 4 — historic_daily on already-expired contracts

| Contract | Expiry | Rows | Date range | nbbo_bid | nbbo_ask | IV |
|---|---|---|---|---|---|---|
| `SPY260320P00570000` | 3 wks ago | 263 | 2025-03-05 .. 2026-03-20 | 263/263 | 263/263 | 259/263 |
| `SPY251219P00570000` | 4 mo ago | 262 | 2024-12-04 .. 2025-12-19 | 262/262 | 262/262 | 261/262 |
| `SPY240621P00540000` | 22 mo ago | 262 | 2023-06-07 .. 2024-06-21 | 262/262 | 262/262 | **79/262** |

**Findings:**
1. `/historic` works for expired contracts with full lifetime data — even 22-month-old contracts return the entire ~1-year life of the contract.
2. `nbbo_bid` and `nbbo_ask` are 100% populated on every tested contract. This is the critical fill-model input.
3. Implied volatility is well-populated on recently-expired contracts but sparse on older ones (30% on a 22-month-old contract). UW appears to have started computing/storing historical IV at some point in the past.
4. Response includes a rich set of extra fields not in the OpenAPI example: `bid_volume`, `ask_volume`, `sweep_volume`, `mid_volume`, `total_ask_changes`, `total_bid_changes`, `avg_price`, `total_premium`, `last_tape_time`, `trades`, `canceled_volume`, `floor_volume`, `multi_leg_volume`, `iv_high`, `iv_low`. These are bonus signals for the research agent.

## Workarounds required by the v3 design

### 1. Chain-discovery gating → algorithmic symbol enumeration

The `/option-chains?date=X` gate blocks us from *discovering* what contracts existed on a past date. But we don't need to discover them — we can **construct** option symbols directly using the OSI regex pattern from the OpenAPI spec (line 16596):

```
^(?<symbol>[\w]*)(?<yy>\d{2})(?<mm>\d{2})(?<dd>\d{2})(?<type>[PC])(?<strike>\d{8})$
```

For liquid underlyings the expiry calendar is predictable (M/W/F weeklies, third-Friday monthlies, quarterlies, EOM) and the strike grid is $1 near ATM. We enumerate all plausible contracts for a ticker across our backtest window, call `/historic` on each, and cache the response. Invalid symbols return empty or 404 which we ignore.

**Cost estimate for SPY, 2 years:** ~60 expiries × ~200 strikes × 2 (put/call) = ~24,000 symbols × ~10 rps = **~40 minutes one-time backfill per ticker**. Across a 10-ticker universe, ~6 hours of one-time backfill.

### 2. IV sparsity on older contracts → Black-Scholes inversion

`nbbo_bid` + `nbbo_ask` are 100% populated, so mid price is always known. Combined with strike, time-to-expiry, underlying spot (UW daily OHLC, 10y depth from Phase 0), and risk-free rate (treasury constant), we solve for IV numerically via Brent's method on the Black-Scholes pricing function. Scipy's `brentq` takes ~1ms per contract. This produces reliable IV even on rows where UW's pre-computed IV is null.

### 3. Missing greeks → compute analytically

`/historic` does not return delta, gamma, theta, or vega — only IV. Once we have IV (either from UW or from the inversion above), the greeks are closed-form expressions over the Black-Scholes model. Implemented as pure functions in `bullbot/features/greeks.py`; standard math.

## v1 scope decisions driven by this finding

- **Daily-timeframe strategies only in v1.** No 0DTE, no intraday. Entry/exit at daily close (or next-day open). Defers the intraday-data question entirely.
- **Strategy seed library pinned to six daily shapes:** PutCreditSpread, CallCreditSpread, IronCondor, CashSecuredPut, LongCall, LongPut.
- **Options backfill becomes a Stage 1 prerequisite task.** Ticker universe must be fully backfilled (symbols enumerated + `/historic` cached + IV filled in + greeks computed) before the evolver runs.
- **Backtest windows capped at ~12 months of history initially.** This keeps IV sparsity in the tolerable range (>95% populated on the last year of data per tested contracts) and avoids the heaviest Black-Scholes inversion load. Windows extend to 2 years once we verify the BS inversion produces stable metrics on older rows.

## Decisions deferred

- Tier upgrade for chain-discovery beyond 7 days — email `dev@unusualwhales.com` only if the algorithmic enumeration turns out to be too slow or misses too many contracts. Revisit in Stage 1 after first backfill.
- Intraday historical data validation — defer until v2 intraday strategies are on the roadmap.
- Alternate data sources (Polygon options tier, ORATS, CBOE DataShop) — not needed for v1, reserve as fallback if UW enumeration fails in practice.

## Conclusion

**Phase 0b passes.** The v3 design does not need to fork. The three workarounds are all mechanical additions to the existing architecture, not architectural changes. Proceeding to spec.
