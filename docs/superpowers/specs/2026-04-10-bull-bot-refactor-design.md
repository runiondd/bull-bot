# Bull-Bot v3 — Refactor Design Spec

**Status:** Draft for approval
**Author:** Session 3 brainstorming (Dan Runion + Claude)
**Date:** 2026-04-10
**Supersedes:** `docs/ARCHITECTURE.md` v2, `docs/WORK_PLAN.md` v2

---

## 1. Problem statement

Bull-Bot is a long-running Python application that automates **per-ticker options strategy discovery, validation, and execution** for a bootstrap trading account. The goal is $10,000/month real-money PnL by mid-July 2026 on a fixed $50,000 starting account.

The v2 architecture framed the system as a research-agent + decision-agent + evolver pipeline with a unified backtest engine. Session 3 brainstorming surfaced a reframing: **the evolver is not a phase of the system, it IS the system.** A single `evolver_iteration(ticker)` function wraps data-fetch → proposal → backtest → outcome-recording, is called in a loop across a curated ticker universe, and produces a per-ticker verdict: `edge_found`, `paper_trial`, `live`, `no_edge`, or `killed`. All other architectural machinery exists to serve that primitive.

This spec replaces v2. It is the contract for Stage 1 build.

---

## 2. Goals and non-goals

### Goals
- Discover, validate, paper-trade, and live-trade per-ticker options strategies with zero human intervention between the `discovering` and `paper_trial` phases.
- Produce a statistically defensible "edge" verdict for each ticker in the universe within the three-month window.
- Keep the discovery loop cheap enough (LLM + data cost) to iterate aggressively without burning the research budget.
- Guarantee capital safety via a layered kill switch that halts trading on daily loss, total drawdown, or runaway research spend.
- Make every backtest deterministic and reproducible so the evolver's memory (failed-proposal history) is trustworthy.

### Non-goals for v1
- Real broker fills (v1 uses a simulated fill model for both `paper` and `live` `run_id`s; real broker integration is v2).
- Intraday / 0DTE / short-dated-within-one-day strategies (v1 is daily-timeframe only).
- Continuous optimization of already-live strategies (v1 freezes a strategy on promotion; re-optimization requires manual re-discovery).
- Multi-machine deployment, distributed workers, or parallelism beyond a single process.
- News, earnings, macro regime, or UW flow inputs to the research agent (v1 uses OHLC + technical indicators + greeks + IV only; richer inputs are labeled extension points for v2).
- Automatic liquidation of open positions when the kill switch trips (v1 freezes new trades; Dan manually reviews open positions).

---

## 3. Success criteria

**Primary (product):** By 2026-07-10, at least one ticker in the universe has been promoted to `live` phase and has produced positive net realized PnL on the real account. Stretch: cumulative live PnL is on a trajectory that extrapolates to $10,000/month within the next 30 days.

**Secondary (system):** By end of Stage 1 (two weeks from spec approval), the system can run a full end-to-end discovery cycle on SPY from an empty database to either `edge_found` or `no_edge` with zero manual intervention, and the frozen-backtest regression test passes bit-exactly across commits.

**Discovery pipeline gate (per-ticker):** A ticker is declared `edge_found` and promoted to `paper_trial` only if its best proposal clears:
- Profit factor ≥ 1.5 in-sample on the training window
- Profit factor ≥ 1.3 out-of-sample on the holdout window
- Trade count ≥ 30 total across walk-forward folds
- All metrics computed net of commissions and slippage

**Promotion gate (paper → live):** A `paper_trial` ticker is promoted to `live` only if, after 21 trading sessions of paper trading:
- Paper-trade count ≥ 10
- All of the last 5 faithfulness checks pass (`|paper_PF − backtest_PF| / backtest_PF ≤ 0.30`)
- Paper max drawdown ≤ backtest max drawdown × 1.5

**Kill switch triggers:** Any of the following halts new trades and halts new LLM calls system-wide:
- Single-day realized loss on `run_id='live'` ≥ $1,500 (3% of $50k)
- Peak-to-trough drawdown on `run_id='live'` ≥ $5,000 (10%)
- Cumulative LLM spend since start ≥ $1,000 with zero tickers in `live` phase

---

## 4. Architecture overview

### 4.1 Shape

Bull-Bot v3 is **one long-running Python process** driven by a single scheduler. The process has exactly one core primitive — `evolver_iteration(ticker)` — called in a loop across the curated universe. Every other component exists to serve that primitive.

```
┌────────────────────────────────────────────────────────────────┐
│                  Bull-Bot v3 (single process)                   │
│                                                                  │
│   ┌──────────────┐                                               │
│   │  Scheduler   │  drives the outer loop                        │
│   │              │  - discovery pass (continuous)                │
│   │              │  - live tick (market hours, per cadence)      │
│   │              │  - end-of-day paper+live mark                 │
│   └──────┬───────┘                                               │
│          │                                                        │
│          ▼                                                        │
│   ┌──────────────────────────────────────────────┐                │
│   │  evolver_iteration(ticker) — THE ALGORITHM    │                │
│   │                                                │                │
│   │  1. Load ticker_state + evolver_proposals      │                │
│   │  2. Build feature snapshot (OHLC + indicators  │                │
│   │     + ATM greeks + IV rank) from cache         │                │
│   │  3. Dedup short-circuit on params_hash         │                │
│   │  4. Opus call: propose rule-param delta OR new │                │
│   │     strategy, given history of past proposals  │                │
│   │  5. Compile proposal into a Strategy object    │                │
│   │  6. walkforward.run across IS/OOS folds via    │                │
│   │     engine.step(cursor) — collect metrics      │                │
│   │  7. Write proposal + metrics to                │                │
│   │     evolver_proposals                          │                │
│   │  8. Update ticker_state (best-so-far, plateau  │                │
│   │     counter, verdict if gate passed/failed)    │                │
│   │  9. Log LLM + data costs to cost_ledger        │                │
│   └──────────┬───────────────────────────────────┘                │
│              │                                                     │
│              ▼                                                     │
│   ┌──────────────────────────────────────────────┐                │
│   │  engine.step(cursor) — unified execution      │                │
│   │  cursor = historical bar → backtest mode      │                │
│   │  cursor = "now"            → live mode        │                │
│   │  Same code path. Writes to paper_ledger,      │                │
│   │  live_ledger, or bt:<uuid> based on run_id.   │                │
│   └──────────┬───────────────────────────────────┘                │
│              │                                                     │
│              ▼                                                     │
│   ┌──────────────────────────────────────────────┐                │
│   │  SQLite (WAL mode, single file)               │                │
│   │  bars · option_contracts · iv_surface         │                │
│   │  strategies (with params_hash + class_version)│                │
│   │  evolver_proposals · ticker_state · ledgers   │                │
│   │  cost_ledger · kill_state · faithfulness      │                │
│   └──────────────────────────────────────────────┘                │
│                                                                    │
│   ┌──────────────┐        ┌──────────────┐                         │
│   │ Data fetchers│        │ Kill switch  │ (pre-trade + post-trade)│
│   │ UW + Polygon │        │ + launchd    │                         │
│   └──────────────┘        └──────────────┘                         │
└────────────────────────────────────────────────────────────────┘
```

### 4.2 Three execution modes, one algorithm

- **Discovery mode** (`ticker_state.phase = 'discovering'`): evolver proposes strategies, backtests them on walk-forward windows, updates `evolver_proposals`. Runs continuously off-hours and between live ticks.
- **Paper mode** (`phase = 'paper_trial'`): every live tick, `engine.step('now')` paper-executes the frozen best strategy, writes to `paper_ledger` with `run_id='paper'`. A nightly job computes paper-vs-backtest faithfulness.
- **Live mode** (`phase = 'live'`): same as paper but writes to `live_ledger` with `run_id='live'`. v1 uses the same simulated fill model; v2 swaps in real broker fills.

The key reframe from v2: **there is no separate live loop.** `engine.step(cursor)` is the single execution primitive. Discovery, paper, and live differ only in (a) what cursors the scheduler passes and (b) which ledger gets written.

### 4.3 Key data/LLM facts anchoring the design

From Phase 0 and Phase 0b validation:

- **Data source:** Unusual Whales is primary. Polygon is fallback for intraday if needed.
  - UW has 10y daily OHLC for liquid underlyings.
  - UW `/api/option-contract/{id}/historic` returns full contract lifetime (~260 daily rows) for expired contracts, with 100% populated `nbbo_bid`/`nbbo_ask`.
  - UW `/api/stock/{ticker}/option-chains?date=X` is gated to the trailing 7 trading days — we work around this via algorithmic symbol enumeration.
  - Historical IV is well-populated on recent contracts (>95%) but sparse on 22-month-old contracts (30%). We fill gaps via Black-Scholes IV inversion from the mid price.
  - UW does not return greeks in the historic endpoint. We compute delta/gamma/theta/vega analytically from IV + spot + strike + time-to-expiry.
- **LLM:** Claude Opus 4.6 for the single proposer call site. Sonnet 4.6 as config-flag fallback. Haiku is not used (Phase 0: 0/5 JSON valid). Opus is pending Phase 0a validation on Bull-Bot's actual prompt shape before Stage 1 lights up the evolver.
- **Cost posture:** Trading capital is fixed at $50k. LLM research budget is elastic — quality dominates cost at the proposer site.

---

## 5. Component inventory

The system is organized into nine logical groups of modules: config/clock, data, features, strategies, engine, backtest, evolver, risk, and top-level (scheduler, nightly, cli, main). Each module has one job, one clear interface, and can be understood and tested independently.

| Module | Purpose |
|---|---|
| `bullbot/config.py` | Single source of config truth (universe, cadence, edge thresholds, kill numbers, model IDs). No logic. |
| `bullbot/clock.py` | Market calendar wrapper (`pandas_market_calendars` or hardcoded NYSE list for v1), UTC↔ET conversion, market-open/close queries. All DB timestamps are UTC epoch seconds. |
| `bullbot/data/fetchers.py` | Thin UW + Polygon HTTP clients. One function per endpoint, retry/backoff/rate-limit handling, cost_ledger writes on every call. |
| `bullbot/data/cache.py` | Read-through cache between fetchers and the rest of the system. Cache TTL rules (see §6.4). |
| `bullbot/data/schemas.py` | Pydantic models for `Bar`, `OptionContract`, `Greeks`, `IVSurfacePoint`. No raw dicts escape the data layer. |
| `bullbot/data/options_backfill.py` | One-time bulk fetcher. Given a ticker + date range, enumerates expected option symbols via the OSI calendar/strike grid, calls `/historic` per symbol, writes to `option_contracts`. Stage 1 prerequisite. |
| `bullbot/features/indicators.py` | Pure functions over `list[Bar]`: SMA, EMA, RSI, ATR, Bollinger, IV rank, IV percentile, ATM implied move. |
| `bullbot/features/greeks.py` | Pure functions: `implied_volatility(mid, strike, spot, t, r, is_put)` via scipy `brentq` on Black-Scholes; `greeks(iv, strike, spot, t, r, is_put)` closed-form. |
| `bullbot/features/regime.py` | Pure function: `classify(bars_60d)` returns one of `"bull"`, `"bear"`, `"chop"`. Thresholds pinned in §6.7. |
| `bullbot/strategies/base.py` | `Strategy` abstract class with `evaluate(snapshot, open_positions)` returning `Signal` or `None`. Rule-based, deterministic at execution time. |
| `bullbot/strategies/registry.py` | Maps strategy IDs to classes; serializes to/from SQLite; seed library (§6.5). |
| `bullbot/strategies/put_credit_spread.py`, `call_credit_spread.py`, `iron_condor.py`, `cash_secured_put.py`, `long_call.py`, `long_put.py` | Six seed strategy implementations. Daily-timeframe only. |
| `bullbot/engine/step.py` | Unified execution primitive: `step(cursor, ticker, strategy, run_id)` returns `StepResult`. Feature-snapshot build, signal, fill, ledger write. |
| `bullbot/engine/fill_model.py` | Simulates options fills: mid ± half-spread ± 1-tick slippage per leg. Applies $0.65/contract commission. |
| `bullbot/engine/position_sizer.py` | Pure function: given strategy max-loss-per-contract and current equity, returns contract count for fixed 2%-of-equity-at-risk. |
| `bullbot/backtest/walkforward.py` | `run_walkforward(strategy, ticker, windows)` returns `BacktestMetrics`. Anchored 70/30 split, 3–5 folds, aggregates PF/Sharpe/max-DD/trade count. |
| `bullbot/evolver/iteration.py` | `evolver_iteration(ticker)`. The single discovery entry point. |
| `bullbot/evolver/proposer.py` | Opus wrapper. Builds prompt from snapshot + last 15 proposals, parses JSON response, retries once on malformed output. |
| `bullbot/evolver/plateau.py` | Pure function: `classify(ticker_state, new_metrics)` returns one of `"continue"`, `"no_edge"`, `"edge_found"`. |
| `bullbot/scheduler.py` | Outer loop. Dispatches each ticker to the right action based on `ticker_state.phase`. Enforces cadence per phase. Catches per-ticker exceptions. |
| `bullbot/risk/cost_ledger.py` | Append-only billing log. `can_afford(cost)` gate before any LLM or data call. |
| `bullbot/risk/kill_switch.py` | Watches ledgers for trip conditions. On trip: flips live tickers to `killed`, writes report, pages Dan. |
| `bullbot/nightly.py` | End-of-day mark-to-market, faithfulness check, promotion eligibility, nightly kill check, nightly report writer. |
| `bullbot/cli.py` | Operator commands: `status`, `rearm`, `add-ticker`, `retire-ticker`, `force-iteration`, `show-proposals`. The surface Dan uses to interact with the running system. |
| `bullbot/main.py` | Process entry point. Initializes DB, wires scheduler, enters main loop. Top-level exception handling. |

**Deliberately absent:** message queue, async workers, ORM, separate performance/attribution agent, 5-cursor live decision agent, signals table, regime_labels table, strategy_versions table.

---

## 6. Design details

### 6.1 Per-ticker state machine

Five phases, four transitions, one override.

```
discovering ──T1(edge_found)──▶ paper_trial ──T3(promote)──▶ live
     │                              │                          │
     └──T2(no_edge)──▶ no_edge       └──T4(demote)──────┐       │
                                                         │       │
                                                         ▼       │
                                                    discovering  │
                                                                 │
                             T5 (kill switch, any ticker) ◀──────┘
                             live ──▶ killed
```

**T1 `discovering → paper_trial`** (inside `evolver_iteration` step 8)
Trigger: latest proposal's metrics pass the edge gate (PF_is ≥1.5, PF_oos ≥1.3, n_trades ≥30).
Side effects: set `best_strategy_id` = proposal's strategy, `paper_started_at = now()`, reset `paper_trade_count = 0`.

**T2 `discovering → no_edge`** (inside `evolver_iteration` step 8)
Trigger: `plateau_counter ≥ 3` after a proposal failed to improve best-so-far PF by ≥ 0.1, OR `iteration_count ≥ 50` safety cap.
Terminal but recoverable: deleting the `ticker_state` row reinitializes the ticker as `discovering` with a fresh counter.

**T3 `paper_trial → live`** (inside `nightly.py`)
Trigger: `days_since(paper_started_at) ≥ 21` AND `paper_trade_count ≥ 10` AND last 5 faithfulness checks all passed AND `paper_max_dd ≤ backtest_max_dd × 1.5`.
Side effects: set `live_started_at = now()`, keep `best_strategy_id` frozen.

**T4 `paper_trial → discovering`** (inside `nightly.py`)
Trigger: `paper_trade_count ≥ 10` AND at least one of the last 5 faithfulness checks failed.
Side effects: reset `plateau_counter = 0`, clear `paper_started_at`, clear `paper_trade_count`. Evolver memory preserved.

**T5 `live → killed`** (inside `kill_switch.trip()`, global override)
Trigger: any of the three kill conditions (daily loss, total DD, research ratthole).
Side effects: all `live` tickers flipped to `killed` atomically. Open live positions are NOT auto-flattened in v1 (human review). Re-arm is manual via CLI and always routes back through `paper_trial`, never directly to `live`.

**Invariants:**
1. A ticker is in exactly one phase at any instant.
2. Phase transitions are atomic SQLite transactions.
3. Phase writes happen in exactly three files: `evolver/iteration.py` (T1/T2), `nightly.py` (T3/T4), `risk/kill_switch.py` (T5).
4. Terminal phases (`no_edge`, `killed`) are terminal within automation. Recovery is always a deliberate human action.
5. `evolver_proposals` persists across phase transitions — a ticker demoted via T4 still sees its full proposal history on the next `evolver_iteration` call.

### 6.2 Edge gate and walk-forward windows

**Walk-forward configuration (pinned):**
- Anchored walk-forward with 70/30 train/test split.
- Base window: 24 months of daily data from v1 startup (see §6.6 for the hybrid native/inverted IV policy that makes this safe).
- Step size: 30 calendar days.
- Minimum folds: 3. Maximum folds: 5.
- PF_is = weighted PF across all train segments; PF_oos = weighted PF across all holdout segments. Weights = trade count per segment.

**Edge gate (pinned):**
- `PF_is ≥ 1.5` AND `PF_oos ≥ 1.3`
- Total `n_trades ≥ 30` across all holdout segments
- All metrics computed net of commissions + slippage (§6.3)

### 6.3 Fill model, commissions, slippage

**Simulate fill** (`engine/fill_model.py`):
- Entry price per leg = `bid + 0.5 × (ask − bid) − 0.01` for short legs, `bid + 0.5 × (ask − bid) + 0.01` for long legs. (Mid ± one tick, worse side.)
- Commission = `$0.65 × contracts × legs` (IBKR standard) applied once on open, once on close.
- If any leg has `bid == 0` or `ask == 0` or `spread > MIN_SPREAD_FRAC × mid` (default `0.50`), the fill is rejected and the signal is skipped. The 0.50 ceiling is loose on purpose — cheap OTM options frequently trade with 30–40% spreads and blanket-rejecting them would eliminate most credit-spread opportunities. Tighten in v2 once we see real fill data.

**Position sizing** (`engine/position_sizer.py`):
- Fixed 2% of current equity at risk per position: `max_contracts = floor( (0.02 × equity) / max_loss_per_contract )`.
- `equity = initial_capital + sum(realized_pnl for run_id='live') + sum(mark_to_mkt for run_id='live' open positions)`.
- Minimum 1 contract. If 1 contract exceeds the 2% cap, the signal is skipped and logged.
- Max concurrent positions per ticker: 3 (prevents stacking on chop).
- Max concurrent positions across universe: 10.

### 6.4 Data cache TTL rules

| Table | Cache key | TTL |
|---|---|---|
| `bars` (1d) | `(ticker, 1d, date)` | Stale for *today* until EOD + 15 min; stale for past dates never |
| `bars` (1h, 15m) | `(ticker, timeframe, ts)` | Stale after `timeframe` interval |
| `option_contracts` | `(ticker, expiry, strike, kind, ts)` | Stale after 1 minute during RTH, never stale outside |
| `iv_surface` | `(ticker, ts)` | Stale after 5 minutes during RTH |

All writes also append to `cost_ledger` with `category='data_uw'` or `data_polygon'` and the computed cost (UW charges nothing per call; Polygon calls logged at zero amount for now).

### 6.5 Strategy seed library

Stage 1 ships six daily-timeframe strategy classes. The evolver's iteration-1 prompt on a fresh ticker uses these as seeds; it can then propose parameter deltas on existing ones or new subclasses in later iterations.

| Strategy | Class name | Default params | Notes |
|---|---|---|---|
| Put credit spread | `PutCreditSpread` | `dte=14, short_delta=0.25, width=5, iv_rank_min=50` | Short-vol, income-style |
| Call credit spread | `CallCreditSpread` | `dte=14, short_delta=0.25, width=5, iv_rank_min=50` | Short-vol, bearish bias |
| Iron condor | `IronCondor` | `dte=21, wing_delta=0.20, wing_width=5, iv_rank_min=60` | Market-neutral, range-bound |
| Cash-secured put | `CashSecuredPut` | `dte=30, target_delta=0.30, iv_rank_min=40` | Directional, income |
| Long call | `LongCall` | `dte=45, delta=0.60` | Directional, debit |
| Long put | `LongPut` | `dte=45, delta=0.60` | Directional, debit |

All six are implemented as `Strategy` subclasses. Each exposes its `max_loss_per_contract()` so the position sizer can compute contract counts.

### 6.6 Historical options data handling (from Phase 0b)

**Symbol enumeration:** `options_backfill.py` constructs candidate option symbols per ticker per date range using the OSI regex format and a hardcoded expiry calendar (M/W/F weeklies, 3rd-Friday monthlies, quarterlies, EOM). Strike grid: $1 near ATM (±20%), $2.5 mid (±40%), $5 far. Invalid symbols return empty from `/historic` and are skipped.

**Backfill cost per ticker:** ~24,000 candidate symbols × ~10 rps = ~40 min one-time fetch. Full 10-ticker universe: ~6 hours.

**IV inversion (hybrid policy):** `features/greeks.py::implied_volatility()` uses `scipy.optimize.brentq` to solve Black-Scholes for IV given mid price, strike, spot, time-to-expiry, and risk-free rate (0.045 hardcoded for v1; v2 may pull from treasury). The policy is **native-first, inverted-on-fallback**: feature-building always prefers UW's `implied_volatility` when populated, and only calls the inverter on rows where the native value is null. Recent data (past ~12 months where native IV is >95% populated) is effectively inversion-free; only older historical rows use the inverter.

**Analytic greeks:** `features/greeks.py::greeks()` returns delta/gamma/theta/vega via Black-Scholes closed form. Always called (never read from UW, since UW does not return greeks in `/historic`). Delta/gamma/theta/vega are computed from whichever IV was used (native or inverted).

**Backtest window policy:** v1 runs on a 24-month window from the start. The hybrid IV policy bounds inversion error — most fills on recent data use native IV, only fills on rows 12+ months old rely on inversion. A Week 2 regression test validates the boundary: on the 12-month overlapping-coverage window, compute PF twice for a frozen strategy — once forcing inverted-IV, once forcing native-IV — and assert the two agree within ±5% on SPY/QQQ/IWM (European-style index options, where Black-Scholes is the correct model) and within ±10% on single-stock tickers (AAPL/MSFT/etc., where American-style early exercise makes BS an approximation). If the regression fails, the evolver's prompt flags older-period proposals as "lower confidence" via a metadata field, but the 24-month window is retained. Falling back to a 12-month window is a last resort only if the regression fails catastrophically (>15% PF delta), which would indicate a bug in the inverter rather than a data problem.

**Walk-forward fold math (on 24-month window):** anchored walk-forward, train window starts at the oldest bar and grows by 30-day increments, each fold appends a 30-day out-of-sample segment. Yields ~8 folds with ~240 total OOS trading days. At even 1 trade/week per ticker, that's ~50 OOS trades — comfortably above the `n ≥ 30` edge gate.

### 6.7 Regime classification

Pinned algorithm (`features/regime.py::classify`):

```
rolling_60d_return = (close[-1] - close[-60]) / close[-60]
rolling_30d_vol = stddev(pct_change(close[-30:])) * sqrt(252)

if rolling_60d_return >= 0.05 and rolling_30d_vol < 0.20:
    return 'bull'
elif rolling_60d_return <= -0.05:
    return 'bear'
else:
    return 'chop'
```

These thresholds are pinned in v1 and never changed without invalidating all historical `regime_breakdown` values in `evolver_proposals`.

### 6.8 Kill switch

**Trip conditions** (all checked nightly and before every intraday tick):

| # | Trigger | Check | Reason code |
|---|---|---|---|
| 1 | Daily realized loss | `sum(pnl_realized WHERE run_id='live' AND closed_at >= today_open_et) ≤ -1500` | `daily_loss` |
| 2 | Total drawdown | `peak_equity − current_equity ≥ 5000` | `total_dd` |
| 3 | Research ratthole | `sum(cost_ledger.amount_usd WHERE category='llm') ≥ 1000 AND count(phase='live' tickers) == 0` | `research_ratthole` |

**Trip sequence:**
1. Open transaction.
2. Set `kill_state.active = 1`, `tripped_at = now()`, `reason`, `trigger_rule`.
3. Flip every `phase='live'` ticker to `phase='killed'`.
4. Commit.
5. Outside transaction: write `reports/kill_YYYY-MM-DDTHH-MM.md` with full context.
6. Emit `KILLED` banner to structured log + flush.

**Re-arm (manual only):**
- Dan runs `python -m bullbot.cli rearm --ticker SYMBOL --acknowledge-risk`.
- CLI reads the kill report, asks explicit confirmation of the trigger.
- On confirmation: clear `kill_state.active`, flip specified ticker to `paper_trial` (not directly to `live`), reset `paper_started_at = now()`.

**What the kill switch does NOT do in v1:** flatten open live positions, cancel broker orders, stop discovery on untouched tickers (only the research ratthole trigger does that).

---

## 7. Data model (SQLite, WAL mode, strict mode)

Twelve tables. Full DDL in §11. Highlights:

- **`bars`** — cached OHLC by `(ticker, timeframe, ts)`. `source` = `'uw'` or `'polygon'`.
- **`option_contracts`** — per-contract per-day history: OHLC, `nbbo_bid`, `nbbo_ask`, `implied_volatility`, `volume`, `open_interest`. PK `(ticker, expiry, strike, kind, ts)`.
- **`iv_surface`** — per-ticker per-day IV rank, percentile, ATM IV, implied move.
- **`strategies`** — every strategy+params combo ever tried. `UNIQUE (class_name, class_version, params_hash)` prevents re-backtesting identical proposals.
- **`evolver_proposals`** — append-only history of every proposal per ticker with its backtest outcome. This IS the evolver's memory.
- **`ticker_state`** — denormalized view of current phase + counters + best-so-far per ticker. Singleton per ticker.
- **`orders`**, **`positions`** — partitioned by `run_id` (`'paper' | 'live' | 'bt:<uuid>'`). Same schema for all three.
- **`cost_ledger`** — append-only billing log by `(ts, category)`.
- **`kill_state`** — singleton row via `CHECK (id=1)`.
- **`faithfulness_checks`** — per-ticker daily paper-vs-backtest PF deltas.
- **`iteration_failures`** — per-exception log outside the main transaction so it persists on rollback.

**Key constraint (the dedup hash):**
```sql
CREATE TABLE strategies (
  id            INTEGER PRIMARY KEY,
  class_name    TEXT NOT NULL,
  class_version INTEGER NOT NULL,      -- bumped manually on strategy code changes
  params        TEXT NOT NULL,         -- canonicalized JSON (sorted keys, no whitespace)
  params_hash   TEXT NOT NULL,         -- SHA1 of canonicalized params
  parent_id     INTEGER REFERENCES strategies(id),
  created_at    INTEGER NOT NULL,
  UNIQUE (class_name, class_version, params_hash)
) STRICT;
```

Before running a backtest, `evolver_iteration` computes `params_hash`, looks up `(class_name, class_version, params_hash)` in `strategies`, and if a matching row exists AND has an `evolver_proposals` row for the current ticker, records a duplicate-proposal row and skips the backtest. This is the single most important performance optimization in the evolver — without it, Opus will waste iterations proposing near-duplicates.

---

## 8. Execution flows

### 8.1 Discovery iteration (abridged)

```
scheduler.tick()
  ├─ kill_switch.is_tripped() → False
  ├─ ticker_state.phase = 'discovering'
  └─ evolver_iteration.run("AAPL")
       1. Load ticker_state + last 15 proposals
       2. Build feature snapshot (OHLC + indicators + greeks + IV rank)
       3. Opus proposer call → Proposal(class_name, params, rationale)
       4. Dedup check: if (class_name, class_version, params_hash) already
          backtested for this ticker → record duplicate, return
       5. Insert strategies row → strategy_id
       6. walkforward.run(strategy, ticker, windows) → metrics
       7. plateau.classify(ticker_state, metrics) → continue | edge_found | no_edge
       8. Atomic SQLite transaction:
          - insert evolver_proposals row
          - update ticker_state (phase, counters, best-so-far)
          - insert cost_ledger rows (llm + data)
```

### 8.2 Market-hours tick (paper or live)

The same code path runs for both `paper_trial` and `live` phases. The only difference is `run_id` (`"paper"` vs `"live"`) and which ledger the rows land in. The example below traces a `paper_trial` tick; `live` is identical modulo the run_id swap.

```
scheduler.tick() [every 15 min during RTH]
  ├─ kill_switch.is_tripped() → False
  ├─ ticker_state.phase = 'paper_trial' (or 'live')
  └─ engine.step("now", ticker="NVDA", strategy_id=312, run_id="paper")
       1. Load strategy + open positions for run_id
       2. Build snapshot at "now"
       3. signal = strategy.evaluate(snapshot, open_positions)
       4. If signal.intent == 'open':
          - contracts = position_sizer.size(strategy, equity)
          - legs = strategy.build_legs(signal, chain)
          - fill = fill_model.simulate(legs) + commission
          - insert orders, positions rows
          - ticker_state.paper_trade_count += 1
       5. If signal.intent == 'close':
          - fill = fill_model.simulate_close(position)
          - update positions (closed_at, pnl_realized)
          - insert orders row
```

### 8.3 Nightly (abridged)

```
scheduler nightly hook [after market close]
  1. For each active ticker:
     - Mark-to-market open positions
     - If phase='paper_trial' and days_since(paper_started_at) >= FAITHFULNESS_MIN_DAYS (5):
         faithfulness check → insert faithfulness_checks row
     - If phase='paper_trial' and days_since(paper_started_at) >= 21:
         check promotion eligibility → T3 (promote) or T4 (demote)
  2. Kill switch full recompute (daily loss, total DD, research ratthole)
  3. Write reports/nightly_YYYY-MM-DD.md
```

---

## 9. Error handling and supervision

**Ring 1 — per-call.** Retries with exponential backoff on 429/5xx for data fetchers; 1 corrective retry on malformed JSON for the proposer; typed errors (`DataFetchError`, `DataSchemaError`, `ProposerJsonError`, `ProposerApiError`, `ProposerBudgetError`, `ProposerUnknownStrategyError`, `EngineStateError`). Non-retryable errors raise immediately.

**Ring 2 — per-iteration.** Every `evolver_iteration` runs inside a single SQLite transaction at the outermost layer. On exception, the transaction rolls back and the scheduler logs to `iteration_failures` (separate auto-commit connection) before continuing to the next ticker. Cost_ledger writes for LLM calls happen in the separate auto-commit connection *before* the main transaction starts, so billing is captured even on rollback.

**Ring 3 — per-process.** `scheduler.tick()` catches all per-ticker exceptions and continues to the next ticker. Anything that escapes the scheduler kills the process. Supervision via `launchd` + `caffeinate -i`:

```
launchd (KeepAlive=true, ThrottleInterval=30)
  └─ caffeinate -i python -m bullbot.main
```

No separate watchdog daemon in v1 — a hang (vs crash) will not be auto-detected. v2 can add a heartbeat file check if this proves to be a problem.

---

## 10. Testing strategy

**Tier 1 — unit (pytest, <5s).** Pure functions only. Indicators, `plateau.classify`, strategy `evaluate` with synthetic snapshots, fill model, kill switch `should_trip_now`, Pydantic validation, IV inversion, greeks, regime classification, position sizer.

**Tier 2 — integration (pytest, <60s).** SQLite in-memory, mocked external APIs. Full `evolver_iteration` with canned Opus response, engine.step in backtest + paper mode, state-machine transitions (T1/T2/T3/T4/T5), nightly pipeline on fixture data, kill-switch trip + re-arm flow.

**Tier 3 — backtest-as-regression (pytest, <60s, the load-bearing tier).** Frozen strategy + frozen fixture OHLC + frozen fixture options data for SPY 2023–2024 → assert BacktestMetrics match golden values bit-exactly. Runs on every commit. This is what detects silent changes to engine, strategy, or fill-model behavior. Fixture is committed under `tests/fixtures/spy_regression_2023_2024.parquet` with a checksum.

**Tier 4 — end-to-end smoke (manual, ~$0.15/run).** `scripts/smoke_test.py` runs 3 real Opus iterations on SPY against a sandbox DB with real UW fetches. Run before merging any branch touching `data/`, `evolver/`, `engine/`, or `risk/`.

**Not in v1:** GitHub Actions CI (Tiers 1–3 run locally via pytest); code coverage percentage targets (targeting invariant coverage, not line coverage).

---

## 11. Schema DDL (full)

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE bars (
  ticker     TEXT NOT NULL,
  timeframe  TEXT NOT NULL,
  ts         INTEGER NOT NULL,
  open       REAL, high REAL, low REAL, close REAL, volume INTEGER,
  source     TEXT NOT NULL,
  PRIMARY KEY (ticker, timeframe, ts)
) STRICT;

CREATE TABLE option_contracts (
  ticker     TEXT NOT NULL,
  expiry     TEXT NOT NULL,
  strike     REAL NOT NULL,
  kind       TEXT NOT NULL CHECK (kind IN ('C', 'P')),
  ts         INTEGER NOT NULL,
  nbbo_bid   REAL, nbbo_ask REAL, last REAL,
  volume     INTEGER, open_interest INTEGER,
  iv         REAL,
  PRIMARY KEY (ticker, expiry, strike, kind, ts)
) STRICT;

CREATE TABLE iv_surface (
  ticker         TEXT NOT NULL,
  ts             INTEGER NOT NULL,
  iv_rank        REAL, iv_percentile REAL, atm_iv REAL, implied_move REAL,
  PRIMARY KEY (ticker, ts)
) STRICT;

CREATE TABLE strategies (
  id            INTEGER PRIMARY KEY,
  class_name    TEXT NOT NULL,
  class_version INTEGER NOT NULL,
  params        TEXT NOT NULL,
  params_hash   TEXT NOT NULL,
  parent_id     INTEGER REFERENCES strategies(id),
  created_at    INTEGER NOT NULL,
  UNIQUE (class_name, class_version, params_hash)
) STRICT;

CREATE TABLE evolver_proposals (
  id                INTEGER PRIMARY KEY,
  ticker            TEXT NOT NULL,
  iteration         INTEGER NOT NULL,
  strategy_id       INTEGER NOT NULL REFERENCES strategies(id),
  parent_strategy_id INTEGER REFERENCES strategies(id),
  rationale         TEXT,
  llm_cost_usd      REAL NOT NULL,
  pf_is             REAL, pf_oos REAL,
  sharpe_is         REAL, max_dd_pct REAL,
  trade_count       INTEGER,
  regime_breakdown  TEXT,
  passed_gate       INTEGER NOT NULL,
  created_at        INTEGER NOT NULL,
  UNIQUE (ticker, iteration)
) STRICT;

CREATE INDEX idx_proposals_ticker_iter ON evolver_proposals(ticker, iteration DESC);

CREATE TABLE ticker_state (
  ticker              TEXT PRIMARY KEY,
  phase               TEXT NOT NULL CHECK (phase IN ('discovering','paper_trial','live','no_edge','killed')),
  retired             INTEGER NOT NULL DEFAULT 0,
  best_strategy_id    INTEGER REFERENCES strategies(id),
  best_pf_is          REAL, best_pf_oos REAL,
  plateau_counter     INTEGER NOT NULL DEFAULT 0,
  iteration_count     INTEGER NOT NULL DEFAULT 0,
  cumulative_llm_usd  REAL NOT NULL DEFAULT 0,
  paper_started_at    INTEGER,
  paper_trade_count   INTEGER NOT NULL DEFAULT 0,
  live_started_at     INTEGER,
  verdict_at          INTEGER,
  updated_at          INTEGER NOT NULL
) STRICT;

CREATE TABLE orders (
  id           INTEGER PRIMARY KEY,
  run_id       TEXT NOT NULL,
  ticker       TEXT NOT NULL,
  strategy_id  INTEGER NOT NULL REFERENCES strategies(id),
  placed_at    INTEGER NOT NULL,
  legs         TEXT NOT NULL,
  intent       TEXT NOT NULL CHECK (intent IN ('open', 'close')),
  status       TEXT NOT NULL,
  commission   REAL NOT NULL DEFAULT 0,
  pnl_realized REAL
) STRICT;

CREATE INDEX idx_orders_run_ticker ON orders(run_id, ticker, placed_at);

CREATE TABLE positions (
  id           INTEGER PRIMARY KEY,
  run_id       TEXT NOT NULL,
  ticker       TEXT NOT NULL,
  strategy_id  INTEGER NOT NULL REFERENCES strategies(id),
  opened_at    INTEGER NOT NULL,
  closed_at    INTEGER,
  legs         TEXT NOT NULL,
  contracts    INTEGER NOT NULL,
  open_price   REAL NOT NULL,
  close_price  REAL,
  pnl_realized REAL,
  mark_to_mkt  REAL
) STRICT;

CREATE INDEX idx_positions_run_ticker_open ON positions(run_id, ticker, opened_at);

CREATE TABLE cost_ledger (
  id          INTEGER PRIMARY KEY,
  ts          INTEGER NOT NULL,
  category    TEXT NOT NULL CHECK (category IN ('llm','data_uw','data_polygon','order_commission')),
  ticker      TEXT,
  amount_usd  REAL NOT NULL,
  details     TEXT
) STRICT;

CREATE INDEX idx_cost_ts ON cost_ledger(ts);
CREATE INDEX idx_cost_ticker ON cost_ledger(ticker, ts);

CREATE TABLE kill_state (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  active        INTEGER NOT NULL DEFAULT 0,
  tripped_at    INTEGER,
  reason        TEXT,
  trigger_rule  TEXT
) STRICT;

CREATE TABLE faithfulness_checks (
  id              INTEGER PRIMARY KEY,
  ticker          TEXT NOT NULL,
  checked_at      INTEGER NOT NULL,
  window_days     INTEGER NOT NULL,
  paper_pf        REAL,
  backtest_pf     REAL,
  delta_pct       REAL,
  passed          INTEGER NOT NULL
) STRICT;

CREATE TABLE iteration_failures (
  id          INTEGER PRIMARY KEY,
  ts          INTEGER NOT NULL,
  ticker      TEXT NOT NULL,
  phase       TEXT,
  exc_type    TEXT NOT NULL,
  exc_message TEXT NOT NULL,
  traceback   TEXT
) STRICT;
```

---

## 12. Config (canonical values)

```python
# bullbot/config.py — single source of config truth

UNIVERSE = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META", "GOOGL"]
UNIVERSE_RETIRED: list[str] = []   # Tickers still closing positions but no new opens

# Capital & timeline
INITIAL_CAPITAL_USD = 50_000
TARGET_MONTHLY_PNL_USD = 10_000
TARGET_DATE = "2026-07-10"

# Edge gate
EDGE_PF_IS_MIN = 1.5
EDGE_PF_OOS_MIN = 1.3
EDGE_TRADE_COUNT_MIN = 30

# Walk-forward
WF_TRAIN_FRAC = 0.70
WF_WINDOW_MONTHS = 24            # hybrid native/inverted IV policy keeps inversion error bounded
WF_STEP_DAYS = 30
WF_MIN_FOLDS = 3
WF_MAX_FOLDS = 5

# Plateau / discovery
PLATEAU_IMPROVEMENT_MIN = 0.10
PLATEAU_COUNTER_MAX = 3
ITERATION_CAP = 50
HISTORY_BLOCK_SIZE = 15          # past proposals shown to the proposer

# Promotion gate
PAPER_TRIAL_DAYS = 21
PAPER_TRADE_COUNT_MIN = 10
FAITHFULNESS_MIN_DAYS = 5        # start nightly faithfulness checks after this many days in paper
FAITHFULNESS_DELTA_MAX = 0.30
PAPER_DD_MULT_MAX = 1.5

# Kill switch
KILL_DAILY_LOSS_USD = 1_500
KILL_TOTAL_DD_USD = 5_000
KILL_RESEARCH_RATTHOLE_USD = 1_000

# Position sizing
POSITION_RISK_FRAC = 0.02
MAX_POSITIONS_PER_TICKER = 3
MAX_POSITIONS_TOTAL = 10

# Fill model
COMMISSION_PER_CONTRACT_USD = 0.65
SLIPPAGE_TICKS_PER_LEG = 1
MIN_SPREAD_FRAC = 0.50

# Regime thresholds
REGIME_BULL_RETURN_MIN = 0.05
REGIME_BEAR_RETURN_MAX = -0.05
REGIME_BULL_VOL_MAX = 0.20

# LLM
PROPOSER_MODEL = "claude-opus-4-6"
PROPOSER_MODEL_FALLBACK = "claude-sonnet-4-6"
PROPOSER_MAX_TOKENS = 2000
PROPOSER_BUDGET_CEILING_USD = 0.10

# Scheduling
TICK_INTERVAL_MARKET_SEC = 60
TICK_INTERVAL_OFFHOURS_SEC = 5
MARKET_TIMEZONE = "America/New_York"

# Risk-free rate (v1 hardcoded; v2 pulls from treasury)
RISK_FREE_RATE = 0.045
```

---

## 13. Stage 1 deliverables

**Week 1 — data + safety + skeleton:**
- [ ] Phase 0a: validate Opus 4.6 as proposer model on Bull-Bot prompt shape (5/5 JSON validity, measure cost/latency); update `reports/phase0_anthropic.md`.
- [ ] `bullbot/config.py`, `bullbot/clock.py`, `bullbot/main.py`, SQLite migrations.
- [ ] `bullbot/data/schemas.py`, `fetchers.py`, `cache.py`.
- [ ] `bullbot/data/options_backfill.py` + run backfill on SPY (first ticker) — validates the 6-hour-per-universe estimate.
- [ ] `bullbot/features/indicators.py`, `features/greeks.py`, `features/regime.py`.
- [ ] `bullbot/risk/cost_ledger.py`, `risk/kill_switch.py`.
- [ ] Tier 1 unit tests for all pure functions above.

**Week 2 — strategies + engine + evolver + CLI + nightly:**
- [ ] `bullbot/strategies/base.py`, `registry.py`, six seed classes.
- [ ] `bullbot/engine/fill_model.py`, `position_sizer.py`, `step.py`.
- [ ] `bullbot/backtest/walkforward.py`.
- [ ] `bullbot/evolver/proposer.py`, `plateau.py`, `iteration.py`.
- [ ] `bullbot/scheduler.py`, `bullbot/nightly.py`, `bullbot/cli.py`.
- [ ] Tier 2 integration tests.
- [ ] Tier 3 frozen-backtest regression test (create and commit SPY fixture).
- [ ] launchd plist for supervision.
- [ ] End-to-end smoke test: discovery → `edge_found` or `no_edge` on SPY from empty DB with zero manual intervention.

**Not in Stage 1:** real broker fills, intraday strategies, flow/news inputs to the proposer, continuous optimization of live strategies, multi-machine deployment, heartbeat watchdog, GitHub Actions CI.

---

## 14. Open questions resolved during brainstorming

All six open questions from the Session 2 handoff are resolved:
1. **Success criterion:** Income target — $10k/month real-money PnL by 2026-07-10 on $50k fixed capital.
2. **Edge definition:** PF ≥1.5 IS, ≥1.3 OOS walk-forward, n≥30 trades, net of costs.
3. **Discovery budget:** Plateau detection (rolling 3, Δ≥0.1), safety cap 50 iterations.
4. **Research inputs v1:** OHLC + indicators + ATM greeks + IV rank. Flow and news/macro are labeled v2 extension points.
5. **Promotion criteria:** 21 sessions paper + ≥10 paper trades + faithfulness within ±30% + paper DD ≤ 1.5× backtest DD.
6. **Capital base & kill thresholds:** $50k fixed; daily loss $1,500 / total DD $5,000 / research ratthole $1,000 without promoted ticker.

Plus architectural answers surfaced by hole review:
7. **Dedup hash:** `(class_name, class_version, params_hash)` UNIQUE constraint; Opus sees last 15 proposals as prompt history; `class_version` bumped manually on strategy code changes.
8. **LLM model:** Opus 4.6 for proposer (reasoning-heavy, low-volume, quality-dominates); Sonnet 4.6 fallback via config flag; Haiku unusable.
9. **Options historical data:** Resolved by Phase 0b — UW `/historic` works on expired contracts, chain discovery workaround via algorithmic symbol enumeration, IV sparsity worked around via Black-Scholes inversion, greeks computed analytically.
10. **Position sizing:** Fixed 2% of current equity at risk per position.
11. **Walk-forward windows:** Anchored 70/30, 12-month base (extends to 24), 30-day step, 3–5 folds.
12. **Commissions + slippage:** $0.65/contract IBKR + 1 tick per leg slippage; reject fills on degenerate chains.
13. **Clock, calendar, timezones:** `pandas_market_calendars` wrapper; all DB times UTC epoch; display in ET.
14. **Cache TTL:** Per-timeframe rules in §6.4.
15. **Bootstrap:** `scripts/backfill.py` + `data/options_backfill.py` as Stage 1 prerequisites.
16. **Universe changes:** `ticker_state.retired` flag — retired tickers close existing positions but open no new ones.
17. **Operator interface:** `bullbot/cli.py` with `status | rearm | add-ticker | retire-ticker | force-iteration | show-proposals`.
18. **Secrets:** `.env` + `python-dotenv`, gitignored, loaded in `config.py`, fail-fast on missing keys.
19. **Regime algorithm:** Pinned in §6.7.

---

## 15. Deferred to v2 / later

- Real broker fills (IBKR API)
- Intraday / 0DTE / short-dated strategies
- Flow and news/earnings/macro inputs to the proposer
- Continuous optimization of already-live strategies
- UW historical chain-discovery tier upgrade (email dev@unusualwhales.com if symbol enumeration proves insufficient)
- Heartbeat watchdog / hang detection
- Auto-liquidation on kill switch trip
- GitHub Actions CI
- Multi-machine deployment
- Alternative data sources (Polygon options tier, ORATS, CBOE DataShop)
- Strategy class blacklisting per ticker (`ticker_state.blacklisted_classes`)
- `paused` phase
- DB backup / vacuum automation beyond daily `cp`

---

## 16. Sign-off

This spec captures the full Session 3 brainstorming output including:
- Ten decisions from the walk through the six canonical open questions
- Three architectural approaches proposed, Approach A (monolithic evolver loop + unified engine + SQLite) selected
- Seven design sections iteratively approved (architecture, components, data model, execution flows, state machine, error handling, testing)
- Thirteen holes identified in self-review, all resolved or deferred
- One critical validation (Phase 0b) run and passed mid-review

**Next step:** `superpowers:writing-plans` to produce the implementation plan for Stage 1.
