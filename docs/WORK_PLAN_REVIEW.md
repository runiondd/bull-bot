# Work Plan Review

**Reviewer:** Claude (second pass)
**Reviewed document:** `docs/WORK_PLAN.md` v2.0
**Architecture basis:** `docs/ARCHITECTURE.md` v2.0 (backtest-as-foundation)
**Date:** 2026-04-09

I re-read `WORK_PLAN.md` v2 as if I were the person who had to execute it next week, with the v1 review findings already folded in and the new backtest engine as a core pillar. The v1 review caught the prereq gaps (schemas, logging, testing), prompt under-scoping, exit engine split, reconcile safety, and filename collision. Those are all in v2. This review focuses on what's new to v2 and what the backtest-first reframing introduced.

---

## 1. Must-fix before build (blockers)

### 1.1 Missing task: historical UW data strategy
T0.2 checks UW endpoint availability but doesn't check historical depth. The architecture assumes regime classification (which uses VIX and IV rank historical) and historical flow for backtest signals, but UW's historical reach is subscription-dependent and probably shallower than Polygon's.

**Risk:** the walk-forward for weekly timeframe needs 3+ years of IV rank and earnings to be meaningful. If UW only exposes the last 12 months historically, the weekly walk-forward degrades into "test on whatever you have," which is exactly the problem we're trying to avoid.

**Action:** Extend T0.2 acceptance to require a written statement of UW historical depth per field (flow, IV rank, earnings, GEX, dark pool). Add a conditional T0.2a "UW historical fallback" for missing fields, similar to T0.1a. Likely fallbacks: compute IV rank ourselves from historical option IV (which depends on Polygon options history), pull historical earnings from a free source (Finnhub, Nasdaq calendar).

### 1.2 Missing task: the engine/live test harness divergence
T4.2 requires a "deterministic test with frozen cursor and mocked agent outputs produces identical results across 10 runs." That's necessary but not sufficient. The real danger is that `engine.step()` in LIVE mode calls real APIs while in BACKTEST modes it calls cache — and those paths can silently drift (different data shapes, different staleness rules, different error handling).

**Risk:** the faithfulness check (T5.6) is supposed to catch drift, but it can't run until day 15 of live operation. Any drift bug introduced before that is hidden until after the first week of live trading.

**Action:** Add T4.2a "Engine parity test":
- Run `engine.step(Cursor.at(2024-06-14 10:30), ExecMode.LIVE)` against a mocked API layer that returns canned historical data.
- Run `engine.step(Cursor.at(2024-06-14 10:30), ExecMode.BACKTEST_FULL)` against the real cache loaded with the same canned data.
- Assert the resulting `StepResult` objects are byte-identical (or differ only in `run_id` and timestamps).
- Run as part of CI on every change to the engine.

This catches drift at code time, not at live-run time.

### 1.3 Bootstrap cost is real money and needs a confirmation contract
T9.3 says "Dan-supervised" and "prompts for confirmation with cost estimate," but the flow is underspecified. The bootstrap is ~$500–700 of real Anthropic spend. If it aborts halfway due to a bug, we've spent real money for partial results.

**Action:** Tighten T5.7 (bootstrap script) acceptance:
- Dry-run mode prints a detailed per-timeframe cost estimate broken down by LLM model used
- Checkpoint every N cursor points (e.g., every 500) so a crash doesn't lose the previous work
- Resumable: re-running the script skips cursor points already in `llm_output_cache`
- Hard cap defaults to `bootstrap_cap_usd * 0.9` so we stop at 90% and review before the final push
- Cost tracker surfaces "cost per ticker-day processed" so we can project total spend halfway through

Without these, bootstrap is one bug away from an expensive mistake.

### 1.4 T5.4 walk-forward is the hardest task in the plan and is marked L without sub-decomposition
Walk-forward validation is effectively 4 interlocking pieces:
- Window generator (split history into train/holdout pairs)
- Runner (execute the proposed + baseline config over each window)
- Aggregator (combine per-window metrics into a summary, per regime)
- Dependency management (baseline re-runs can reuse cached results; proposed runs must use the proposed config)

Marking it as a single L task invites it to stretch into a full session without a clear stopping point.

**Action:** Split T5.4 into:
- T5.4a Window generator (S) — pure function, easy to unit-test
- T5.4b Runner (M) — calls T5.1 for each window
- T5.4c Aggregator (M) — cross-window metrics, per-regime breakdown
- T5.4d Walk-forward integration test (S) — end-to-end on fixture history

### 1.5 Phase 4 depends on Phase 3 for prompts, but the engine needs to be testable without real prompts
T4.2 requires research and decision prompts (T3.1–T3.3) to be done before the engine can run end-to-end. That's a dependency chain that blocks engine testing until session 7 at earliest.

**Risk:** the engine is architecturally central. Bugs in it stall everything downstream. We need to be able to test the engine *before* the prompts are polished.

**Action:** Add T4.2b "Engine fixture prompts":
- Minimal stub research_base.md and decision_agent.md that just emit valid JSON with fixed values
- Used only for testing the engine, never for real runs
- Engine parity tests (T4.2a) and smoke tests run against these stubs until real prompts are ready

This decouples the engine from the prompt sessions.

---

## 2. Should-fix before build (strong recommendations)

### 2.1 Regime classifier needs VIX and SPY history as a hard prerequisite
T1.7 (regime classifier) reads from `bars` and `iv_rank`. But the classifier operates on VIX and SPY specifically, and neither is in the current 27-ticker universe.

**Action:** Add VIX and SPY to a separate "index tickers" list in `config.py` and have T1.3a backfill them automatically (10 years daily, 3 years 15m for intraday regime). Explicitly note this in T1.7 dependencies.

**Current impact:** T1.3a will fail to produce usable regime labels without this. Caught now, it's a 5-minute fix.

### 2.2 Walk-forward windows don't have "minimum data" safety checks
The windows are defined in `WALK_FORWARD_WINDOWS` with `min_trades_holdout` thresholds (Appendix A.15), but the runner doesn't have a task to enforce them. If a walk-forward window has fewer than 20 trades in the holdout (weekly) or 40 (intraday), the metrics are noise.

**Action:** Add to T5.4b acceptance: "Runner skips (and logs) any window where `trade_count < min_trades_holdout`, and the approval gate treats a proposal with < 3 usable windows as 'insufficient data' — rejected with reason."

### 2.3 Baseline caching is implied but not tasked
When walk-forward runs the baseline config on window N, the result should be cached by `(strategy_version, window_spec)` so subsequent proposals re-use the baseline instead of re-running it. Otherwise every evolver proposal re-backtests the baseline over the whole history — expensive and pointless.

**Action:** Add T5.4e "Baseline result cache" (S): simple SQLite-backed cache keyed by (strategy_version, train_start, holdout_start, holdout_end) → metrics_json. Walk-forward runner checks this cache before running the baseline on any window.

### 2.4 `llm_output_cache` invalidation rules are missing
The cache is keyed by `(agent, strategy_version, ticker, timeframe, bar_ts)`. But what happens when a research prompt changes? The cache is still keyed by `strategy_version`, so if the strategy version bumps (because the evolver approved a prompt change), the old cache becomes stale.

**Risk:** CHEAP backtest validating a numeric change might accidentally use a cached output from a prompt version that's no longer active. Silent correctness bug.

**Action:** Update T4.4 acceptance to include: "Cache key includes a hash of the prompt template files (base + delta) in addition to strategy_version. Changing a prompt file automatically invalidates the cache for that agent."

### 2.5 The cost tracker is underspecified for cross-run rollup
Every task writes `agent_runs` rows with `cost_usd`. The bootstrap backtest has `backtest_runs.total_cost_usd`. The nightly report has "cost summary." But there's no single "show me total LLM spend this month" query or dashboard.

**Action:** Add T7.1a "Cost rollup view" (S): a SQLite view `v_cost_summary` that aggregates agent_runs by day/week/month and by run_type (live, backtest, bootstrap). Nightly report queries this view.

### 2.6 T3.x effort on evolver prompt is probably still under-scoped
T3.5 is M+ (~3–4 h). The evolver is the hardest prompt to write because:
- It has to understand schema validation for its own proposals
- It has to reason about its own track record (previous proposals + realized outcomes)
- It has to discriminate between "numeric change" and "prompt change" for the backtest mode selection
- It has to propose small incremental changes without rewriting the config
- It has to cite specific evidence by trade ID

That's a full session of iteration. Re-estimate T3.5 as L.

**Action:** Update T3.5 effort to L. Adjust session 7 target accordingly.

### 2.7 No task for evolver rationale verification
The evolver cites trade IDs as evidence. If it hallucinates an ID, the citation is useless and we don't know. There should be a post-processing step that verifies every cited trade ID actually exists.

**Action:** Add to T6.2 acceptance: "After the evolver returns a proposal, verify every trade ID cited in the rationale against `positions_closed`. Any hallucinated ID flags the proposal as invalid and logs a schema_version bump request."

### 2.8 Phase 9 go-live assumes bootstrap completes before live starts — but live can start earlier
There's an argument for starting live research and decision passes *during* the bootstrap backtest, rather than after. Reasons to start live early:
- Bootstrap can take 1–3 hours
- Live passes don't depend on the LLM output cache being populated
- Earlier real-world feedback
- Faithfulness check starts accumulating earlier

Reasons to wait:
- The evolver's baseline metrics aren't ready
- Live trades before baseline exists have no comparison point

**Resolution:** Live can start after T9.2 (backfill done), not T9.3 (bootstrap done). Move T9.4 (manual dry run) before T9.3. Adjust the sequence.

**Action:** Reorder Phase 9:
- T9.1 smoke test
- T9.2 historical backfill
- T9.4 manual dry run (live mode, small scale)
- T9.5 enable schedules for live
- T9.3 bootstrap backtest (runs in parallel with live if needed)
- T9.6 first walk-forward evolver (after 4 weeks live + bootstrap done)
- T9.7 first faithfulness check (day 15)
- T9.8 log accomplishment

### 2.9 Data quality checks from T7.3 should also run on backtest bars
T7.3 is scoped to "every feature computation" but in practice backfilled bars are the ones most likely to have holes. The check should also run during the backfill script (T1.3a) and during bootstrap (T5.7) with failures aggregated into a "data quality report" rather than blocking the run.

**Action:** Update T7.3 scope: "runs on feature computation AND during bar backfill AND during backtest cursor advance. Failures are aggregated; a fatal threshold (e.g., >5% of bars flagged) aborts the backfill."

### 2.10 No task for strategy_version promotion audit
When a proposal moves from `pending/` to `active/`, that's a significant event. It should be logged with who approved it, when, and what the walk-forward metrics looked like. Currently there's an implicit "Claude moves the file" but no audit trail task.

**Action:** Add T6.2a "Strategy promotion audit" (S):
- Helper function `paper_trading/strategy_versions.py:promote(version, approver, walk_forward_result)`
- Writes to a new `strategy_promotions` table (small addition to T1.2 schema)
- Records: old_version, new_version, approver, ts, walk_forward_report_id, diff_summary
- Dan-via-Claude promotes through this helper, never by direct file copy

---

## 3. Nice-to-have (not blockers)

### 3.1 Backtest reproducibility — pin the engine version in the result
Backtest results should include the git commit SHA of the engine code so we can reproduce old runs later. Small addition to `backtest_runs.summary_json`.

### 3.2 Regime transitions are interesting but not tracked
Most per-regime attribution just bucketizes trades by regime at open. But the interesting question is: what happens when the regime changes mid-trade? That's where a lot of P&L variance lives. Adding `regime_at_open` + `regime_at_close` to `positions_closed` (already in v2) is the right first step; computing "regime_transition_penalty" is a future feature.

### 3.3 Walk-forward parallelism
Walk-forward runs are embarrassingly parallel across windows. For a 3-year weekly walk-forward with quarterly retrain, that's 12 windows. Running them serially is 12× slower than running them in a worker pool. Not critical for v1 but should be noted for v2.

### 3.4 Cost estimate dashboard in nightly report
Nightly report's cost summary is text. A running chart (text-based is fine) showing daily spend trend would catch anomalies faster than reading the number every night.

### 3.5 Bootstrap can be a multi-day operation
A $500–700 bootstrap run is probably going to want to pause and resume across multiple sessions (both to manage cost visibility and to let Dan sanity-check intermediate results). The current T5.7 supports resume-via-cache, but doesn't explicitly support "run for 2 hours, pause, review, resume." That would be a useful operator ergonomics improvement.

**Small action:** Add a `--max-runtime 2h` flag to `bootstrap.py` that gracefully halts after the specified runtime, preserving cache state.

---

## 4. Consistency issues

### 4.1 `ExecMode.LIVE` vs `run_id="live"` mismatch
Architecture says `run_id="live"` and `ExecMode.LIVE`. Both are independently correct but it's worth calling out that they're distinct concepts: ExecMode determines how data is fetched (fresh vs cached), run_id determines which ledger partition gets writes. A live cron run is `(LIVE, "live")`. A backtest run is `(BACKTEST_*, "bt_<uuid>")`. A developer test could in principle be `(BACKTEST_CHEAP, "live")` to simulate live mode on cached data, but this should never happen in production — add an assertion.

**Action:** Update T4.2 acceptance: "Engine asserts that `run_id=='live'` implies `mode==ExecMode.LIVE`, and vice versa. Any other combination raises `RuntimeError` unless an explicit `allow_mixed=True` flag is passed."

### 4.2 "Phase 4" (engine) in v2 conflicts with "Phase 4" (scripts) in v1
Not a real bug but future readers comparing versions will be confused. Add a note at the top of WORK_PLAN.md that v2 inserted a new Phase 4 and renumbered everything downstream.

**Action:** Add a "Changes from v1" section at the top of WORK_PLAN.md.

### 4.3 T1.7 regime classifier comes before T1.8 ledger but T1.8 doesn't need regimes
Dependency-wise fine. But in the session phasing, T1.7 and T1.8 are in the same session (session 3). Make sure T1.7 is built first since it has no downstream deps and T1.8 is the bigger task.

**Action:** No code change, just a note in the session description.

### 4.4 Appendix A.13 (cursor semantics) requires the engine to never look ahead
The engine's data loaders use `end_ts <= cursor.datetime`. But the regime classifier (T1.7) computes regimes at build time during backfill — it knows the full future. That's correct for the classifier itself (it's pure labeling), but the *consumers* of regime labels (decision agent, performance agent) must only see the regime for the cursor date, not ahead.

**Action:** Add to T1.7 acceptance: "Classifier writes regime labels keyed by date; consumers look up regime by `cursor.date` only. Add unit test: regime lookup at cursor 2024-06-14 returns the 2024-06-14 label, not 2024-06-15."

---

## 5. Effort re-estimation

Current plan estimate: ~80–95 hours across 12 sessions.

After folding in the additions from §1 and §2:

| Area                                      | Plan  | Revised |
|-------------------------------------------|-------|---------|
| Phase 0 (validation + env)                | 3 h   | 4 h     |
| Phase 1 (foundations)                     | 14 h  | 16 h    |
| Phase 2 (clients)                         | 5 h   | 5 h     |
| Phase 3 (prompts)                         | 12 h  | 14 h    |
| Phase 4 (engine unification)              | 8 h   | 10 h    |
| Phase 5 (backtest + walk-forward)         | 14 h  | 17 h    |
| Phase 6 (live scripts)                    | 8 h   | 9 h     |
| Phase 7 (integration/hardening)           | 4 h   | 4 h     |
| Phase 8 (deployment)                      | 4 h   | 4 h     |
| Phase 9 (go-live, Dan-supervised)         | 6 h   | 6 h     |
| **Total**                                 | **78 h** | **89 h** |

Plus the real-money bootstrap: ~$500–700 Anthropic spend during T9.3.

**Session phasing still works at 12 sessions** but several are now "tight fit" rather than comfortable. I'd expect Dan to want a 13th session as a buffer.

---

## 6. Summary of required changes to WORK_PLAN.md

Must-fix (before build):
1. Extend T0.2 + add T0.2a UW historical fallback
2. Add T4.2a engine parity test
3. Add T4.2b engine fixture prompts
4. Tighten T5.7 bootstrap acceptance (checkpoints, resumability, 90% soft cap)
5. Split T5.4 into T5.4a/b/c/d

Should-fix (strongly recommended):
6. Add VIX + SPY to ticker universe for regime classification (T1.3a + T1.7)
7. Add min-trades safety in T5.4b
8. Add T5.4e baseline result cache
9. Tighten T4.4 cache key to include prompt hash
10. Add T7.1a cost rollup view
11. Re-estimate T3.5 from M+ to L
12. Add trade ID verification to T6.2
13. Reorder Phase 9 so live can start before bootstrap completes
14. Extend T7.3 data quality to run during backfill and backtest
15. Add T6.2a strategy promotion audit
16. Add engine mode/run_id consistency assertion to T4.2
17. Add regime lookup scope unit test to T1.7

Nice-to-have (log for v2):
- Git SHA in backtest summary
- Walk-forward parallelism
- Cost trend chart in nightly report
- Bootstrap `--max-runtime` flag

---

## 7. Build-readiness gate

After folding the must-fix and should-fix items into WORK_PLAN.md, the plan is build-ready.

**My recommendation to Dan:** approve the updated architecture + work plan, and authorize session 1 to start with:
- Phase 0 (T0.0–T0.5): environment, API depth validation, deployment path decision
- Phase 1 prereqs (T1.0a/b/c): schemas, logging, test harness

That's a clean, low-risk first session. No external costs, no LLM spend, no irreversible actions. It unblocks everything else.

The first session with meaningful spending risk is session 12 (T9.3 bootstrap). Everything before that is cheap experimentation and can be paused or discarded without material cost.
