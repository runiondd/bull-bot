# Strategy Search Engine — Design Spec

**Date:** 2026-05-14
**Status:** awaiting Dan's spec review
**Author:** Mentor (autonomous), brainstormed with Dan
**Supersedes:** `.mentor/proposals/2026-05-14-strategy-throughput.md` (the earlier markdown proposal — kept for traceability)

---

## Summary

Replace the bot's current research loop (one LLM-proposed strategy tested per ticker per manual tick, ~23 strategies lifetime) with a continuous always-on search engine that produces ~1,000+ ranked strategies per day. The LLM stops doing parameter selection (which it is bad at) and only proposes strategy *classes* and *parameter ranges*. Python sweeps the ranges. A grid baseline runs alongside as a control group. All results feed one leaderboard ranked by capital-efficiency, gated by portfolio-level risk. The leaderboard's top entry, filtered to the current regime, is the bot's "deploy this" recommendation.

The system is built sequential-first; agent fan-out is a future wrapper around the same code.

---

## Goals

1. Test ≥ 1,000 strategies per day on a single laptop, ≥ 10,000 per day with agent fan-out later.
2. Rank every tested strategy on a single comparable metric: **annualized return on buying-power consumed**.
3. Enforce one risk rule that works for every strategy type: **no single trade's worst-case loss exceeds 2% of total portfolio value**.
4. Treat equity strategies (buy shares, with or without options overlay) as first-class — they share the same scoring, gating, and leaderboard with options strategies.
5. Learn class eligibility per market regime from data, starting day one. No hardcoded "use IronCondor in chop" tables.
6. Run continuously without human input — the mentor cron only verifies health, doesn't drive the loop.

## Non-Goals

- **Live execution.** This is a paper-search engine. Live trading is a separate spec.
- **Fixing the `_dispatch_paper_trial` bug.** Critical bug, but orthogonal — separate spec.
- **Agent fan-out (the "hundreds of agents" build).** Designed-for but built later. Sequential ships first.
- **Crypto data adapter.** Out of scope unless explicitly added.

---

## Architecture

Three engines feed one leaderboard. The leaderboard is read by the dashboard, the daily brief, and (in a later spec) the live-execution layer.

**Engine A — LLM Proposer (hourly during market hours).** Sequential, one ticker at a time. For each ticker, the bot reads the current regime (a tuple like `bull / low-IV / moderate-vol`), queries the eligibility module for a 4-class menu (3 top-performing classes for this regime + 1 explore slot), passes the menu + IV-rank distribution + regime context to the LLM, and receives one chosen class plus a *parameter spec with ranges* (e.g., `short_delta ∈ {0.15, 0.20, 0.25, 0.30}`). The LLM also returns `max_loss_per_trade` (always) and `stop_loss_pct` (for equity strategies).

**Engine B — Param Sweep (fires after every Engine A or C proposal).** Takes the parameter spec, walks the cartesian product up to a cell cap (default 200), runs each cell through the existing `walk_forward` engine. Cells run in parallel across CPU cores via `joblib.Parallel(n_jobs=-1)`. Each cell writes one row to `evolver_proposals` with `regime_label`, `score_a` (annualized return on BP held), `size_units` (how many contracts/shares the sizer says), `max_loss_per_trade`, and `passed_gate`. A crashing cell logs to `sweep_failures` and does not halt the other cells.

**Engine C — Grid Baseline (weekly, Sundays off-market).** Ignores the LLM entirely. Enumerates a fixed grid: every strategy class × every ticker × every reasonable parameter cell (~9,600 cells). Feeds straight into Engine B. Rows tagged `proposer_model = 'grid:baseline'`. This is the honesty check — if the LLM-proposed search doesn't beat the grid baseline on gate-pass rate over a 4-week window, the mentor brief escalates a flag for human decision.

**Leaderboard.** A SQL view over `evolver_proposals` + `strategies` + `regime_briefs`. Computes the score-A, applies the gate-B and trade-count floor, returns a ranked list. The dashboard, the brief, and the eligibility module all read from this view.

**Sequential first, fan-out later.** The dispatcher inside Engine A is written as a function that takes a *list* of `(ticker, class)` slots; it just happens to be called with one slot at a time today. When agent fan-out lands, the same function is called with all 96 active slots and an internal dispatcher spawns one subagent per slot in parallel.

---

## Components

| Module | New / Modify | Responsibility |
|---|---|---|
| `bullbot.regime.eligibility` | NEW | Given a ticker + current regime, return the LLM's 4-class menu (top 3 by historical score-A in this regime + 1 underexplored explore slot). Reads live from the leaderboard view. Implements cold-start logic: when a `(regime, class)` cell has < 5 observations, treat it as "unknown" and force-include it. |
| `bullbot.leaderboard` | NEW | SQL view + Python query layer. Computes score-A, applies gate-B and trade-count floor, returns ranked list. Exposes `top_n(regime=None, ticker=None, class_name=None)` for the dashboard and brief. |
| `bullbot.evolver.sweep` | NEW | Engine B. Takes a strategy spec with parameter ranges, expands to cells (capped at 200), runs each through `walk_forward.run()` via `joblib.Parallel`, writes one row per cell. Per-cell error isolation. |
| `bullbot.evolver.proposer` | MODIFY | Existing module. Changes: prompt now includes regime-aware menu + ticker IV-rank percentiles; response JSON schema asks for *ranges* not points; response includes `max_loss_per_trade` and `stop_loss_pct` (latter for equity). |
| `bullbot.risk.sizing` | NEW | Replaces ad-hoc sizing in `engine/`. Given a strategy + portfolio value + 2% per-trade cap, returns `size_units` (contracts or shares). Single source of truth used by sweeper, dashboard, and (later) live execution. |
| `scripts.grid_baseline` | NEW | Engine C runner. Iterates the fixed grid, calls `sweep`, tags rows. Runs weekly on Sundays via cron. |
| `scripts.run_continuous` | NEW (replaces `run_one_tick`) | The hourly daemon. Loops every 60 minutes during market hours, calls one Engine A round, writes heartbeat, sleeps. |
| `bullbot.dashboard` | MODIFY | Add leaderboard tab. Auto-refresh every 60 seconds. Prominent "last updated" timestamp. Three new status tiles: daemon heartbeat, today's LLM cost vs. cap, sweep success rate. |
| `evolver_proposals` schema | MODIFY | Add columns: `regime_label TEXT`, `score_a REAL`, `size_units INTEGER`, `max_loss_per_trade REAL`. Backfill historical rows from `regime_briefs` join where possible; default NULL where not. |
| `sweep_failures` table | NEW | One row per crashed cell: `(id, ts, ticker, class, cell_params_json, exc_type, exc_message, traceback)`. Read by the daily brief. |

---

## Data Flow

**Continuous (hourly during market hours, 09:35–16:00 ET, M–F):**

1. **Refresh inputs.** Polygon bar refresh (idempotent — fetches only what's missing). Regime briefs recomputed if stale (once per day per ticker; cached).
2. **For each ticker in UNIVERSE, weighted by bandit + `TICKER_PRIORITY_WEIGHT`:**
   - Read regime label.
   - Query eligibility module → 4-class menu.
   - Call LLM proposer → one class + parameter ranges + risk fields.
   - Hand spec to sweep → 100–200 cells, parallel.
   - Each cell: sizer computes `size_units`; row written to `evolver_proposals` with score-A, regime label, all metrics.
   - Update `ticker_state` (iteration_count, plateau_counter, best_score_a, best_strategy_id).
3. **After the round:** write timestamp to `cache/last_continuous_run.txt` (heartbeat).

**Weekly (Sundays, off-market):**

- Engine C: enumerate the grid, call sweep, tag rows `proposer_model='grid:baseline'`. Skip LLM entirely.

**Read path:**

- **Dashboard:** new leaderboard tab queries the view, sortable by score-A, regime, class, ticker, trade count. Auto-refreshes every 60 seconds.
- **Daily mentor brief:** reads top-10 lifetime + top-N from the last 24 hours; renders in "Strategies considered (lifetime trail)" and "Strategies investigated this run" sections (per the brief format finalized 2026-05-14).
- **Live execution (future, separate spec):** reads top-1 per ticker, sizes via `risk.sizing`, places paper orders.

---

## Scoring, Risk, and Sizing

**Score-A (the only rank):** `(realized_pnl / max_buying_power_held_during_trade) × (365 / days_held)`. Annualized so a 30-day options trade and a 2-year equity position are directly comparable.

**Gate-B (the only filter):** No proposed strategy is sized such that its worst-case single-trade loss exceeds 2% of total portfolio value. Default `MAX_LOSS_PCT_OF_PORTFOLIO = 0.02`, tunable in `config.py`. On a $265k portfolio that's $5,300 max loss per trade.

**Equity handling:** Equity strategies must propose a `stop_loss_pct`. The sizer treats `stop_loss_pct × spot × shares` as the worst-case loss. A strategy with no stop-loss is technically legal but gets sized so small (100% theoretical loss = $5,300 position) that it's effectively benched.

**Trade-count floor:** `EDGE_TRADE_COUNT_MIN ≥ 5` preserved from existing config. A strategy with 4 trades is not eligible for the leaderboard's top regardless of score-A.

**Why this combination:** Score-A optimizes for capital efficiency. Gate-B caps absolute downside. Trade-count floor caps statistical noise. The three together prevent the bot from recommending "this strategy earned 200% on its 2 trades and would have lost the account on its 3rd."

---

## Eligibility and Priority — the bandit layer

**Class eligibility (per regime).** For each `(regime, class)` cell, the eligibility module computes historical score-A from the leaderboard and ranks. Cold-start rule: cells with < 5 observations are treated as "unknown" and force-included. As data accumulates, the menu naturally tightens. Decay: exponential, ~6-month half-life, so the bot forgets stale regime behavior.

**Ticker priority.** Same bandit logic, extended one level: `(ticker × regime × class)` cells are weighted by score-A and underexploration. Tickers whose top strategies are producing high score-A get more LLM calls. Tickers with thin data (VCX at 39 bars today) get zero weight until they have enough bars for the walk-forward.

**User override.** `TICKER_PRIORITY_WEIGHT` config dict, default `{'<ticker>': 1.0}` for all. Dan can bump META to `2.0` to double its sampling rate, or set HYG to `0.0` to bench it without removing from UNIVERSE. The bandit can still override if the data screams loud enough (a known-loser pinned to 2.0 still loses weight if it keeps failing the gate).

**Algorithm choice.** Thompson sampling is the recommended algorithm — handles sparse data gracefully and doesn't need a hand-tuned epsilon. Final algorithm choice can be revisited at plan time; the eligibility module's interface (`top_n(ticker, regime) → list[ClassMenuEntry]`) doesn't depend on the algorithm.

---

## Error Handling

**LLM failures** — JSON parse, API timeout, cost cap hit. Proposer retries once with stricter prompt; if still failing, logs an `iteration_failure` row and moves to next ticker. `MAX_LLM_USD_PER_DAY = 5.00` (configurable) caps total daily spend; when hit, Engine A suspends for the day, Engine C continues (zero-LLM-cost).

**Sweep failures** — one cell crashes. Per-cell try/except; the failed cell writes to `sweep_failures`, the rest of the sweep continues. Daily brief surfaces `sweep_failures` count.

**Data freshness** — Polygon down, stale bars, insufficient history. Sweep refuses to run on tickers whose latest bar is > 3 trading days old. Tickers with < 504 bars are filtered out (bandit weight = 0). Daily Polygon health-check (the existing 12/12 validator) gates the daemon — if probes < 11/12, daemon refuses to start that day.

**Daemon failures** — process death, OOM, machine reboot. Heartbeat file checked by mentor cron at 07:30 ET; if stale > 12 hours during a weekday, cron auto-restarts. Restart back-off: max 3 restarts/hour to prevent storms. Logs to `logs/continuous-daemon.log` with full tracebacks.

**Stuck-class circuit breaker** — a `(ticker, class)` cell that fails the gate ≥ 10 consecutive iterations gets bandit weight zero until either (a) the regime changes meaningfully or (b) the weekly grid baseline produces a different result for that cell.

**Recommendation drift** — top-ranked strategy's trailing-7-day score-A drops below #2's. Brief emits "recommendation rotation suggested" flag. No auto-action; human review.

---

## Observability

- **Daily brief** (existing, augmented): adds sweep_failures count, daemon heartbeat status, LLM cost vs. cap, top-10 leaderboard, recommendation rotation flags.
- **Dashboard** (modified): leaderboard tab, auto-refresh 60s, last-updated timestamp, daemon/cost/sweep-success tiles.
- **Logs:** `logs/continuous-daemon.log` (per-round), `logs/sweep_failures.log` (per failed cell), `cache/last_continuous_run.txt` (heartbeat). Rotated weekly.

---

## Testing Strategy

**Layer 1 — Unit tests per module, written first (TDD).** Eligibility, sweep, leaderboard, risk.sizing each ship with comprehensive coverage including cold-start, error paths, and edge cases.

**Layer 2 — Integration test end-to-end with a fake LLM.** Drives the full Engine A → sweep → leaderboard pipeline with a deterministic LLM fake. Catches regressions across the whole loop.

**Layer 3 — Regression tests for known bugs.** Every previously-fixed bug becomes a permanent test: cagr-complex, Haiku-JSON-fence, plus new ones: sweep cell isolation, cost-cap honored mid-round, stale heartbeat triggers restart, backtest determinism (same inputs → byte-identical metrics).

**Layer 4 — Automated A/B between Engine A and Engine C.** Weekly: gate-pass rate of LLM proposer vs. grid baseline over same regime cells. If LLM ≤ grid by > 20% for 4 consecutive weeks, brief escalates "consider killing the LLM proposer."

**Pre-merge gate** (enforced by mentor cron): 12/12 polygon validator, all unit + integration tests pass, sweep_failures count from last 24h not increased, yesterday's LLM spend inside cap. Any failure blocks merge to main.

---

## Open Questions (resolve at plan time, not blocking spec approval)

1. **Bandit algorithm.** Recommended: Thompson sampling. Final choice deferred to plan.
2. **Regime label discretization.** We've said the cell is `(direction × vol-regime × iv-band)`. Need to lock the bins — e.g., is IV-rank "low" `<25` or `<30`? Final bins deferred to plan; calibration uses 5-year history of the existing `regime_briefs` table.
3. **Daemon hosting.** Today: assumed to run on Dan's laptop. If laptop sleeps, the daemon sleeps. Migration to a $5/month VPS is a one-pager — out of scope here, separate operational decision.
4. **`evolver_proposals` backfill.** Adding `regime_label` and `score_a` columns means historical rows need backfill via timestamp-join to `regime_briefs`. Most rows backfill cleanly; rows older than the regime_briefs table get NULL. Acceptable.
5. **Engine A → Engine B handoff.** Sync or async? Sync is simpler (LLM call blocks until sweep finishes); async lets multiple sweeps run concurrently. Recommend sync for v1; async optional later.

## Out of Scope (explicit, do not pull into this spec)

- Live trading execution
- Fixing the `_dispatch_paper_trial` bug (META/SPY/TSLA paper dispatch)
- Agent fan-out implementation (Engine A as parallel subagent fleet)
- Crypto data adapter
- New strategy classes beyond what's already in `bullbot.strategies.registry`
- Daemon migration off Dan's laptop
- Trading-hours / market-holiday handling beyond "skip weekends" (existing `bullbot.clock` is sufficient)

---

## Decision Log (what got debated and locked in)

- **Score:** `realized_pnl / max_bp_held`, annualized. (vs. risk-adjusted Sharpe, vs. max-loss-based — annualized BP-return won because it's the binding-constraint metric.)
- **Risk gate:** portfolio-level 2% max-loss-per-trade. (vs. per-strategy-class 60% gate I originally proposed — Dan's framing won, it's strategy-agnostic and forces equity to declare stop-losses.)
- **Eligibility:** learned from leaderboard day one. (vs. hardcoded table — Dan insisted, accepted; cold-start logic added to handle sparse-data bootstrap.)
- **Ticker priority:** bandit-driven + `TICKER_PRIORITY_WEIGHT` override. (vs. pure data-driven — Dan picked B for the option to express opinions.)
- **Fan-out timing:** sequential first, fan-out later. (Dan initially pushed for "now," accepted the debugging argument for sequential-first.)
- **Equity status:** first-class. (Triggered by Dan flagging buy-the-shares mid-architecture.)
