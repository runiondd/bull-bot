# Bull-Bot Work Plan

**Status:** Approved for build (v2.1)
**Version:** 2.1
**Last updated:** 2026-04-09

This document breaks the build into concrete, sequenced tasks with dependencies, rough effort estimates, and acceptance criteria. It's meant to be read after `ARCHITECTURE.md` v2 and `WORK_PLAN_REVIEW.md` and before any code is written.

**Changes from v2.0:** Folded in all must-fix and should-fix items from `WORK_PLAN_REVIEW.md`. Tiered bootstrap (T9.3a/b/c) replacing single bootstrap task. Phase 9 reordered so live can start before bootstrap completes. Engine parity test + fixture prompts added to Phase 4. Walk-forward runner decomposed into sub-tasks. Several acceptance criteria tightened.

**Effort scale:**
- **S** = small, < 1 hour of Claude time
- **M** = medium, 1–3 hours
- **L** = large, 3–6 hours

**Total estimated effort:** ~90 hours of Claude build time, 12–13 sessions.

**Real-money cost:** ~$380 one-time at T9.3 (tiered bootstrap), plus ongoing live operational LLM spend (~$15–30/day during live operation, falling over time as caches fill).

---

## Phase 0: Environment + pre-build validation

### T0.0 Environment bootstrap (S)
Verify Python 3.11+, create venv, `pip install -r requirements.txt`, copy `.env.template` → `.env` and populate, `python -c "import config; print(config.TICKERS)"` as a smoke test.

**Acceptance:** `venv/bin/python -c "import config"` succeeds and prints the ticker list.

**Blocker for:** everything.

### T0.1 Verify Polygon subscription + per-timeframe depth (M)
Smoke test Polygon with Dan's real key. Confirm historical depth per timeframe:
- Weekly bars going back to 2021-01-01 (5 years) — *was 10y; shortened 2026-05-13 because Polygon Starter caps weekly at ~5y and we declined to pay for the tier upgrade until paper P&L justifies it. Decision in `.mentor/proposals/2026-05-13-polygon-tier-decision.md` (Option 1, accepted DR).*
- Daily bars going back to 2021-01-01 (5 years)
- 4h bars going back to 2023-01-01 (3 years)
- 1h bars going back to 2024-04-01 (2 years)
- 15m bars going back to 2025-04-01 (1 year)
- Historical options chains via `expired=true&as_of=...`
- Live stock and options snapshots

**Acceptance:** Each depth target returns fully populated bars, OR a concrete fallback is decided and written to `docs/NOTES.md`. Historical option chains either return usable data or trigger T0.1a.

**Blocker for:** T1.3a, T2.1, T5.x, T9.x.

### T0.1a Historical options data fallback (M, conditional on T0.1)
If Polygon doesn't return historical option chains with usable greeks, decide between alternative data source (CBOE DataShop, ORATS), computed greeks from historical IV + Black-Scholes, or scope reduction. Written decision in `docs/NOTES.md`.

### T0.2 Verify Unusual Whales subscription + historical depth (M)
Smoke test UW endpoints for flow, GEX, IV rank, earnings calendar, dark pool. For each, confirm both current and historical access depth. Fix any endpoint path mismatches in `clients/uw_client.py`.

**Acceptance:** All endpoints return populated data. Historical depth per field documented. Any field with insufficient depth triggers T0.2a.

**Blocker for:** T2.2, T5.x, T9.x.

### T0.2a UW historical fallback (M, conditional on T0.2)
For any UW field with insufficient historical depth, decide fallback:
- IV rank: compute from Polygon historical option IVs via rolling 52-week percentile
- Earnings history: use Finnhub/Nasdaq calendar as backup
- Flow history: skip backtest on flow-conditional rules, use live-only
- GEX/dark pool history: deferred to v2 if unavailable

Written decision in `docs/NOTES.md`.

### T0.3 Verify Anthropic API key and SDK setup (S)
Confirm `ANTHROPIC_API_KEY` in `.env`, test Haiku + Sonnet calls via official SDK. Confirm cost-per-token assumptions on a typical prompt size.

**Acceptance:** Both model calls succeed. Per-call cost estimate documented for Haiku-research, Sonnet-research, Sonnet-decision prompt sizes.

**Blocker for:** T2.3.

### T0.4 Decide on reports sync mechanism (S, Dan decision)
Pick one: Dropbox / iCloud Drive / Syncthing / Google Drive / SSH-only.

### T0.5 Confirm dedicated machine OS + deploy path (S, Dan decision)
Pick one: cron on macOS, cron on Linux, systemd on Linux, Docker supervisor. Determines which of T8.1/T8.2/T8.3 get built.

---

## Phase 1: Foundations (prereqs for everything)

### T1.0a Pydantic schemas module (M)
Create `schemas/` directory with one file per model family:
- `schemas/signals.py` — Signal, ConfluenceResult
- `schemas/decisions.py` — DecisionCandidate, DecisionOutput, RejectedCandidate
- `schemas/performance.py` — DailyReport, WeeklyReport
- `schemas/evolver.py` — EvolverProposal, ProposedChange
- `schemas/config.py` — StrategyConfig, RuntimeState, TickerEligibility
- `schemas/trading.py` — Position, Leg, Fill
- `schemas/backtest.py` — BacktestResult, BacktestRun, WalkForwardWindow, FaithfulnessCheck, ApprovalStatus
- `schemas/regime.py` — MacroRegime, IntradayRegime

**Acceptance:** All schemas import cleanly, have example fixtures in `schemas/fixtures/`, and round-trip JSON serialization via pytest.

**Blocker for:** T1.2, T2.3, T3.x, T4.x, T5.x, T6.x.

### T1.0b Logging infrastructure (S)
`utils/logging.py` with JSON formatter. Directory structure `logs/research/`, `logs/decisions/`, `logs/performance/`, `logs/backtest/`, `logs/errors/`. Daily rotation, keep 30 days. Single `get_logger(name)` helper.

**Acceptance:** Any module can `from utils.logging import get_logger; log = get_logger(__name__)` and produce structured logs.

### T1.0c Test harness bootstrap (S)
`tests/conftest.py` with fixtures: `tmp_db`, `sample_bars_tsla`, `sample_signals`, `fake_polygon_responses`, `fake_anthropic_responses`, `frozen_clock`. `tests/fixtures/` with saved JSON payloads. `pytest.ini`, coverage config. Smoke test that confirms pytest runs.

**Acceptance:** `pytest` runs and passes the smoke test.

### T1.1 Install trading calendar (S)
Add `pandas_market_calendars>=4.4.0` to `requirements.txt`. `clients/calendar.py` with `is_trading_day`, `is_early_close`, `session_window`, `next_trading_day`, `previous_trading_day`, `trading_days_between`.

**Acceptance:** Unit test hits 2026 US market holidays and early-close days correctly.

### T1.2 Build SQLite schema and migrations (M)
Create `data/schema.sql` implementing v2 schema (all tables with `run_id` partitioning, `regime_labels`, `backtest_runs`, `walk_forward_windows`, `faithfulness_checks`, `llm_output_cache`, `bootstrap_checkpoints`, `strategy_promotions`). `data/db.py` with connection helper (WAL), `initialize_schema`, `backup_db`, migration version table.

`scripts/seed_db.py` initializes fresh DB and seeds `daily_equity` for `run_id="live"`.

**Acceptance:** Fresh DB has all tables correctly indexed on `(run_id, ...)`. `latest_signals` PK `(run_id, ticker, timeframe)` with `ON CONFLICT DO UPDATE` tested. `llm_output_cache` schema includes prompt_hash column.

**Depends on:** T1.0a, T1.0b, T1.1.

### T1.3 Build data cache layer (M)
`data/cache.py` with `upsert_bars`, `get_bars(ticker, tf, end_ts, limit)`, `get_bars_range`, `upsert_options_chain`, `get_latest_options_quote(contract_id, as_of_ts)`, `move_cold_bars_to_parquet`, `read_bars_unified(ticker, tf, start, end)` (transparent SQLite + parquet).

**Acceptance:** Unit tests verify dedup, cutoff-by-end-ts, cross-ticker isolation, parquet round-trip.

**Depends on:** T1.2.

### T1.3a Historical bar backfill script (M)
`scripts/backfill_history.py --timeframe <tf>|all` reads `config.BACKTEST_HISTORY_BARS` and populates cache per-timeframe depth. Rate limit aware. Idempotent. Prints progress + cost estimate.

**Config additions (in config.py):**
```python
BACKTEST_HISTORY_BARS = {
    "1w": 520, "1d": 1260, "4h": 4500, "1h": 3500, "15m": 6500,
}

# Index tickers for regime classification (backfilled separately, not in TICKERS universe)
INDEX_TICKERS = {
    "SPY": {"depths": {"1d": 2520, "15m": 6500}},  # 10 years daily, 1 year 15m
    "VIX": {"depths": {"1d": 2520}},                # 10 years daily
}
```

**Acceptance:** Backfill completes, row counts match expected, re-run is a no-op. Index tickers SPY and VIX populated independently of the trading universe.

**Depends on:** T0.1, T1.3.

### T1.4 Technical indicators library (M)
`analysis/indicators.py` — pure functions for EMA, SMA, RSI, MACD, ATR, Bollinger, vwap_session, vwap_rolling, volume_profile, support_resistance. Numpy in/out, no I/O.

**Acceptance:** Unit tests against a known reference dataset.

### T1.5 Technical features writer (S)
`analysis/features.py` with `compute_features(ticker, timeframe, end_ts)` that loads bars with cutoff, computes indicators, writes to `tech_features`, returns dict.

**Acceptance:** On seeded bars, produces complete `tech_features` row.

**Depends on:** T1.3, T1.4.

### T1.6 Confluence scorer (S)
`analysis/confluence.py` implementing Appendix A.1 math. Writes to `confluence` table.

**Acceptance:** Unit tests with hand-crafted signal combinations match expected scores.

**Depends on:** T1.0a.

### T1.7 Regime classifier (M)
`analysis/regime.py` implementing Appendix A.14 rules. `classify_macro(date)` and `classify_intraday(bar_ts)`. Reads from `bars` (including VIX and SPY index tickers) and `iv_rank`. Writes to `regime_labels`.

**Acceptance criteria (expanded):**
- Unit tests with fixture bars produce expected labels
- Spot check: 2020-03-16 → `(high_vol, bear, high)`; 2023-06-01 → `(normal, bull, normal)`
- **Scope test:** regime lookup at cursor 2024-06-14 returns the 2024-06-14 label, not 2024-06-15 (no look-ahead)
- Requires SPY + VIX backfilled (T1.3a dependency)

**Depends on:** T1.3a, T1.4.

### T1.8 Build paper trading ledger (M)
`paper_trading/ledger.py` with Position, Leg, `open_position(position, run_id)`, `close_position`, `mark_to_market(run_id, bar_date)`, `get_open_positions(run_id)`, `get_closed_positions`, `log_event`.

All ledger ops partitioned by `run_id`.

**Acceptance criteria (expanded):**
- Single equity open/mark/close + P&L check
- Multi-leg credit spread with partial leg close + full close
- Mark on holiday → carries forward previous mark
- Mark on halted ticker → flags and skips
- Gap-close where exit fill differs from stop → records actual fill, logs gap reason
- Isolation test: `run_id="live"` and `run_id="bt_test"` don't cross-contaminate

**Depends on:** T1.2.

### T1.9 Exit rule engine (S)
`paper_trading/exit_engine.py` with `check_exits(positions, bars_at_cursor, strategy_config) -> list[ExitAction]`. Handles stops, targets, time-based exits (DTE for credit spreads), hard losses on long options, expiry auto-close.

Called by both decision pass and nightly pass.

**Acceptance:** Unit tests exercise each exit rule family.

**Depends on:** T1.8.

### T1.10 Fill model (S)
`paper_trading/fill_model.py`:
- `fill_equity(order, bar)` — fills at bar close
- `fill_option(order, chain_snapshot, slippage_bps=10)` — mid + slippage
- **Staleness rule:** if option quote > 10 min older than cursor, widen slippage 3× or reject per config
- `validate_liquidity(contract, min_vol=10, min_oi=50)`

**Same module used by live and backtest — no divergent code paths.**

**Acceptance:** Unit tests for each case including stale-quote widening.

### T1.11 Portfolio rules enforcer (M)
`paper_trading/portfolio.py`: `can_open`, `compute_margin_required`, `compute_position_size`, `check_circuit_breakers`, `update_margin_interest`.

**Circuit breaker reset:** manual only. Human writes `trading_paused=false` + `consecutive_loss_count=0` to `runtime_state.json`.

**Acceptance:** Unit tests for concurrent limit, sector limit, earnings blackout, breaker trip, sizing, and explicit breaker reset flow.

**Depends on:** T1.8.

### T1.12 Create v1 `strategy_config.json` (S)
Write initial config with defaults from ARCHITECTURE.md §9.2 + Appendix A.12. Store at project root. Snapshot to `strategy_versions/v001_2026-04-09.json`.

**Acceptance:** Loads cleanly against `schemas/config.py:StrategyConfig`.

### T1.13 Create `runtime_state.json` + helper (S)
Initial file with `trading_paused=false`. `paper_trading/runtime_state.py` with `read`, `write`, `pause`, `resume`, `request_close_all`.

**Acceptance:** Round-trip tests.

---

## Phase 2: API clients hardening

### T2.1 Harden Polygon client (M)
- Token-bucket rate limiter (5 req/sec default)
- `fetch_bars_smart(ticker, timeframe, start, end)` — cache-first, fills gaps
- `fetch_snapshot(ticker)` with 60s in-process cache
- `fetch_options_chain(underlying, as_of_date, expiry_range)` — supports historical
- Black-Scholes greeks fallback
- Mode-aware: backtest mode refuses to hit real-time endpoints

**Acceptance:** Integration test passes. Backtest-safe mode refuses real-time endpoints when `mode=BACKTEST`.

**Depends on:** T0.1, T1.3.

### T2.2 Harden Unusual Whales client (S)
- Rate limiter (2 req/sec)
- `fetch_flow_smart(ticker, since)`
- `fetch_earnings_window(days_ahead=7)`
- Historical fetching for backtest where UW supports it

**Acceptance:** Integration test passes.

**Depends on:** T0.2.

### T2.3 Build Anthropic client wrapper (M)
`clients/anthropic_client.py`:
- `call_agent(prompt, model, schema, max_retries=3) -> parsed`
- Token counting pre-flight
- Retry on 5xx / rate limits
- JSON extraction + Pydantic validation
- Writes `agent_runs` row with cost
- Running daily cost total
- **LLM output cache:** keyed by `(agent_name, strategy_version, prompt_hash, ticker, timeframe, bar_ts)` — `prompt_hash` is a hash of the loaded prompt files, so prompt changes invalidate the cache automatically
- Cost cap enforcement per-run (reads `BACKTEST_COST_LIMITS`)

**Acceptance:** Unit tests with mocked SDK + integration test. Cost cap unit test aborts when exceeded. Prompt-hash invalidation test: changing a prompt file produces a cache miss.

**Depends on:** T0.3, T1.0a.

---

## Phase 3: Agent prompts

### T3.0a Prompt loader utility (S)
`agents/loader.py` with `load_research_prompt(timeframe)` (base + delta concatenation), `load_agent_prompt(name)`, `inject_inputs(prompt_template, inputs_dict)`, `compute_prompt_hash(prompt_name)` (for T2.3 cache keying).

**Acceptance:** Loads base+delta combo; hash is stable across runs for the same files.

### T3.1 Write `agents/research_base.md` (L)
Shared base prompt: role, output schema (JSON example), analysis framework (trend/momentum/structure/volume/levels/catalysts), rules (neutral-if-unclear, R:R must justify the trade), 6–10 few-shot examples covering long/short/neutral/options situations.

**Acceptance:** Prompt passes self-review. Assumes pre-computed features in input, no tool calls.

### T3.2 Five timeframe delta files (M)
`research_15m.md`, `research_1h.md`, `research_4h.md`, `research_1d.md`, `research_1w.md`. Each 15–40 lines with timeframe-specific priorities.

**Acceptance:** Each delta emphasizes a distinct signal set.

**Depends on:** T3.1.

### T3.3 Write `agents/decision_agent.md` (L)
Prompt for the decision agent. Structured inputs, structured output. Key logic: confluence scales size (not gate), strategy family selection, rejection reasoning, cannot bypass `can_open()`.

**Acceptance:** Schema-valid output on hand-crafted tests covering strong confluence, standalone 15m, earnings blackout, breaker active.

### T3.4 Write `agents/performance_agent.md` (M)
Two variants: nightly (narrative + breaker check + cost summary) and weekly (attribution + per-regime breakdown).

**Acceptance:** Schema-valid outputs for both modes.

### T3.5 Write `agents/evolver_agent.md` (L)
Inputs: past 4 weeks trades + attribution, current config, previous proposals + outcomes, portfolio state, current regime. Must cite specific trade IDs as evidence. Small incremental changes only. Flags each change as "numeric" or "prompt change" (affects backtest mode).

**Acceptance:** Schema-valid proposal + rationale on sample data.

---

## Phase 4: Execution Engine (unified core)

### T4.1 Cursor and ExecMode (S)
`engine/cursor.py` with `Cursor` class (`now`, `at`, `bar` constructors) and `ExecMode` enum (`LIVE`, `BACKTEST_FULL`, `BACKTEST_CHEAP`, `BACKTEST_HYBRID`).

**Acceptance:** Round-trip tests, serialization.

### T4.2 Build `engine/step.py` (L)
Core `engine.step(cursor, mode, run_id) -> StepResult` function. Implements ARCHITECTURE.md §3 flow.

**Acceptance (expanded):**
- Deterministic test with frozen cursor produces identical results across 10 runs
- `run_id="live"` and `run_id="bt_test"` on same cursor produce isolated ledger state
- Every ExecMode behaves correctly
- **Consistency assertion:** `run_id=="live"` implies `mode==LIVE`, and vice versa. Mixing raises `RuntimeError` unless `allow_mixed=True` (for tests only).

**Depends on:** T1.3, T1.5, T1.6, T1.7, T1.8, T1.9, T1.10, T1.11, T2.x, T4.2b.

### T4.2a Engine parity test (M)
Catches drift between LIVE and BACKTEST modes at code time, not run time.
- `engine.step(Cursor.at(2024-06-14 10:30), ExecMode.LIVE)` against mocked API layer returning canned historical data
- `engine.step(Cursor.at(2024-06-14 10:30), ExecMode.BACKTEST_FULL)` against real cache loaded with same canned data
- Assert `StepResult` objects match (modulo `run_id` and timestamp fields)
- Part of CI on every engine change

**Acceptance:** Test passes deterministically; intentional bug injection causes it to fail loudly.

**Depends on:** T4.2.

### T4.2b Engine fixture prompts (S)
Minimal stub `research_base.md` and `decision_agent.md` that emit valid JSON with fixed values. Used for engine testing before real prompts land.

**Acceptance:** Engine parity tests and smoke tests run against stubs without needing real prompts.

**Blocker for:** T4.2 testing (the engine can be tested before Phase 3 completes).

### T4.3 Task router (S)
`engine/task_router.py` maps task name (`research_15m`, `decision`, `nightly`, etc.) to the correct subset of work for `engine.step()`.

**Acceptance:** All task names route correctly; unknown tasks raise.

### T4.4 Cached-output store (M)
`engine/llm_cache.py` with `get(agent, strategy_version, prompt_hash, keys) -> dict | None` and `put(...)`. Backed by `llm_output_cache` SQLite table.

**Cache key includes prompt_hash** so prompt changes invalidate automatically.

**Acceptance:** Cache hit rate test: run backtest in FULL mode, then CHEAP mode — second run matches with zero LLM cost. Prompt-change invalidation test.

**Depends on:** T1.2, T2.3.

---

## Phase 5: Backtest Engine

### T5.1 Build `engine/backtest_runner.py` (L)
Iterates cursor from `start_date` to `end_date`, calls `engine.step()` at each scheduled invocation. Creates `backtest_runs` row, accumulates state, computes metrics, writes report.

**CLI:** `scripts/backtest/run_backtest.py --start 2021-01-01 --end 2026-04-01 --mode full|cheap|hybrid --strategy v001 [--timeframes 1d,4h,1h,15m]`

**Acceptance:** 1-month historical backtest runs deterministically, produces `backtest_runs` row + report. Aborts on cost cap with partial state preserved.

**Depends on:** T4.x.

### T5.2 Backtest metrics calculator (M)
`engine/metrics.py` — total return, Sharpe, Sortino, max DD, max DD duration, profit factor, win rate, R-multiple distribution, trade count. Per-slice variants: by_timeframe, by_strategy_family, by_regime, by_ticker.

**Acceptance:** Unit tests against hand-computed metrics on fixture trades.

### T5.3 Backtest report generator (M)
`engine/backtest_report.py` renders markdown with headline metrics, equity curve, per-slice tables, trade distribution, regime breakdown, cost summary, git SHA of engine code.

**Acceptance:** Runs on seeded backtest result, produces readable report.

**Depends on:** T5.2.

### T5.4a Walk-forward window generator (S)
Pure function: `generate_windows(history_range, timeframe_scope) -> list[WindowSpec]`. Reads `WALK_FORWARD_WINDOWS` from config.

**Acceptance:** Unit tests produce correct train/holdout splits for each scope.

### T5.4b Walk-forward runner (M)
`engine/walk_forward.py:run_walk_forward(proposed_config, timeframe_scope)`. Iterates windows, runs backtest for proposed + baseline per window, writes `walk_forward_windows` rows.

**Min-trades safety:** skip and log any window where `trade_count < min_trades_holdout`. If < 3 usable windows, return "insufficient data" status.

**Acceptance:** Runs on fixture history, produces per-window results, skip-on-insufficient-trades works.

**Depends on:** T5.1, T5.4a.

### T5.4c Walk-forward aggregator (M)
Combines per-window metrics into cross-window summary, per-regime breakdown. Returns `WalkForwardResult`.

**Acceptance:** Hand-computed aggregate matches runner output.

**Depends on:** T5.4b.

### T5.4d Walk-forward integration test (S)
End-to-end test on fixture history: generate windows, run walk-forward, aggregate, validate shape.

**Acceptance:** Passes.

### T5.4e Baseline result cache (S)
Simple SQLite cache keyed by `(strategy_version, train_start, holdout_start, holdout_end)` → metrics_json. Walk-forward runner checks cache before re-running baseline.

**Acceptance:** Second walk-forward run against same baseline is zero-cost on the baseline side.

**Depends on:** T5.4b.

### T5.5 Approval gate validator (M)
`engine/approval_gate.py` implementing Appendix A.16 thresholds. Takes `WalkForwardResult`, returns `ApprovalStatus` with `passed: bool`, `reasons: list[str]`, per-threshold diagnostics.

**Acceptance:** Hand-crafted results (one passes, one fails each threshold) produce expected status.

**Depends on:** T5.4c.

### T5.6 Faithfulness checker (M)
`engine/faithfulness.py` implementing Appendix A.17. Runs 14-day backtest in CHEAP mode, compares to live, writes `faithfulness_checks` row, returns status.

**Acceptance:** Seeded matching test → green; divergent → red. Red triggers notification.

**Depends on:** T5.1.

### T5.7 Bootstrap backtest script (L)
`scripts/backtest/bootstrap.py --tier {1|2|3} [--tickers ...] [--dry-run] [--confirm] [--max-runtime 2h]`.

Implements the tiered bootstrap. Prints per-tier cost estimate in dry-run mode. Checkpoint every 500 calls to `bootstrap_checkpoints` table. Resumable: skips cursor points already in `llm_output_cache`. Aborts on tier cost cap; pauses-for-review at 90% soft cap.

**Tier definitions (from config.py):**
- Tier 1: daily, all 27 tickers, Haiku — ~$100
- Tier 2: weekly, all 27 tickers, Sonnet — ~$280
- Tier 3: intraday (4h/1h/15m), configurable ticker subset, Haiku — deferred

**Acceptance:**
- Dry-run prints detailed per-tier cost estimate
- Confirm mode runs tier to completion; resumes cleanly after interrupt
- 90% soft cap triggers pause + checkpoint; Dan can inspect and resume
- Cost stays within tier cap
- Partial completion preserves state with "incomplete" flag

**Depends on:** T5.1, T5.3.

---

## Phase 6: Live scripts (thin wrappers over engine)

### T6.1 `scripts/live/run_live.py` (M)
Single entry point with `--task {research_15m, research_1h, research_4h, research_1d, research_1w, decision, nightly, weekly, evolver}`. Uses `engine/task_router.py`.

**Acceptance:** Each task fires the right engine subset. Concurrency test: 27 tickers × 15m research completes < 90 sec with asyncio worker pool of 5.

**Depends on:** T4.2, T4.3.

### T6.2 `scripts/live/run_evolver.py` (M)
Runs evolver agent, passes proposal through walk-forward validator, applies approval gate, writes to `strategy_versions/pending/` or `strategy_versions/rejected/` with walk-forward report embedded.

**Trade ID verification:** after evolver returns proposal, verify every trade ID cited in rationale exists in `positions_closed`. Hallucinated IDs flag the proposal invalid.

**Acceptance:** End-to-end on seeded history produces pending proposal + walk-forward report + approval status. Hallucinated ID test rejects.

**Depends on:** T5.4x, T5.5, T3.5.

### T6.2a Strategy promotion audit (S)
`paper_trading/strategy_versions.py:promote(version, approver, walk_forward_result)`. Writes to `strategy_promotions` table: old_version, new_version, approver, ts, walk_forward_report_id, diff_summary. Claude-on-Dan's-behalf promotes via this helper, never direct file copy.

**Acceptance:** Promote helper writes audit row + moves pending to active atomically.

### T6.3 `scripts/reconcile.py` (M)
Per Appendix A.10: marks open positions, replays missed `daily_equity` rows (with `was_backfilled=true`), checks triggered stops/targets during downtime, invalidates stale signals. Only operates on `run_id="live"`. Does not trip circuit breakers on backfilled rows.

**Acceptance:** Simulated 3-day downtime → reconcile fills gap, no breaker trips.

**Depends on:** T1.8, T2.1.

### T6.4 `scripts/smoke_test.py` (M)
Full pipeline test: init DB → research pass (3 tickers × 2 TFs live) → decision pass → nightly pass → 5-day backtest → summary.

**Acceptance:** Runs cleanly on fresh DB.

**Depends on:** T6.1, T5.1.

---

## Phase 7: Integration and hardening

### T7.1 Cost summary in nightly report (S)
Nightly report includes LLM cost for the day, breakdown by agent, running weekly/monthly totals.

**Acceptance:** Nightly report has Cost Summary section with correct numbers.

### T7.1a Cost rollup SQLite view (S)
View `v_cost_summary` aggregating `agent_runs` by day/week/month and run_type (live, backtest, bootstrap).

**Acceptance:** Query returns correct aggregates.

### T7.2 Report file naming with timestamps (S)
`run_live.py` writes `reports/research/YYYY-MM-DD/<tf>/<ticker>_HHMM.md` + `latest.md` symlink. Prevents 15m overwrite.

**Acceptance:** Multiple 15m runs in one day produce distinct files + updated symlink.

### T7.3 Data quality sanity checks (M)
`analysis/data_quality.py` with checks: bar close within X% of last close, VIX in 5–80 range, zero-volume days flagged, missing IV rank flagged.

Runs on feature computation AND during bar backfill AND during backtest cursor advance. Failures aggregated into a data quality report. Fatal threshold (>5% of bars flagged) aborts backfill.

**Acceptance:** Seeded bad data produces expected warnings; fatal threshold test aborts backfill.

---

## Phase 8: Deployment assets

### T8.1 ET crontab file (S, conditional on T0.5=cron)
`deploy/crontab.et` with all scheduled entries. `deploy/install_cron.sh` installer.

### T8.2 systemd unit files (S, conditional on T0.5=systemd)
`.service` + `.timer` files in `deploy/systemd/`.

### T8.3 Dockerfile (S, conditional on T0.5=docker)
Single-stage Dockerfile with venv, supervisor script using APScheduler.

### T8.4 Install doc (M)
README with install steps, sync folder setup, runtime verification, troubleshooting, **secrets hygiene section** (`chmod 600 .env`, `.gitignore`, warning about report contents).

**Acceptance:** Dan can follow from clean machine to running system.

---

## Phase 9: Smoke + go-live (reordered so live can start before bootstrap)

### T9.1 Real-API smoke test (M)
`scripts/smoke_test.py` against real Polygon + UW + Anthropic. Fix breakages.

**Acceptance:** Smoke test passes on real APIs.

### T9.2 Historical backfill run (M)
Execute `scripts/backfill_history.py` against real Polygon/UW. Takes several hours. Populates per-timeframe bar history + regime labels + SPY + VIX.

**Acceptance:** `bars` row count matches expected; `regime_labels` populated for full range.

### T9.3 Manual dry run for 1 trading day (M, Dan-supervised)
**Moved before bootstrap.** Run each `run_live.py --task X` manually during one real trading day. Dan reviews each output.

**Acceptance:** Dan approves live behavior.

### T9.4 Enable schedules (S, Dan-approved)
Install cron/systemd (whichever picked in T0.5). Watch for 24 hours.

**Acceptance:** 24 hours of automatic runs, no errors.

### T9.5a Bootstrap Tier 1 — Daily (M, ~$100)
**Dan-supervised.** Run `scripts/backtest/bootstrap.py --tier 1 --dry-run` to confirm cost. Then `--confirm`. Monitors progress + cost live.

**Acceptance:** Tier 1 complete, cost within cap, daily baseline metrics populated, `llm_output_cache` populated with verified hit rate.

### T9.5b Bootstrap Tier 2 — Weekly (M, ~$280)
**Dan-supervised, only after T9.5a validated.** Same flow for weekly Sonnet.

**Acceptance:** Tier 2 complete, cost within cap, weekly baseline metrics populated.

### T9.5c Bootstrap Tier 3 — Intraday (DEFERRED)
Not run in v1. Revisited after 3 months of live operation or when intraday evolver needs walk-forward validation.

### T9.6 First walk-forward evolver run (M)
After 4 weeks of live data + Tiers 1+2 bootstrap complete, trigger evolver manually. Confirm walk-forward + approval gate + pending/rejected output.

**Acceptance:** End-to-end evolver run produces reviewable proposal.

### T9.7 First faithfulness check (S)
Day 15 of live: confirm nightly pass runs faithfulness check, writes green/yellow/red row.

**Acceptance:** Row written; status in daily report.

### T9.8 Log to accomplishments log (S)
Append entry to `~/Bull-Bot/accomplishments-log.md` capturing scope, Dan's role, skills demonstrated.

---

## Dependency graph (simplified)

```
Phase 0 (pre-build) ──────────► Phase 1 prereqs (T1.0a/b/c)
                                    │
                                    ▼
Phase 1 foundations ────────────► Phase 2 clients
                                    │              │
                                    ▼              ▼
                              Phase 3 prompts + T4.2b stubs
                                    │
                                    ▼
                            Phase 4 engine unification
                                    │
                          ┌─────────┴──────────┐
                          ▼                     ▼
                  Phase 5 backtest        Phase 6 live scripts
                          │                     │
                          └─────────┬───────────┘
                                    ▼
                            Phase 7 integration
                                    │
                                    ▼
                            Phase 8 deployment
                                    │
                                    ▼
                            Phase 9 go-live
                              (live before bootstrap)
```

---

## Session phasing

| Session | Target                                                                  | Est. effort |
|---------|-------------------------------------------------------------------------|-------------|
| 1       | Phase 0 setup + T1.0a/b/c prereqs                                        | 5 h         |
| 2       | T1.1–T1.5 (calendar, schema, cache, backfill script, indicators, features) | 6 h      |
| 3       | T1.6–T1.11 (confluence, regime, ledger, exit engine, fill model, portfolio) | 6 h      |
| 4       | T1.12, T1.13, T2.1, T2.2                                                 | 5 h         |
| 5       | T2.3, T3.0a, T4.2b stubs, T3.1 starts                                    | 5 h         |
| 6       | T3.1 finishes, T3.2, T3.3                                                | 6 h         |
| 7       | T3.4, T3.5, T4.1, T4.2, T4.2a                                            | 6 h         |
| 8       | T4.3, T4.4, T5.1                                                         | 6 h         |
| 9       | T5.2, T5.3, T5.4a/b/c/d/e, T5.5                                          | 6 h         |
| 10      | T5.6, T5.7, T6.1, T6.2, T6.2a                                            | 6 h         |
| 11      | T6.3, T6.4, T7.x, T8.x                                                   | 6 h         |
| 12      | T9.1–T9.4 (smoke, backfill, dry run, enable live)                        | 5 h         |
| 13      | T9.5a/b (bootstrap Tiers 1+2), T9.6, T9.7, T9.8                          | 5 h         |

Reality check: early sessions will run long. Dan can pause between any session.

---

## Definition of done

1. All Phase 0–8 tasks complete and accepted.
2. Tier 1 + Tier 2 bootstrap has populated daily + weekly baseline and LLM output cache.
3. `scripts/smoke_test.py` passes on real APIs.
4. One full trading day of manual dry run with Dan's approval.
5. Scheduled jobs running for 24 hours without errors.
6. Valid daily report in `reports/performance/daily/`.
7. First faithfulness check passes green (or yellow with documented reason).
8. First walk-forward evolver run produces reviewable proposal.
9. Accomplishment entry logged.
10. `README.md` reflects final architecture with accurate install steps.
