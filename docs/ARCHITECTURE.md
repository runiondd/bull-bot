# Bull-Bot Architecture

**Status:** Draft for review (v2 — backtest-as-foundation reframing)
**Version:** 2.0
**Last updated:** 2026-04-09

This document describes the system architecture for Bull-Bot: a self-improving, multi-timeframe, multi-agent paper trading simulator for stock and options research. The central idea is that **backtesting is not a feature of the system — it is the system**. Live paper trading is just "the backtest, but with today's bar as the current bar." Every strategy change has to survive a walk-forward backtest before it reaches the live loop.

---

## 1. System overview

Bull-Bot is a long-running Python application with one learning loop at its core:

1. **Execute** (live or historical) — a single unified engine drives both live paper trading and historical backtests. It takes a time cursor (either "now" or "2024-06-14 10:30 ET") and runs one full cycle: fetch features, run research agents, run the decision agent, fill orders, mark the ledger.
2. **Measure** — nightly and weekly performance analyzers attribute P&L to timeframes, agents, conviction buckets, strategy families, and market regimes.
3. **Propose** — the strategy evolver reads the attribution history and proposes numeric rule changes and (rarely) prompt changes.
4. **Validate** — every evolver proposal is run through the backtest engine on walk-forward windows before it can be approved. Proposals that don't survive holdout testing are rejected automatically.
5. **Deploy** — approved proposals become a new strategy version and start affecting live decisions on the next cycle.
6. **Audit** — a faithfulness check compares live paper P&L to what the backtest engine predicts for the same window. If they diverge, something is wrong with the engine unification and it's surfaced in the nightly report.

The human interface is Claude chat: Dan talks to Claude on his main machine to query the signals DB, read reports, review and approve evolver proposals, and adjust the watchlist. The bot itself runs continuously on a dedicated machine.

**Why this shape:** the entire point of the system is to improve over time. Without a disciplined backtest + walk-forward loop, the evolver is proposing changes based on 4 weeks of noisy live data with no way to tell overfit from signal. With the backtest engine as the foundation, every proposal has to prove itself on diverse historical regimes before it can touch the live account. Paper trading then becomes the final out-of-sample check, not the primary learning signal.

---

## 2. Component map

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    Bull-Bot (dedicated machine, 24/7)                     │
│                                                                            │
│  ┌───────────────────────┐                                                 │
│  │  Execution Engine     │  ← single engine, time-cursor driven            │
│  │  engine.step(cursor)  │                                                 │
│  └──────────┬────────────┘                                                 │
│             │                                                              │
│   ┌─────────┴──────────┐                                                   │
│   │                    │                                                   │
│   ▼                    ▼                                                   │
│ ┌──────────┐      ┌──────────────┐                                         │
│ │  Live    │      │  Backtest    │                                         │
│ │ scheduler│      │   runner     │                                         │
│ │  (cron)  │      │ (on-demand)  │                                         │
│ └──────────┘      └──────────────┘                                         │
│                                                                            │
│  ┌─────────────────────┐  ┌─────────────────────┐                          │
│  │ Data Fetchers       │  │ Historical Backfill │                          │
│  │ (Polygon only,      │  │ (per-timeframe      │                          │
│  │  live delta)        │  │  deep history)      │                          │
│  └─────────┬───────────┘  └──────────┬──────────┘                          │
│            │                         │                                     │
│            ▼                         ▼                                     │
│           ┌─────────────────────────────────┐                              │
│           │         Data Cache              │                              │
│           │  SQLite (hot) + Parquet (cold)  │                              │
│           │  bars • options • IV            │                              │
│           │  regime_labels                  │                              │
│           └───────────┬─────────────────────┘                              │
│                       │                                                    │
│                       ▼                                                    │
│           ┌─────────────────────────┐                                      │
│           │ Indicators + Features   │                                      │
│           │ analysis/features.py    │                                      │
│           └───────────┬─────────────┘                                      │
│                       │                                                    │
│                       ▼                                                    │
│      ┌────────────────────────────────────────────┐                        │
│      │         Research Agents (5)                │                        │
│      │   15m • 1h • 4h • 1d • 1w                  │                        │
│      │   Haiku × 4   +  Sonnet × 1 (weekly)       │                        │
│      └───────────────┬────────────────────────────┘                        │
│                      │                                                     │
│                      ▼                                                     │
│           ┌─────────────────────┐                                          │
│           │  Signals DB         │  (origin: live | backtest | replay)      │
│           │  + latest_signals   │                                          │
│           └──────────┬──────────┘                                          │
│                      │                                                     │
│                      ▼                                                     │
│           ┌─────────────────────┐                                          │
│           │  Decision Agent     │  (Sonnet)                                │
│           └──────────┬──────────┘                                          │
│                      │                                                     │
│                      ▼                                                     │
│           ┌─────────────────────┐                                          │
│           │  Paper Ledger       │  (live + backtest partitioned by         │
│           │                     │   run_id; live is run_id = "live")       │
│           └──────────┬──────────┘                                          │
│                      │                                                     │
│                      ▼                                                     │
│           ┌─────────────────────┐    ┌──────────────────────┐              │
│           │ Performance Agent   │───▶│ Attribution DB       │              │
│           │ (daily + weekly)    │    │ (per-regime metrics) │              │
│           └──────────┬──────────┘    └──────────┬───────────┘              │
│                      │                          │                          │
│                      │                          ▼                          │
│                      │              ┌──────────────────────┐               │
│                      │              │ Faithfulness Checker │               │
│                      │              │ (live vs backtest)   │               │
│                      │              └──────────────────────┘               │
│                      │                                                      │
│                      ▼                                                      │
│           ┌─────────────────────────┐                                       │
│           │  Strategy Evolver       │  (Sonnet, weekly)                     │
│           └──────────┬──────────────┘                                       │
│                      │                                                      │
│                      ▼                                                      │
│           ┌─────────────────────────┐                                       │
│           │  Backtest Validator     │  walk-forward on proposal             │
│           │  train/holdout splits   │  auto-rejects overfits                │
│           └──────────┬──────────────┘                                       │
│                      │                                                      │
│                      ▼                                                      │
│           ┌─────────────────────────┐                                       │
│           │ strategy_versions/      │                                       │
│           │ (pending + active)      │                                       │
│           └─────────────────────────┘                                       │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────┐         │
│  │ Reports folder (markdown) — synced to Dan's main machine       │         │
│  │ research / decisions / performance / evolver / backtest /      │         │
│  │ reconcile                                                       │         │
│  └────────────────────────────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
         ┌────────────────────────────────────────┐
         │ Dan's main machine — Claude chat UI    │
         │ reads reports, queries DBs, approves   │
         │ evolver proposals, edits watchlist     │
         └────────────────────────────────────────┘
```

---

## 3. Execution Engine (the unified core)

The execution engine is the single entry point for both live trading and backtest replay. Its contract:

```python
def step(cursor: Cursor, mode: ExecMode, run_id: str) -> StepResult:
    """
    Run one full cycle of the pipeline at the given time cursor.

    Cursor can be:
      - Cursor.now()                → live trading
      - Cursor.at(datetime)         → historical replay
      - Cursor.bar(ticker, tf, ts)  → single-bar replay for tests

    Mode determines how data is fetched and how LLM calls are made:
      - ExecMode.LIVE           → fetch from APIs, call LLMs fresh
      - ExecMode.BACKTEST_FULL  → read historical from cache, call LLMs fresh
      - ExecMode.BACKTEST_CHEAP → read historical from cache, replay cached LLM outputs

    run_id partitions data:
      - "live" for live trading
      - "bt_<uuid>" for backtest runs
    """
```

**Every other component treats `engine.step()` as its only entry point.** There is no "live mode script" and "backtest mode script" doing slightly different things. Both paths go through the same code. This is non-negotiable — otherwise backtest metrics and live metrics drift and the evolver's learning signal is corrupted.

**What `step()` does internally:**

1. Trading calendar check for `cursor.datetime` (no-op if not a trading day)
2. For each ticker in scope:
   - Load bars ≤ `cursor.datetime` from cache
   - Compute features
   - For each timeframe whose schedule would have fired at `cursor.datetime`:
     - Load or generate the research signal (fresh call in LIVE/BACKTEST_FULL, cached lookup in BACKTEST_CHEAP)
     - Write to `signals` with `origin=mode` and `run_id`
3. Compute confluence from latest signals as of cursor
4. Run the decision agent on the snapshot (or in BACKTEST_CHEAP, replay cached decision)
5. Apply portfolio rules, open/close positions in the ledger partitioned by `run_id`
6. Mark to market using the bar at cursor
7. Write decision audit row
8. Return a `StepResult` with what happened

**Scripts on top of the engine:**

- `scripts/live/run_live.py` — calls `engine.step(Cursor.now(), ExecMode.LIVE, run_id="live")` on the cron-driven schedule.
- `scripts/backtest/run_backtest.py --start 2021-01-01 --end 2026-04-01 --mode full|cheap` — iterates a cursor over the date range and calls `engine.step()` at every scheduled invocation point.
- `scripts/backtest/run_walk_forward.py --strategy pending/v012.json` — runs the walk-forward framework against a proposed config.

**The live scheduler is a thin wrapper.** Everything that looks like a "live script" in the work plan is really `run_live.py --task research_15m` or similar — all routes end up calling the engine.

---

## 4. Data layer

### 4.1 SQLite schema (single DB file: `cache/bull_bot.db`)

All tables use WAL mode for concurrent reads and coarse-grained writes. PKs and FKs enforced. `run_id` is a partitioning column on every trading-related table so live and backtest data coexist without interference.

**Market data tables (shared across all runs)**

| Table           | Purpose                                    | Key columns                                              |
|-----------------|--------------------------------------------|----------------------------------------------------------|
| `bars`          | OHLCV bars, all timeframes, all tickers    | (ticker, timeframe, bar_ts), O, H, L, C, V, VWAP, trans  |
| `options_chain` | Options contract metadata snapshots        | (underlying, expiry, strike, right, snapshot_ts)         |
| `options_quote` | Per-contract bid/ask/greeks snapshots      | (contract_id, snapshot_ts), bid, ask, iv, greeks         |
| `iv_rank`       | Daily IV rank per ticker                   | (ticker, date), iv_rank, iv_percentile                   |
| `earnings`      | Upcoming + historical earnings calendar    | (ticker, announce_date, session)                         |
| `halts`         | Trading halts                              | (ticker, halt_start, halt_end, reason)                   |
| `regime_labels` | Market regime classification per date/tf  | (date, timeframe, regime_id, labels_json)                |

**Analysis tables (shared across runs)**

| Table             | Purpose                              | Key columns                                              |
|-------------------|--------------------------------------|----------------------------------------------------------|
| `tech_features`   | Computed indicators per ticker/tf    | (ticker, timeframe, bar_ts), indicators...               |

**Signal table (partitioned by run_id)**

| Table             | Purpose                                    | Key columns                                              |
|-------------------|--------------------------------------------|----------------------------------------------------------|
| `signals`         | Research agent outputs (append-only)       | (signal_id, run_id, origin, ticker, timeframe, ts, direction, conviction, entry, stop, target_1, target_2, rr, rationale_md, schema_version) |
| `latest_signals`  | Latest per (run_id, ticker, timeframe)     | PK (run_id, ticker, timeframe), signal_id, ttl_expires_at|
| `confluence`      | Aggregated confluence per ticker per run   | (run_id, ticker, ts, confluence_score, ...)              |

`origin` values: `live`, `backtest_full`, `backtest_cheap`, `bootstrap_replay`.

**Paper trading tables (partitioned by run_id)**

| Table             | Purpose                                          | Key columns                                                   |
|-------------------|--------------------------------------------------|---------------------------------------------------------------|
| `positions_open`  | Open paper positions                             | (position_id, run_id, ticker, strategy, direction, legs_json, size, entry_ts, entry_price, stop, target, strategy_version, expiry_date, margin_required, opening_signals_json) |
| `positions_closed`| Closed paper positions                           | same + (exit_ts, exit_price, exit_reason, pnl_gross, pnl_net, hold_days, regime_at_open, regime_at_close) |
| `trade_log`       | Append-only audit of every ledger event          | (event_id, run_id, ts, event_type, position_id, details_json) |
| `daily_marks`     | Mark-to-market per position                      | (run_id, position_id, mark_date, mark_price, unrealized_pnl)  |
| `daily_equity`    | Daily equity snapshot                            | (run_id, date, cash, unrealized_pnl, realized_pnl, total_equity, drawdown_pct, gross_exposure, margin_used, was_backfilled) |
| `circuit_breakers`| History of breaker events                        | (run_id, trigger_ts, type, value_at_trigger, cleared_ts)       |

**Agent runtime tables**

| Table             | Purpose                                    | Key columns                                          |
|-------------------|--------------------------------------------|------------------------------------------------------|
| `agent_runs`      | One row per agent call                     | (run_id, agent_name, model, ts, ticker, timeframe, input_tokens, output_tokens, latency_ms, cost_usd, success, error) |
| `decision_audits` | Per-decision context and rationale         | (audit_id, run_id, decision_ts, strategy_version, snapshot_json, candidates_json, opened_ids, rationale_md) |

**Backtest runtime tables**

| Table                    | Purpose                                              | Key columns                                          |
|--------------------------|------------------------------------------------------|------------------------------------------------------|
| `backtest_runs`          | One row per backtest invocation                      | (run_id, kind, strategy_version, start_date, end_date, timeframe_scope, mode, created_at, total_cost_usd, summary_json) |
| `walk_forward_windows`   | Per-window results in a walk-forward backtest        | (run_id, window_idx, train_start, train_end, holdout_start, holdout_end, train_metrics_json, holdout_metrics_json) |
| `faithfulness_checks`    | Live-vs-backtest divergence measurements             | (check_date, live_run_id, backtest_run_id, live_pnl, bt_pnl, divergence_pct, status, notes) |

### 4.2 Parquet for cold storage

Bars older than 90 days in the live run are moved to parquet files in `cache/parquet/` partitioned by ticker + timeframe + year + month. Backtest runs read from both SQLite and parquet transparently via a unified reader. SQLite stays hot and small; parquet holds the multi-year history cheaply.

### 4.3 Historical data depth (per timeframe)

Backtest depth scales with timeframe: longer timeframes need more years of history to cover diverse regimes; shorter timeframes generate so many bars per year that a shorter window already has ample statistical mass, and older intraday data is less representative due to microstructure drift.

| Timeframe | Depth      | Approx bars/ticker | Rationale                                                              |
|-----------|------------|--------------------|------------------------------------------------------------------------|
| 1w        | **10 years** | ~520             | Spans 2016 bull, 2020 covid crash, 2022 bear, 2023–24 recovery, 2025 mixed |
| 1d        | **5 years**  | ~1,260           | Covers 2021 reopening, 2022 bear, 2023–24 recovery, 2025 chop            |
| 4h        | **3 years**  | ~4,500           | Intraday structure evolves faster; 3yr is ample sample size              |
| 1h        | **2 years**  | ~3,500           | Same reasoning                                                           |
| 15m       | **1 year**   | ~6,500           | Microstructure drifts; older 15m data is less representative             |

These values live in `config.py` as `BACKTEST_HISTORY_BARS`. The T0.1 Polygon verification task confirms each depth is actually fetchable on the current subscription; any truncation triggers a fallback decision (shorter window or data augmentation).

### 4.4 Regime classification

Regimes are labeled on every trading day at two scopes: **macro regime** (weekly/daily scope) and **intraday regime** (4h/1h/15m scope). Classification uses simple deterministic rules, not a model:

**Macro regime** (applied to 1d and 1w analysis):
- `VIX_BUCKET` ∈ {low, normal, elevated, high} from VIX absolute level
- `TREND_BIAS` ∈ {bull, bear, chop} from SPY 20d vs 50d SMA + slope
- `VOL_REGIME` ∈ {low_vol, normal, high_vol} from SPY 20d realized vol percentile
- Combined label: e.g., `(low_vol, bull, normal_vix)`

**Intraday regime** (applied to 4h, 1h, 15m analysis):
- `SESSION_PHASE` ∈ {premarket, opening_range, midday, power_hour, afterhours}
- `RELATIVE_VOLUME` ∈ {thin, normal, heavy}
- `INDEX_TREND_5D` ∈ {up, flat, down} from SPY 5-day slope
- Combined label: e.g., `(midday, normal, up)`

Regime labels are stored in `regime_labels` at build time (during backfill) so they're available as attribution keys everywhere downstream. Every closed trade carries `regime_at_open` and `regime_at_close` for per-regime P&L breakdown.

---

## 5. Agent layer

### 5.1 Shared agent infrastructure

All agents are Python functions that:
1. Build a prompt from a markdown template + structured inputs
2. Call the Anthropic SDK with the selected model
3. Parse the response as JSON against a Pydantic schema
4. Retry on failure, log the call to `agent_runs`, return the validated result

Agents **do not** make their own API calls to Polygon directly. They receive pre-fetched data from the data layer. This keeps agents fast, cheap, and deterministic to test — and critical for backtest replay consistency.

Research agent outputs are cached by `(strategy_version, ticker, timeframe, bar_ts)` so that `BACKTEST_CHEAP` mode can look them up instead of re-calling the LLM. Decision agent outputs are similarly cached by `(strategy_version, decision_ts)`.

### 5.2 Research agents

Five agents, one per timeframe. Each takes as input:

- Ticker
- Last N bars for their timeframe (from `bars`)
- Computed indicators for their timeframe (from `tech_features`)
- Options flow snapshot for the ticker
- IV rank
- Upcoming earnings within 5 days
- Current regime label (macro or intraday, depending on timeframe)
- Current price snapshot

Output is a structured signal matching the schema in `schemas/signals.py` (direction, conviction 0..10, entry, stop, target_1/2, risk_reward, key_levels, rationale, indicators_used).

**Prompt files:** `agents/research_base.md` + one delta per timeframe (`research_15m.md`, `research_1h.md`, `research_4h.md`, `research_1d.md`, `research_1w.md`).

**Model selection:**
- 15m, 1h, 4h, 1d: Haiku
- 1w: Sonnet

### 5.3 Decision agent

Runs 5× per day in live mode: 9:45a, 12:30p, 2:45p, 4:15p, 8:15p ET. In backtest mode, triggered at the same cursor times over the historical range.

Input:
- Latest per-timeframe signals (from `latest_signals` filtered by run_id)
- Current portfolio state (from `positions_open` filtered by run_id)
- Active `strategy_config.json`
- Upcoming earnings for held tickers
- Active circuit breakers
- Current regime label

Logic (summary; full spec in Appendix A.1):
1. Compute confluence score per ticker
2. For each candidate with confluence above threshold or a standalone high-conviction signal:
   - Check portfolio rules
   - Check earnings blackout
   - Check circuit breakers
   - Select strategy family based on IV rank + ticker eligibility
   - Compute position size
3. Emit open/close orders to the ledger
4. Write decision audit row and markdown report

**Model:** Sonnet.

### 5.4 Performance analyzer

Runs nightly at 8:30 PM ET (live only) plus a weekly synthesis Friday 9:00 PM. Also invoked on-demand by the backtest runner at the end of every window.

**Nightly:**
1. Fetch latest close prices for all open positions (from `bars` cache, or real-time snapshot in live mode)
2. Mark each position to market
3. Compute `daily_equity` row
4. Auto-close positions hitting stops / targets / expiries
5. Check circuit breakers
6. Run the faithfulness check (§7.3) if this is a live run
7. Write daily report with narrative + cost summary

**Weekly:**
1. Pull all trades closed in the past 7 days
2. Attribute by timeframe, strategy family, conviction bucket, sector, ticker, **regime**
3. Compute per-segment metrics
4. Write weekly attribution report
5. Trigger the strategy evolver if it's Sunday

### 5.5 Strategy evolver (now with backtest gate)

Runs Sunday 7:00 PM ET in live operation. One Sonnet call produces a proposal.

Input:
- Past 4 weeks of closed trades + attribution
- Current `strategy_config.json`
- Last 4 evolver proposals and their realized outcomes (own track record)
- Current portfolio state
- Current macro regime

Output: a proposal file `strategy_versions/pending/vNNN_proposal.json` + `vNNN_rationale.md` explaining the diff and the evidence.

**The proposal does NOT go to Dan directly.** It first passes through the backtest validator (§7). Only proposals that survive walk-forward validation are presented for approval.

---

## 6. Backtest Engine

This is the heart of the system. Everything else is plumbing for this.

### 6.1 What the backtest engine does

Given a strategy config, a date range, and a mode, the backtest engine:

1. Creates a new `backtest_runs` row with a unique `run_id`.
2. Walks a cursor from `start_date` to `end_date`, stepping at every scheduled invocation point (research passes, decision passes, nightly close).
3. At each cursor, calls `engine.step(cursor, mode, run_id)`.
4. Accumulates ledger state partitioned by `run_id`.
5. At the end of the run, computes full metrics (overall, per-timeframe, per-strategy-family, per-regime, per-ticker).
6. Writes a backtest report to `reports/backtest/<run_id>/` with the summary, equity curve, trade list, and attribution tables.
7. Returns a `BacktestResult` with metrics that downstream code (walk-forward, evolver) can compare.

### 6.2 Modes

- **`FULL`** — fresh LLM calls for every research signal and decision. Used for the initial bootstrap (when no cached outputs exist) and for validating prompt changes. Expensive: roughly $30–100 for a multi-year run on 27 tickers, depending on timeframe coverage.

- **`CHEAP`** — uses cached LLM outputs from previous FULL runs or from live trading. Only recomputes numeric rules (sizing, confluence, portfolio constraints, exits). Used for validating numeric evolver proposals. Near-zero cost, typically runs in seconds to minutes.

- **`HYBRID`** — fresh LLM calls only at cursor points where no cached output exists; uses cached outputs elsewhere. Used when backtesting a strategy that is mostly numeric changes but has one or two prompt tweaks.

### 6.3 Metrics

Every backtest run produces a metrics object with:

- **Headline:** total return, sharpe, sortino, max drawdown, max drawdown duration, profit factor, total trades
- **Per-timeframe:** metrics broken down by `opening_signals` primary timeframe
- **Per-strategy-family:** metrics per {credit_spread, long_option, wheel, inverse_etf, long_equity}
- **Per-regime:** same metrics, bucketed by regime at open
- **Per-ticker:** small table showing winners and losers
- **Trade distribution:** R-multiple histogram, hold-time distribution, win-rate by conviction bucket
- **Cost:** LLM $ used (if FULL mode), total bars read, elapsed time

The regime breakdown is the most important one for the evolver — it's how we detect "this change looks great overall but only because it hit one lucky regime."

### 6.4 Fill model consistency (critical)

The backtest engine uses the **same** `paper_trading/fill_model.py` as live. No divergent "backtest-only" fill logic. The fill model takes a bar (or chain snapshot) and an order and returns a fill price. In live mode the bar is current; in backtest mode the bar is historical. Same function, same outputs.

For options in backtest, the fill model reads from `options_quote` at the cursor timestamp. If the cursor is at 10:30 and the nearest option quote snapshot is from 10:25, the fill uses the 10:25 mid with staleness-aware slippage. Same rule in live mode.

### 6.5 Historical LLM replay cost budget (tiered)

The bootstrap is the one expensive one-time event. Full replay across all 5 timeframes and all 27 tickers would run ~$1,200–1,800. Instead, Bull-Bot uses a **tiered bootstrap** that front-loads cost on the timeframes that matter most for the evolver (daily and weekly) and defers intraday replay in favor of letting the live cache fill organically.

Full cost table if all tiers were run:

| Agent          | Model  | Calls for full bootstrap            | Est. cost |
|----------------|--------|-------------------------------------|-----------|
| Research 1w    | Sonnet | 520 × 27 = 14k                      | ~$280     |
| Research 1d    | Haiku  | 1,260 × 27 = 34k                    | ~$100     |
| Research 4h    | Haiku  | 4,500 × 27 = 121k                   | ~$350     |
| Research 1h    | Haiku  | 3,500 × 27 = 94k                    | ~$270     |
| Research 15m   | Haiku  | 6,500 × 27 = 175k                   | ~$500     |
| Decision agent | Sonnet | 5/day × ~1,260 trading days = 6.3k  | ~$190     |
| **Total (all)**|        |                                     | **~$1,700** |

**Tier 1 — Daily (approved, ~$100):** Research 1d on all 27 tickers using Haiku. ~34k calls, ~30 minutes of wall time. Unlocks daily-timeframe walk-forward validation from day 1.

**Tier 2 — Weekly (approved, ~$280):** Research 1w on all 27 tickers using Sonnet. ~14k calls, ~45 minutes of wall time. Unlocks weekly macro evolver validation from day 1.

**Tier 3 — Intraday (deferred):** Research 4h, 1h, 15m across all or priority tickers. Not run initially. The cheap-mode cache for intraday timeframes fills organically from live running — roughly 3 months of live operation produces enough 15m cache depth to begin intraday walk-forward validation. If the intraday evolver becomes important before then, Tier 3 can be run on priority tickers only (top 10) for ~$135, or in full for ~$1,120.

**Approved tier 1+2 total: ~$380 one-time, plus a small decision-agent sample (~$40) to anchor the daily/weekly decision loop.**

This is a one-time cost. Subsequent walk-forward validations of numeric proposals run in CHEAP mode at near-zero cost. Prompt changes trigger a targeted FULL replay on only the windows needed, typically ~$20–50 per proposal.

**Cost controls:**
- Each tier requires explicit user confirmation with a dry-run cost estimate first
- Cost tracked in real time in `backtest_runs.total_cost_usd`
- Hard caps configurable in `config.py`, tier-specific (see Appendix A.18)
- Checkpoint-resume: bootstrap can be interrupted and resumed without re-spending
- 90% soft cap triggers a pause-for-review before the final 10% of spend
- Daily cost rollup in the nightly report

---

## 7. Walk-Forward Learning Loop

### 7.1 The walk-forward protocol

Walk-forward prevents the evolver from overfitting to recent data. The protocol splits history into repeating (train, holdout) windows and only accepts strategy changes that improve both.

**Per-timeframe windows:**

| Timeframe scope | Train window | Holdout window | Retrain cadence | Rationale |
|-----------------|--------------|-----------------|-----------------|-----------|
| Weekly (1w)     | 3 years      | 1 year          | Quarterly       | Slow-moving; need long windows for regime coverage |
| Daily (1d)      | 26 weeks     | 8 weeks         | Monthly         | Standard mid-frequency cadence                     |
| Intraday (4h, 1h, 15m) | 12 weeks | 4 weeks      | Weekly          | Fast adaptation; shorter windows are representative |

**The walk-forward run:**

1. Take a proposed `strategy_config.json` from the evolver.
2. Slide through the history in (train_window + holdout_window) chunks, advancing by the retrain cadence.
3. For each chunk:
   - Evaluate the proposal on the train window → train metrics.
   - Evaluate the proposal on the holdout window → holdout metrics.
   - Also evaluate the current (baseline) config on the same holdout → baseline metrics.
4. Report aggregate train/holdout/baseline metrics per regime.

### 7.2 The approval gate

A proposal is **auto-rejected** if any of these hold on the walk-forward output:

- **Holdout underperforms baseline.** Proposal's holdout Sharpe < baseline holdout Sharpe − 0.1 tolerance, averaged across windows.
- **Single-regime overfit.** Proposal beats baseline in only one regime but loses in two or more others.
- **Worse drawdown.** Proposal's worst holdout drawdown is > 1.25× baseline's.
- **Insufficient activity.** Proposal trades < 50% of baseline trade count in holdout (may mean rules got too restrictive to be useful).
- **Cost blowout** (for prompt changes only). Proposal's projected LLM cost > 2× baseline.

Proposals that **pass** the gate are presented to Dan with the full walk-forward report, including baseline comparison, per-regime breakdown, and the trades that differed between baseline and proposal. Dan still has final approval — the gate is a filter, not a rubber stamp.

**Design note:** the gate is soft — when new proposals arrive, they surface the result AND the auto-reject reason if any. Dan can override and approve anyway if he has reason to (e.g., "this is a speculative experiment for two weeks, I want to see it run live"). Overrides are logged in the proposal audit.

### 7.3 Faithfulness check (live ↔ backtest consistency)

This is how we know the backtest engine is actually faithful to live behavior. Starting three weeks into live operation, the nightly performance pass runs:

1. Identify the live window (last 14 trading days of live activity).
2. Run a backtest over the same window using the same strategy config.
3. Compare metrics: total P&L, per-trade fill divergence, trade count.
4. Compute `divergence_pct = abs(live_pnl - bt_pnl) / max(|live_pnl|, |bt_pnl|)`.
5. Write a `faithfulness_checks` row.
6. Surface in the nightly report:
   - `< 2%` → green
   - `2–5%` → yellow (log and watch)
   - `> 5%` → red (alert, include diagnostic info)

A red check triggers an investigation before any new evolver proposal is accepted. Common causes: stale options quotes at live fill time, off-by-one cursor in backtest, silent bar-backfill during downtime.

This check is the immune system of the learning loop. If it's broken, every evolver proposal is operating on bad feedback.

### 7.4 Bootstrapping without live data

The initial problem: when we first turn the system on, there's no live history to compare against. We can't run the faithfulness check, and we can't do CHEAP backtests because there are no cached LLM outputs.

**Bootstrap sequence:**

1. **T-week backfill:** populate `bars`, `options_chain`/`options_quote` (to the extent Polygon history allows), `iv_rank`, `earnings`, `regime_labels` for the full per-timeframe depths.
2. **Initial FULL backtest:** run `engine.step()` in FULL mode across the history, generating historical signals and decisions. This populates `signals` with `origin=bootstrap_replay` and creates the first backtest baseline for the current config.
3. **Baseline metrics frozen:** the baseline metrics from this run become the benchmark for every future evolver proposal.
4. **Live mode starts:** cron enables live runs. Faithfulness checks start after 3 weeks of live data.
5. **First evolver proposal:** earliest at ~week 5 (after 4 weeks of closed live trades to analyze). The proposal is validated via walk-forward against the cached historical signals before being presented to Dan.

This makes the bootstrap the most expensive single operation in the project, but it unlocks every downstream learning loop. Without it, the evolver is flying blind for weeks.

---

## 8. Scheduling (live only)

The live scheduler is a cron table or systemd timer set. Each entry invokes `scripts/live/run_live.py --task <task_name>`, which resolves to `engine.step(Cursor.now(), ExecMode.LIVE, run_id="live")` with the appropriate task filter.

```
# Crontab (illustrative, ET-local)
# Research 15m every 30 min, 4a–8p, weekdays
*/30 4-19 * * 1-5  cd /path && venv/bin/python scripts/live/run_live.py --task research_15m
# Research 1h every hour
0 5-20 * * 1-5     cd /path && venv/bin/python scripts/live/run_live.py --task research_1h
# Research 4h four times a day
0 8,12,16,20 * * 1-5  cd /path && venv/bin/python scripts/live/run_live.py --task research_4h
# Daily after extended close
15 20 * * 1-5      cd /path && venv/bin/python scripts/live/run_live.py --task research_1d
# Weekly Friday evening
20 20 * * 5        cd /path && venv/bin/python scripts/live/run_live.py --task research_1w
# Decision passes
45 9,12,14 * * 1-5  cd /path && venv/bin/python scripts/live/run_live.py --task decision
15 16,20 * * 1-5    cd /path && venv/bin/python scripts/live/run_live.py --task decision
# Performance nightly (includes faithfulness check)
30 20 * * 1-5      cd /path && venv/bin/python scripts/live/run_live.py --task nightly
# Weekly attribution
0 21 * * 5         cd /path && venv/bin/python scripts/live/run_live.py --task weekly
# Strategy evolver (proposes + auto-runs walk-forward backtest)
0 19 * * 0         cd /path && venv/bin/python scripts/live/run_live.py --task evolver
```

Every script honors the trading-calendar check. Early-close days use the shortened window from `pandas_market_calendars`.

### 8.1 Backtest is on-demand, not scheduled

Backtests are invoked manually (`scripts/backtest/run_backtest.py`) or automatically by the evolver script (walk-forward validation of its own proposals). No cron entry runs a backtest on a schedule — that would be wasted compute.

### 8.2 Reconciliation on startup

`scripts/reconcile.py` runs on bot startup and after downtime. Semantics in Appendix A.10 (unchanged from v1).

### 8.3 Error handling

Unchanged from v1. Every script wraps its main work in try/except, logs failures to `logs/errors/YYYY-MM-DD.jsonl`, exits non-zero on failure, fires a notification webhook.

---

## 9. Configuration

### 9.1 `config.py` — static, code-level

Ticker universe, timeframes, default risk rules, paths, sector map, `BACKTEST_HISTORY_BARS` (per-timeframe depths), walk-forward window sizes, regime-labeling rules, cost caps.

### 9.2 `strategy_config.json` — dynamic, evolver-editable

Versioned. Contains risk tolerance, confluence thresholds, timeframe weights, sizing bases, strategy preferences, ticker eligibility, exit rules. The v1 default is in Appendix A.12.

### 9.3 `.env` — secrets

`POLYGON_API_KEY`, `UNUSUAL_WHALES_API_KEY`, `ANTHROPIC_API_KEY`, `NOTIFY_WEBHOOK_URL`, `TIMEZONE`.

### 9.4 `runtime_state.json`

Runtime toggles for trading_paused, force_close_all, etc. (Appendix A.11.)

---

## 10. Deployment

### 10.1 Target machine

Linux or macOS, Python 3.11+, 8GB RAM recommended (up from 4GB in v1 — the backtest engine loads years of bars), persistent network, NTP-synced clock. Runs as Dan's user, no special privileges.

### 10.2 Install steps

1. `git clone` or `rsync` the project onto the machine.
2. `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
3. Copy `.env.template` to `.env`, fill in keys.
4. Run `python scripts/seed_db.py` to initialize the SQLite schema and seed capital.
5. Run `python scripts/backfill_history.py` to populate the full backtest history (takes hours; prints progress). Cost estimate printed up front.
6. Run `python scripts/backtest/bootstrap.py` to do the initial FULL backtest. **Prompts for confirmation with cost estimate before starting.** Takes 1–3 hours, costs $500–700.
7. Run `python scripts/smoke_test.py` to verify live pipeline works.
8. Install the cron table from `deploy/crontab.et` (or systemd units).
9. Optional: set up folder sync to Dan's main machine.

### 10.3 Dockerfile (optional, v2)

Same as v1. Supervisor-based internal scheduler instead of OS cron.

---

## 11. Interaction model (Dan ↔ Bull-Bot)

Dan never runs scripts directly. He talks to Claude on his main machine. Claude reads the synced folder and can answer:

- Research findings (`reports/research/...`)
- Current paper trades (`positions_open` via read-only helper or latest performance report)
- Today's decisions (`reports/decisions/...`)
- Weekly attribution (`reports/performance/weekly/...`)
- **Evolver proposals + their walk-forward report** (`reports/evolver/pending/...`)
- **Backtest results on demand** ("run a backtest of the current config against Q1 2024")
- Approve evolver proposals (Claude moves pending → active after Dan confirms)
- Pause trading via `runtime_state.json`
- Adjust the watchlist via `config.py`

Claude on the main machine has read-only access to the synced reports and can invoke helper scripts over SSH for DB queries.

---

## 12. Non-functional requirements

- **Engine consistency:** live and backtest share the exact same execution engine. Any divergence is a bug and is caught by the faithfulness check.
- **Idempotency:** every scheduled script is re-runnable. Deduplication on `(run_id, ticker, timeframe, bar_ts)`. Decision passes use a lock file.
- **Observability:** every agent call and every ledger mutation is logged with enough context to reconstruct what happened.
- **Cost visibility:** LLM spend estimated and reported daily; backtest runs report cost up front and track it live against the cap.
- **Soft failure modes:** a failed research call produces a `neutral` signal, not a crash. A failed decision pass skips that cycle. A failed nightly pass sends an alert.
- **Security:** no secrets in code or git. All keys in `.env`. No network-exposed endpoints.
- **Testability:** every pure function is unit-testable. The execution engine can be driven deterministically with a fake clock.
- **Auditability:** every strategy version is snapshotted at proposal time and approval time. Every backtest run is stored with its inputs so results are reproducible.

---

## Appendix A — Review-driven clarifications (continued)

The appendices from v1 remain authoritative. The new additions for the backtest reframing are below.

### A.1 Confluence math
*(Unchanged from v1.)*

### A.2 Signal staleness rules
*(Unchanged from v1.)*

### A.3 Leg dataclass
*(Unchanged from v1, but `run_id` is now a top-level position column.)*

### A.4 Expiry handling
*(Unchanged from v1.)*

### A.5 Margin accounting
*(Unchanged from v1.)*

### A.6 `latest_signals` table
*(Updated: PK is now `(run_id, ticker, timeframe)`.)*

### A.7 VWAP anchoring
*(Unchanged from v1.)*

### A.8 Crypto ETF special handling
*(Unchanged from v1.)*

### A.9 Trading calendar
*(Unchanged from v1.)*

### A.10 Reconcile replay semantics
*(Unchanged from v1. Adds: reconcile only operates on `run_id="live"`.)*

### A.11 `runtime_state.json`
*(Unchanged from v1.)*

### A.12 v1 `ticker_eligibility`
*(Unchanged from v1.)*

### A.13 Backtest cursor semantics (new)

A backtest cursor advances through history at exactly the same points a live cron would fire. For example, on 2024-06-14, the 15m research cursor would fire at 04:00, 04:30, 05:00, ... through 19:30 ET. At each cursor point:

- Data available = everything with `ts <= cursor.datetime`. No look-ahead.
- `pandas_market_calendars` is consulted for the date; if not a trading day, cursor skips to next trading day.
- Early-close days use the shortened window.
- Cursor advances are logged so a backtest run is fully reproducible.

### A.14 Regime classifier rules (new)

Exact rules as implemented. All inputs come from cached `bars` and `iv_rank`.

**Macro regime** (computed daily, applied to 1d + 1w scope):

```
vix_level = bars[VIX].close            # VIX close, available ~15m after close
trend_bias:
    spy_20_sma = SMA(bars[SPY].close, 20)
    spy_50_sma = SMA(bars[SPY].close, 50)
    slope = linear_regression_slope(bars[SPY].close[-20:])
    if spy_20_sma > spy_50_sma and slope > 0:   "bull"
    elif spy_20_sma < spy_50_sma and slope < 0: "bear"
    else:                                        "chop"

vix_bucket:
    if vix_level < 15: "low"
    elif vix_level < 20: "normal"
    elif vix_level < 28: "elevated"
    else: "high"

vol_regime:
    rv_20 = realized_vol(bars[SPY].returns[-20:])
    rv_pct = percentile_rank(rv_20, history_1_year)
    if rv_pct < 30: "low_vol"
    elif rv_pct < 70: "normal"
    else: "high_vol"
```

**Intraday regime** (computed per 15m bar, applied to 15m + 1h + 4h scope):

```
session_phase:
    if bar_ts time < 09:30: "premarket"
    elif 09:30 <= time < 10:30: "opening_range"
    elif 10:30 <= time < 15:00: "midday"
    elif 15:00 <= time < 16:00: "power_hour"
    else: "afterhours"

relative_volume:
    avg_vol = mean(bars[ticker].volume, 20 bars same session phase)
    if bar.volume < 0.7 * avg_vol: "thin"
    elif bar.volume > 1.5 * avg_vol: "heavy"
    else: "normal"

index_trend_5d:
    spy_5d_slope = linear_regression_slope(bars[SPY, 1d].close[-5:])
    if slope > +0.3%: "up"
    elif slope < -0.3%: "down"
    else: "flat"
```

Results are written to `regime_labels` and indexed by date+timeframe for fast lookup.

### A.15 Walk-forward window specification (new)

Exact windows as implemented. Stored in `config.py`:

```python
WALK_FORWARD_WINDOWS = {
    "weekly": {
        "scopes": ["1w"],
        "train_weeks": 156,  # 3 years
        "holdout_weeks": 52,  # 1 year
        "step_weeks": 13,    # quarterly retrain
        "min_trades_holdout": 20,
    },
    "daily": {
        "scopes": ["1d"],
        "train_weeks": 26,
        "holdout_weeks": 8,
        "step_weeks": 4,     # monthly retrain
        "min_trades_holdout": 30,
    },
    "intraday": {
        "scopes": ["4h", "1h", "15m"],
        "train_weeks": 12,
        "holdout_weeks": 4,
        "step_weeks": 1,     # weekly retrain
        "min_trades_holdout": 40,
    },
}
```

The walk-forward runner iterates each scope independently and aggregates results per regime.

### A.16 Approval gate thresholds (new)

Stored in `config.py` and loaded by the walk-forward validator:

```python
APPROVAL_GATE = {
    "min_holdout_sharpe_vs_baseline": -0.1,   # proposal must be within 0.1 of baseline
    "min_holdout_return_vs_baseline": -0.02,  # within 2% return
    "max_holdout_dd_vs_baseline": 1.25,       # worst drawdown no more than 1.25× baseline
    "min_trade_count_ratio": 0.50,            # at least 50% of baseline trade count
    "max_single_regime_dependency": 0.65,     # no more than 65% of excess return from one regime
    "max_cost_ratio_for_prompt_changes": 2.0, # LLM cost no more than 2× baseline
    "soft_gate": True,                         # surface rejection but still allow Dan override
}
```

### A.17 Faithfulness check specification (new)

Runs in the nightly performance pass starting on day 15 of live operation (after ~3 weeks of warm-up).

```
faithfulness_check(live_run_id="live", window_days=14):
    end = today() - 1 trading day
    start = end - 14 trading days

    bt_run_id = "bt_faith_<yyyymmdd>"
    engine.backtest(
        start=start,
        end=end,
        config=current_strategy_config,
        mode=ExecMode.BACKTEST_CHEAP,  # reuses live LLM outputs
        run_id=bt_run_id,
    )

    live_pnl = sum(positions_closed.pnl_net WHERE run_id="live" AND exit_ts BETWEEN start AND end)
    bt_pnl   = sum(positions_closed.pnl_net WHERE run_id=bt_run_id AND exit_ts BETWEEN start AND end)

    divergence_pct = abs(live_pnl - bt_pnl) / max(abs(live_pnl), abs(bt_pnl), 100)

    status:
        < 2%  → "green"
        < 5%  → "yellow"
        else  → "red"

    write faithfulness_checks row
    if status == "red": write alert to notify_webhook
```

Red status blocks evolver proposals from being accepted until resolved.

### A.18 Cost cap enforcement (new)

The backtest engine reads tier-specific cost caps from `config.py`:

```python
BACKTEST_COST_LIMITS = {
    "default_run_cap_usd": 100,         # most ad-hoc backtests
    "bootstrap_tier1_cap_usd": 150,     # daily only (expected ~$100)
    "bootstrap_tier2_cap_usd": 400,     # weekly on Sonnet (expected ~$280)
    "bootstrap_tier3_cap_usd": 1500,    # intraday full (expected ~$1,120)
    "walk_forward_cap_usd": 300,        # evolver validation runs
    "prompt_change_replay_cap": 150,    # targeted FULL replay for prompt tweaks
    "soft_cap_pct": 0.90,               # pause for review at 90%
    "abort_on_exceed": True,
    "checkpoint_every_n_calls": 500,    # for resumable runs
}
```

Every LLM call estimates its cost, adds to a running total, and enforces two gates:
1. **Soft cap:** at `soft_cap_pct` of the per-tier cap, the run pauses and writes a checkpoint. Operator (or Claude on Dan's behalf) reviews and explicitly resumes.
2. **Hard cap:** at 100%, the run aborts and preserves partial results flagged as incomplete.

Checkpoint-resume: every N calls (configurable) the bootstrap script writes a checkpoint row to a small `bootstrap_checkpoints` table with the cursor position, cost-so-far, and cache keys touched. Re-running the script skips cursor points already present in `llm_output_cache`.
