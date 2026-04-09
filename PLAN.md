# Trading Research Agents — Implementation Plan

**Status:** Draft, pending Dan's approval before build continues.
**Author:** Claude, as pair programmer
**Last updated:** 2026-04-09

This is the plan we agree to before writing more code. Nothing in the `agents/`, `paper_trading/`, `analysis/`, or `scripts/` directories gets written until this is approved.

---

## 1. Objective

Build a simulated (paper) trading bot that runs 24/7 on Dan's dedicated machine. It researches a fixed universe of tickers across five timeframes, makes per-timeframe recommendations via specialized agents, synthesizes those into paper trades, tracks performance, and evolves its own rules over time. Run for 1–2+ months and measure whether the recommendations get better.

**Explicit non-goals:**
- Real trade execution
- Financial advice
- Beating the market in week one
- Perfect fill simulation — we'll be conservative and approximate

---

## 2. Architecture at a glance

```
                    ┌─────────────────────────────────┐
                    │  Polygon.io + Unusual Whales    │
                    └────────────┬────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────────────┐
                    │  Data cache layer (SQLite)      │
                    │  Bars, options chains, flow     │
                    └────────────┬────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
      ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
      │ 15m Research │   │ 1h Research  │   │ 4h Research  │   (per ticker)
      │    Agent     │   │    Agent     │   │    Agent     │
      └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
             │                  │                  │
             │          ┌──────────────┐   ┌──────────────┐
             │          │ Daily Agent  │   │ Weekly Agent │
             │          └──────┬───────┘   └──────┬───────┘
             │                  │                  │
             └──────────────────┼──────────────────┘
                                ▼
                    ┌─────────────────────────┐
                    │    Signals Database     │
                    │  (latest + full history) │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │     Decision Agent       │
                    │  Applies strategy cfg &  │
                    │     risk rules           │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   Paper Trade Ledger     │
                    │  SQLite + append-only log│
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │  Performance Analyzer    │
                    │  Daily P&L + attribution │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   Strategy Evolver       │
                    │  Proposes rule updates   │
                    └─────────────────────────┘
```

---

## 3. Agent topology

### 3.1 Research agents (5 total, one per timeframe)

Each agent runs independently and produces a structured recommendation for its timeframe only. A shared base prompt defines the output schema and framework. Each timeframe gets a delta file that tunes the personality and priorities.

| Agent         | Timeframe    | Priorities                                              |
|---------------|--------------|---------------------------------------------------------|
| Weekly agent  | 1W           | Macro regime, multi-week trend, major S/R               |
| Daily agent   | 1D           | Swing trend, catalysts, earnings proximity, daily pivot |
| 4H agent      | 4H           | Intraday trend, retest setups, range breaks             |
| 1H agent      | 1H           | Entry zones, momentum divergences, session highs/lows   |
| 15m agent     | 15m          | Execution timing, noise filtering, near-term momentum   |

**Output schema (same for all):**
```json
{
  "ticker": "TSLA",
  "timeframe": "1h",
  "timestamp_utc": "2026-04-09T18:00:00Z",
  "direction": "long" | "short" | "neutral",
  "conviction": 0..10,
  "entry": 175.40,
  "stop": 172.10,
  "target_1": 180.00,
  "target_2": 184.50,
  "risk_reward": 2.25,
  "key_levels": { "support": [172.10, 170.00], "resistance": [178.00, 184.50] },
  "rationale": "Price reclaimed EMA21 on strong volume; RSI out of oversold; gamma wall at 180.",
  "indicators_used": ["EMA21", "RSI14", "VWAP", "volume_profile"]
}
```

**LLM model tier:** Research agents run on Haiku (fast + cheap) for routine hourly passes. Promotion to Sonnet for the daily close pass and any ticker flagged as unusual.

### 3.2 Decision agent (1)

Reads the latest recommendation from each of the 5 research agents for every ticker. Does **not** require multi-timeframe confluence to trade. Instead, confluence scales position size:

- **Standalone signals are valid.** A strong 15m signal can open a small, short-term paper trade even if higher timeframes are neutral. Same for 1h.
- **Each timeframe has a base position size**, expressed as a fraction of the per-trade risk budget. Slower timeframes get bigger base allocations because they historically have better reliability:
  - Weekly base: 100% of max risk
  - Daily base:   80%
  - 4h base:      50%
  - 1h base:      30%
  - 15m base:     15%
- **Confluence is a multiplier.** When other timeframes agree in direction on a signal, the base size is scaled up toward the per-trade risk cap. Full 5-timeframe agreement with conviction ≥ 8 takes the trade to the max per-trade risk.
- **Global risk tolerance** in `strategy_config.json` scales everything. `conservative` halves all base sizes and caps the confluence multiplier at 1.5×. `aggressive` uses full base sizes and uncapped multiplier up to the per-trade risk limit.
- **Gates that still apply:** min conviction per trade, min R:R, portfolio-level limits (concurrent, per-ticker, per-sector, gross exposure), available risk capital.
- **Short-term trades carry tighter exits.** A 15m-origin trade uses the 15m agent's stop/target; a weekly-origin trade uses the weekly agent's stop/target. Mixed-confluence trades use the highest-weight timeframe's levels.

**Runs on:** Sonnet (heavier reasoning, fewer calls).

**Inputs:** signals DB, paper ledger state, current strategy version, portfolio limits.
**Outputs:** trade open/close orders (paper), decision rationale appended to log.

### 3.3 Performance analyzer (1)

Runs every night after close. Marks all open positions to market. Computes P&L, hit rate, average winner/loser, profit factor, max drawdown — all segmented by: agent (which timeframe recommended this), conviction bucket, strategy family, sector, ticker. Writes a daily performance row and weekly attribution report.

**Runs on:** Haiku for number-crunching wrapup; Sonnet for the weekly synthesis report.

### 3.4 Strategy evolver (1)

Runs weekly (Sunday evening). Reads the performance attribution report + ledger history. Proposes specific rule changes with evidence. Never applies changes automatically — writes a new draft version of `strategy_config.json` that Dan reviews and approves via Claude.

**Runs on:** Sonnet (wants the best reasoning for self-improvement proposals).

**Example proposals:**
- "15m signals alone had a 38% hit rate with 0.85 profit factor — recommend requiring 15m confluence with at least 1h or 4h."
- "Conviction 8+ trades had 62% hit rate vs 48% overall — recommend bumping min conviction threshold to 7."
- "Long puts on TSLA performed poorly (−$1,240) — recommend disabling long-put strategy for TSLA and relying on TSLQ instead."

---

## 4. Core Python modules

### 4.1 `analysis/indicators.py`
Pure functions for EMA, SMA, RSI, MACD, ATR, Bollinger Bands, VWAP, volume profile. Used by both the research agents (agent calls them via tool) and the backtester.

### 4.2 `analysis/confluence.py`
Takes a list of per-timeframe recommendations for a ticker and computes:
- Directional agreement score (do timeframes agree on long/short?)
- Conviction-weighted confluence score 0–100
- Dominant timeframe (which one has the highest conviction?)
- R:R aggregation (use the highest-timeframe stop/target for the trade)

### 4.3 `paper_trading/ledger.py`
SQLite-backed append-only ledger. Tables:
- `positions_open` — ticker, strategy, direction, entry, stop, target, size, entry_ts, conviction, strategy_version, opening_signals_json
- `positions_closed` — above + exit_ts, exit_price, exit_reason, pnl_gross, pnl_net, hold_days
- `daily_marks` — date, ticker, mark_price, unrealized_pnl, equity
- `daily_equity` — date, cash, unrealized, realized, total_equity, drawdown
- `trade_log` — append-only audit trail

### 4.4 `paper_trading/portfolio.py`
Portfolio state + risk rules enforcement. Methods:
- `can_open(candidate)` — checks concurrent position limit, per-ticker limit, sector limit, gross exposure, available risk capital
- `position_size(entry, stop, equity)` — computes share/contract count from per-trade risk %
- `mark_to_market(bars)` — updates unrealized P&L for all open positions

### 4.5 `clients/polygon_client.py` ✅ (already built)
### 4.6 `clients/uw_client.py` ✅ (already built)
### 4.7 `data/cache.py`
SQLite cache for bars + options chains. Dedup by (ticker, timeframe, bar_ts). Handles incremental updates so hourly runs only fetch new bars.

---

## 5. Run cadence

Dedicated machine is in ET. All times below are ET. Extended hours included per Dan's preference — pre-market 4:00 AM to 9:30 AM, regular 9:30 AM to 4:00 PM, after-hours 4:00 PM to 8:00 PM.

| Job                   | Schedule                                           | What it does                                     |
|-----------------------|----------------------------------------------------|--------------------------------------------------|
| 15m research pass     | Every 30 min, 4:00a–8:00p, M–F (trading days only) | Refresh 15m bars + run 15m agent, all tickers    |
| 1h research pass      | Every hour, 5:00a–8:00p, M–F                       | Refresh 1h bars + run 1h agent, all tickers      |
| 4h research pass      | 8:00a, 12:00p, 4:00p, 8:00p, M–F                   | Refresh 4h bars + run 4h agent                   |
| Daily research pass   | 8:15p (post extended hours close), M–F             | Refresh daily bars + run daily agent             |
| Weekly research pass  | Friday 8:20p                                       | Refresh weekly bars + run weekly agent           |
| Decision pass         | 9:45a, 12:30p, 2:45p, 4:15p, 8:15p                 | Read latest signals, open/close paper trades     |
| Performance nightly   | 8:30p daily, M–F                                   | MTM, compute P&L, write daily report             |
| Performance weekly    | Friday 9:00p                                       | Weekly attribution report                        |
| Strategy evolver      | Sunday 7:00p                                       | Propose rule changes for Dan's review            |

**Trading calendar:** Jobs skip US market holidays automatically. Uses a hard-coded holiday list in v1 (Martin Luther King Jr. Day, Presidents' Day, Good Friday, Memorial Day, Juneteenth, July 4, Labor Day, Thanksgiving, Christmas, New Year's Day). Early-close days (e.g., day before Thanksgiving) use reduced windows.

**Cost sanity check at the reduced 30-min cadence:**
- 15m pass: 32 runs/day × 25 tickers = 800 calls/day
- 1h pass: 16 runs/day × 25 tickers = 400 calls/day
- 4h pass: 4 runs/day × 25 tickers = 100 calls/day
- Daily + weekly passes: ~125 calls/day avg
- Decision passes: 5/day × 25 tickers reasoned together ≈ 5 Sonnet calls/day
- Performance: 1 Sonnet call/day
- **Total: ~1,425 Haiku calls + 6 Sonnet calls per trading day**

At Haiku pricing (small prompts, <1k tokens input/output each), ~$1.50–$3/day LLM cost is realistic. Polygon + UW costs are fixed by subscription.

---

## 6. Deployment

### 6.1 Target: dedicated 24/7 machine
- macOS or Linux, doesn't matter. Runs Python 3.11+.
- Clone the project directory onto that machine.
- `pip install -r requirements.txt` into a venv.
- Fill in `.env` with API keys.
- Install scheduled jobs using one of:
  - **Linux:** systemd timers (I'll provide `.service` + `.timer` unit files)
  - **macOS:** `launchd` plists
  - **Either:** cron (simplest, I'll provide a crontab)
- Outputs (reports, ledger, logs) live in the project folder. Sync that folder to your main machine via iCloud / Dropbox / Syncthing / rsync so you can read reports from Claude on your main machine.

### 6.2 Optional: Docker
A single `Dockerfile` + `docker-compose.yml`. The container runs a supervisor process that invokes the schedule via APScheduler (instead of OS cron). Easier to deploy, easier to reproduce, but the venv approach is simpler for v1.

### 6.3 Claude interaction from main machine
The main Claude session on Dan's laptop reads from the synced folder. Typical queries:
- "What does the signals DB say about NVDA right now?"
- "Show me all paper trades from this week"
- "What did the evolver propose Sunday?"

Claude can also trigger one-off runs on the dedicated machine via SSH if desired, but not required.

---

## 7. Observability + reports

Everything writes markdown reports to `reports/` in addition to the SQLite DB. Format:

- `reports/research/YYYY-MM-DD/<tf>/<ticker>.md` — one file per research pass
- `reports/decisions/YYYY-MM-DD.md` — each day's decisions and rationale
- `reports/performance/daily/YYYY-MM-DD.md`
- `reports/performance/weekly/YYYY-Www.md`
- `reports/evolver/YYYY-Www-proposal.md`

The markdown format means Claude (on either machine) can read them natively without touching the DB. The DB is for aggregation and queries; the markdown is for human + Claude reading.

---

## 8. Design decisions (resolved)

All questions from the first draft have answers. Recorded here for the build pass.

1. **LLM tiers:** Claude Haiku for routine research agents (15m/1h/4h/daily/weekly passes). Claude Sonnet for the decision agent, weekly performance synthesis, and the strategy evolver. Calls go through the Anthropic SDK from Python — not via chat sessions. Requires `ANTHROPIC_API_KEY` in `.env`.

2. **Paper capital:** $25,000 base with 2x margin = $50,000 buying power. Margin interest charged at 8% annualized on borrowed amount (tracked in daily P&L as a drag). Paper account is not subject to PDT but we'll flag any day-trading patterns in reports so the mapping to a real account stays realistic.

3. **Risk per trade:** 2.0% of equity ($500 at start). Hard cap per position regardless of what the decision agent thinks confluence justifies.

4. **Strategy bias: premium income (theta).** Defaults:
   - Credit spreads are the primary income vehicle across all tickers.
   - Wheel (CSP → CC) only on tickers where one contract's collateral is under 30% of buying power (~$15k). Viable set: HIMS, INTC, AMD, CLSK, BE, IBIT, BSOL, SLV, SILJ, CPER, REMX, SATS, ASST, plus MSTR on dips.
   - Directional plays (long options, inverse ETFs) only on strong multi-timeframe confluence, sized smaller than income trades.

5. **Short mechanism:** Long puts + call credit spreads + inverse ETFs where they exist. Additionally, the research layer will identify alternative short tickers / inverse ETFs that could serve as pair hedges (e.g., SOXS for semis exposure, SARK for Cathie Wood exposure). These are surfaced as "suggested pair hedges" in the weekly report for Dan's consideration.

6. **Ticker cleanup:** USOIL → `USO` (US Oil Fund ETF). SATS kept in the universe with a config note that it's EchoStar (comms sector, not crypto). ASST kept (Asset Entities).

7. **Syllabus / accomplishments log:** Not found on this machine. I'll create a fresh `~/Bull-Bot/accomplishments-log.md` tied to this project and append entries as the build progresses. If Dan points me at an existing log later, I'll merge.

8. **Timezone:** ET on the dedicated machine. No conversion needed for the scheduler. Internally all timestamps stored as UTC.

9. **Run cadence:** 15m research runs every 30 minutes (reduced from every 15 min) during extended hours (4:00 AM to 8:00 PM ET). Other timeframes per §5 table.

10. **Ticker universe count:** 25 tickers + 2 inverse ETFs (TSLQ, SDS) = 27 symbols fetched.

---

## 9. What's already built (keep)

- `README.md` — overview (will need minor update after this plan is approved)
- `requirements.txt` — Python deps
- `.env.template` — API key template
- `config.py` — tickers, timeframes, risk rules
- `clients/polygon_client.py` — Polygon wrapper
- `clients/uw_client.py` — Unusual Whales wrapper

All empty-stub directories created: `analysis/`, `paper_trading/`, `agents/`, `scripts/`, `backtest/`, `strategies/`, `reports/`, `cache/`, `logs/`, `data/`.

---

## 10. Exit logic and position management

Entry logic is only half the problem — exits drive realized P&L. Rules the decision agent and nightly performance pass enforce:

### 11.1 Equity / long-option exits
- **Hard stop:** hit the stop price set at entry. Fill at next bar close (conservative).
- **Profit target:** hit target_1 → scale out 50%; hit target_2 → close remainder.
- **Signal reversal:** if the originating timeframe's research agent flips direction with conviction ≥ 7, close at next decision pass.
- **Confluence decay:** if aggregate confluence score drops below 40 while position is open, tighten stop to breakeven.
- **Time stop:** if a directional trade hasn't moved 1×ATR in its favor within (1.5 × the originating timeframe's bar count), close.

### 11.2 Credit spread exits
- **Profit target:** close at 50% of max profit (industry standard for theta strategies).
- **Loss target:** close at 2× credit received (i.e., −200% of credit).
- **Time exit:** close at 21 DTE regardless of P&L to avoid gamma risk.
- **Earnings exit:** close any spread with earnings in the position's lifetime, 1 trading day before the announcement.

### 11.3 Wheel exits
- **CSP assignment:** if the short put goes ITM at expiry, simulate assignment → the ledger flips from "short put" to "100 long shares" at the strike price.
- **CC assignment:** if the short call goes ITM at expiry, simulate assignment → shares called away at strike + short call credit.
- **Early close:** close short options at 50% of max profit if hit before expiration.

### 11.4 Stop check cadence
- Stops are checked at the end of each bar for the originating timeframe. A 1h trade has its stop checked at 1h bar close, not every 15 min.
- Plus a "safety check" every decision pass (up to 5 times a day) to catch stops that were hit during a window the originating timeframe's next bar hasn't closed yet. Safety-check fills are at the current 15m bar close as the conservative proxy.

### 11.5 Options expiration handling
- Positions held to expiry are auto-closed on the performance nightly pass of the expiration date. OTM → expire worthless; ITM → cash-settled at intrinsic value for long options; assignment for short options per the wheel rules.

---

## 11. Risk controls and circuit breakers

### 12.1 Earnings risk
- No new positions opened on a ticker within 3 trading days of its earnings date.
- Existing open positions: if a spread's expiry is after earnings, close 1 day before. If a long option's target holding period crosses earnings, flag for Dan in the daily report before the decision agent opens it.
- Earnings calendar pulled daily from Unusual Whales.

### 12.2 Drawdown circuit breakers
- **Soft breaker:** if paper equity drops 5% from 20-day peak, the decision agent is capped at 0.5% risk per trade and must wait for confluence ≥ 75 to open new trades.
- **Hard breaker:** if paper equity drops 10% from 20-day peak, the decision agent stops opening new trades entirely and the nightly report flags the breaker. Only Dan can clear it via a chat instruction.

### 12.3 Consecutive-loss kill switch
- 7 consecutive losing trades (any strategy) → pause new trade opens for the rest of the trading day, report flagged.

### 12.4 Per-position hard cap
- No single position can lose more than the per-trade risk cap ($500 at start) regardless of stop behavior. If slippage would cause a larger loss, the ledger still records it but an alert fires in the daily report.

### 12.5 Weekend gap risk
- Short-DTE credit spreads (< 2 DTE on Friday) are auto-closed before Friday market close to avoid weekend gap assignment risk.

---

## 12. Error handling, data quality, and rate limits

### 13.1 Data quality gates
- Bars with 0 volume during trading hours are flagged as "stale" and not used for signal generation.
- Missing bars: if the expected number of bars for the lookback window is short by > 10%, skip that ticker's research pass and log a warning.
- Halted stocks (detected via Polygon snapshot status) are skipped for the halt duration.

### 13.2 LLM call failure handling
- Each research agent call is wrapped in retry (3x with exponential backoff) and schema validation (Pydantic model matching the output schema).
- If an LLM returns invalid JSON after 3 retries, the ticker/timeframe gets a `neutral` signal for that pass with a `data_quality_failure` flag.
- If the decision agent returns invalid output, no trades are opened that pass and the failure is logged loudly.

### 13.3 API rate limiters
- Polygon client: max 5 req/sec (adjustable based on Dan's subscription tier).
- Unusual Whales: max 2 req/sec.
- Anthropic SDK: max 8 parallel requests (well under Sonnet tier limits).
- Built using simple token-bucket limiters in each client class.

### 13.4 Crash recovery
- On startup, the scheduler runs a `reconcile` step that:
  - Loads open positions from the ledger
  - Marks them to market using latest bars
  - Replays any missed performance passes for days the bot was down
  - Does NOT retroactively open trades for signals generated while down (avoids backfilling decisions into stale data)

---

## 13. Strategy versioning and evolver workflow

### 14.1 Storage
- `strategy_config.json` — the **current active** strategy used by the decision agent.
- `strategy_versions/` — directory containing timestamped snapshots: `v001_2026-04-09.json`, `v002_2026-04-16.json`, etc.
- `strategy_versions/pending/` — proposed versions from the evolver awaiting Dan's approval.

### 14.2 Evolver proposal format
Each proposal is a pair of files:
- `pending/v003_proposal.json` — the new config
- `pending/v003_rationale.md` — human-readable diff + evidence from the performance attribution

### 14.3 Approval workflow
Dan reviews the rationale markdown from Claude ("what did the evolver propose this week?"), then either:
- **Approve:** Claude moves `pending/v003_*.json` → `strategy_versions/v003_*.json` and overwrites `strategy_config.json`. The active version updates on the next decision pass.
- **Modify:** Dan gives edits in chat; Claude adjusts the pending file before approving.
- **Reject:** Claude deletes the pending file, evolver retries the next week.

### 14.4 Attribution by strategy version
- Every trade in the ledger records the strategy version active at entry time.
- Performance reports can segment P&L by version to answer "did v003 actually improve on v002?"
- If a new version underperforms its predecessor for 2 consecutive weeks, Claude alerts Dan to consider rolling back.

---

## 14. Observability and audit trail

### 15.1 Structured logs
- Every agent call (research, decision, performance, evolver) writes a JSONL line to `logs/agents/YYYY-MM-DD.jsonl` with:
  - timestamp, agent name, ticker (if applicable), model used, input tokens, output tokens, latency, success/failure
- Separate `logs/api/YYYY-MM-DD.jsonl` for Polygon/UW calls.

### 15.2 Decision audit
- Every decision pass writes `reports/decisions/YYYY-MM-DD_HHMM.md` containing:
  - Full signal snapshot for all tickers at that moment
  - Portfolio state (open positions, available risk)
  - Strategy version active
  - Each candidate trade considered, whether it was opened, and why/why not
  - All trades opened with their rationale

### 15.3 Daily narrative report
Every night, the performance agent produces a plain-English narrative summary:
> "Today we opened 3 positions (TSLA put credit spread, HIMS cash-secured put, NVDA long call). 2 positions closed: SPY put credit spread at 52% max profit (+$180), INTC covered call at expiry (+$45). Daily P&L: +$210. Drawdown from peak: 0.8%. Notable: the 4h agent flipped bearish on META mid-day but confluence was not yet high enough to open a short."

### 15.4 Benchmark tracking
- Daily equity curve compared to buy-and-hold SPY over the same period.
- Sharpe ratio, max drawdown, profit factor, win rate — all vs. the SPY benchmark.

### 15.5 Notifications (optional)
- If `NOTIFY_WEBHOOK_URL` is set in `.env`, the bot posts short alerts to Slack/Discord on:
  - Trade opened/closed
  - Drawdown circuit breaker triggered
  - Kill switch activated
  - Evolver proposal ready for review
  - Data quality failure (multiple tickers)

---

## 15. What I'll build next (after approval)

In order, each committed as I go so you can stop me at any point:

1. `data/cache.py` — SQLite cache for bars and options, WAL mode, dedup
2. `analysis/indicators.py` — EMA/RSI/MACD/ATR/VWAP/volume profile
3. `analysis/confluence.py` — confluence scoring with size multiplier
4. `paper_trading/ledger.py` — multi-leg paper ledger + fills + crash recovery
5. `paper_trading/portfolio.py` — risk rules, circuit breakers, kill switch
6. `paper_trading/fill_model.py` — bar-close fills, option mid + slippage
7. `strategy_config.json` — v1 strategy with per-ticker eligibility
8. `strategy_versions/` structure + approval workflow helper
9. `agents/research_base.md` + 5 timeframe deltas (15m, 1h, 4h, 1d, 1w)
10. `agents/decision_agent.md`
11. `agents/performance_agent.md`
12. `agents/evolver_agent.md`
13. `clients/anthropic_client.py` — Anthropic SDK wrapper with retry + schema validation
14. `clients/calendar.py` — US market holiday calendar + trading-day checks
15. `scripts/run_research.py --timeframe 15m|1h|4h|1d|1w`
16. `scripts/run_decision.py`
17. `scripts/run_performance.py`
18. `scripts/run_evolver.py`
19. `scripts/reconcile.py` — crash recovery / DB bootstrap
20. `scripts/seed_db.py` — initialize tables, seed capital
21. Smoke test pass (run one research + decision cycle on 3 tickers)
22. Deployment assets: ET crontab, systemd units, optional Dockerfile
23. Update README with final architecture + deployment instructions
24. Log this project to the accomplishments log

**Stop criteria:** I stop after smoke test and check in before setting up any schedules — scheduling something that runs during market hours without review would be reckless.

---

## 16. Second-pass review — gaps I found on re-read

After drafting sections 1–9, I walked through the plan pretending I was a reviewer who's about to run this for a month and asked "what breaks first?" Everything added in sections 10–14 came out of that exercise. Explicit list of what I had missed on the first draft:

### 16.1 Gaps that would have broken the bot within a week
- **No exit rules.** First draft had entries and confluence but no systematic exit logic. Added §10.
- **No earnings risk handling.** Selling options through earnings without a rule is how accounts blow up. Added §11.1.
- **No options expiration handling.** Positions held to expiry needed explicit logic for assignment, cash settlement, and roll. Added §10.5.
- **No stop-check cadence spec.** "Stops are checked" is ambiguous — which timeframe's bar triggers them? Added §10.4.
- **No multi-leg position representation.** Credit spreads are two contracts; the ledger needs to know it's one "position." Called out in §15 item 4.
- **No crash recovery / reconcile.** If the bot crashes mid-day, how does it know what's open on restart? Added §12.4 and script in §15.

### 16.2 Gaps that would have produced unreliable data
- **No data quality gates.** Zero-volume bars, missing bars, halted stocks — all silent corruption sources. Added §12.1.
- **No LLM failure handling.** Research agents returning invalid JSON would crash the pipeline. Added §12.2 with retry + schema validation.
- **No API rate limiters.** Polygon, UW, and Anthropic all rate-limit; bursts during peak hours would cause cascading failures. Added §12.3.
- **No schema validation on agent outputs.** Trusting LLM output structure without validation is how silent logic bugs happen. Added §12.2.

### 16.3 Gaps that would have made attribution impossible
- **No strategy version tagging on trades.** Without it, you can't answer "did the evolver's changes actually help?" Added §13.4 — every trade records the active version.
- **No structured logging.** Debugging production issues without JSONL logs is guesswork. Added §14.1.
- **No decision audit trail.** Needed to answer "why did the bot open this trade?" weeks later. Added §14.2.
- **No benchmark comparison.** Can't tell if +5% monthly is good or bad without comparing to SPY over the same period. Added §14.4.

### 16.4 Gaps around safety
- **No drawdown circuit breaker.** A bad week could keep trading toward zero. Added §11.2 with soft and hard levels.
- **No consecutive-loss kill switch.** Same problem on a different axis. Added §11.3.
- **No weekend gap rule for credit spreads.** Friday 0-DTE spreads held over the weekend is how you wake up assigned on Monday. Added §11.5.
- **No per-position hard cap.** Without it, slippage could exceed the configured risk in extreme cases. Added §11.4.

### 16.5 Gaps around the evolver
- **No explicit storage structure for strategy versions.** Unclear where the evolver writes its proposals vs. active config. Added §13.1.
- **No approval workflow.** The evolver could "propose" changes into a vacuum. Added §13.3 with approve/modify/reject steps.
- **No rollback mechanism.** If a new version underperforms, there was no way to detect it. Added §13.4 with 2-week underperformance alerting.

### 16.6 Gaps I noticed but chose to defer
- **Greeks rollup at the portfolio level.** Net delta/gamma/theta/vega across all positions is valuable for risk management but adds complexity. Defer to v2 after the base system proves out.
- **Tax lot accounting for wheel assignments.** Proper FIFO cost basis tracking for CC assignments. Paper trading doesn't strictly need it. Defer.
- **Crypto ETF weekend gaps (IBIT/BSOL).** The underlying (BTC/SOL) trades 24/7 but the ETFs don't. Weekend moves create Monday gap risk. Acknowledged in §12 but no explicit rule — flag it in the daily report and let Dan decide.
- **Schema migration framework.** If I change the DB schema later, existing data needs to migrate. For v1, the schema is stable enough that drop-and-recreate is acceptable during dev. Will address before first production schema change.
- **Notification system beyond webhook.** SMS, email, richer formatting — optional. Webhook to Slack/Discord is enough for v1.
- **Multi-agent parallelization.** Running 5 research agents in parallel per ticker would speed up the research pass but adds complexity around ordering and rate limiting. Start serial, parallelize if the hourly pass is too slow.

### 16.7 Things I'm still uncertain about and want your input
- **Polygon options data tier.** The plan assumes Polygon provides historical options chains and greeks. This depends on your Polygon subscription tier. If you only have stocks data, the options backtest logic needs a Black-Scholes fallback and UW becomes the primary options source. Please confirm what your Polygon plan includes.
- **Extended hours bar behavior.** Polygon's 15m bars include extended hours data, but indicators like VWAP get weird across the pre-market gap. I'll anchor VWAP to the regular-session start by default and offer a flag to include extended hours.
- **Black swan handling.** If the market gaps 5% overnight, every stop is stale and every paper position is mispriced. I'll mark positions at the opening bar, flag large gaps in the report, and let you decide whether the simulator should also pause trading until confluence rebuilds.
- **UW API exact paths.** My UW client uses best-guess endpoint paths. Your UW API plan may differ. I'll validate paths against your real API key during the smoke test and fix any mismatches before the first scheduled run.
