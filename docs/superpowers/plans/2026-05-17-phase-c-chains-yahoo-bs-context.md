# Bull-Bot v2 Phase C.1 — Context for External Reviewer

This document gives an outside reviewer (Grok, a human consultant, or another model) the project background needed to review the C.1 chains-module implementation plan critically.

## 1. What is Bull-Bot

Bull-Bot is a personal automated trading research project. It is built and operated by one person (Dan), runs on a single Mac mini ("pasture") via launchd, paper-trades a fixed universe of US equity tickers, and maintains a SQLite database (`cache/bullbot.db`) as its single source of truth.

The bot is a learning project, not a commercial product. There is no broker integration. All trades are simulated against Yahoo Finance bar and chain data. The goal is to develop trading judgment that could later be deployed with real capital, and to learn AI engineering by building agentic systems against a domain Dan cares about (markets).

Dan is a Product Manager by background, not a backend engineer. The bot is asked to communicate state in plain language ("we made $X today on AAPL"), and to make autonomous strategy/parameter decisions itself rather than asking the operator to pick deltas, DTEs, vehicles, or sizing — these are explicitly the bot's job to discover.

## 2. Where Phase C stands today

The Phase C design + Grok review response are committed at:
- `docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md` (full design, post-Grok-review)
- `docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md` (all 4 Tier 1 findings + Tier 2 improvements integrated)
- `docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-context.md` (project background)

Phase C is broken into 7 sub-steps:
- **C.0 — Schema + positions.py + risk.py.** **SHIPPED** in PR [bull-bot#1](https://github.com/runiondd/bull-bot/pull/1) (2026-05-16). 10 commits. All 547 unit tests pass. Includes the `v2_chain_snapshots` schema table that this plan writes into.
- **C.1 — Chains module (Yahoo + BS).** **THIS PLAN.**
- C.2 — Support/resistance module (`levels.py`).
- C.3 — Earnings + vehicle agent (LLM) + exits.
- C.4 — Backtest harness (including event-day IV bump per Grok Tier 1 Finding 3).
- C.5 — Forward runner + dashboard tabs.
- C.6 — Ship to pasture + verify live.

## 3. What the C.1 plan delivers

The plan ships `bullbot/v2/chains.py` — the live option-pricing layer for Phase C. Two public entry points:

1. `fetch_chain(conn, ticker, asof_ts, client=None) -> Chain | None` — pulls a Yahoo option chain, persists rows into `v2_chain_snapshots`, and returns the assembled `Chain` (or `None` on any failure).
2. `price_leg(conn, ticker, leg, spot, today, asof_ts) -> tuple[float, str]` — returns `(per-share mid price, source)` where `source ∈ {'yahoo', 'bs'}`. Tries the cached Yahoo snapshot first, then falls back to Black-Scholes using the snapshot's IV if present, else an IV proxy.

Key design choices baked into the plan:
- **BS math is reused from `bullbot.data.synthetic_chain.bs_price` and `realized_vol`** — no duplicate pricer.
- **IV proxy formula:** `realized_vol_30(underlying) × (vix_today / median(vix_last_60))`, clamped to `[0.05, 3.0]`. Falls back to defaults when bar series too short.
- **Yahoo client is injected** as an optional callable parameter (default lazily imports `yfinance.Ticker`) so tests don't need real network calls.
- **Atomic persistence:** on any partial failure mid-fetch, NO rows are written. All-or-nothing.
- **Event-day IV bump is explicitly NOT in this module** — it lives in C.4's `backtest/synth_chain.py` because it's a backtest-only correction. Forward mode uses raw proxy.

## 4. What the plan does NOT touch

- **`bullbot/db/migrations.py`** — schema for `v2_chain_snapshots` already landed in C.0 Task 1.
- **`positions.py`, `risk.py`** — finalized in C.0.
- **LLM / vehicle agent** — C.3 scope.
- **Backtest synth_chain (with event-day bump)** — C.4 scope.
- **Forward MtM loop, dashboard tabs** — C.5 scope.

## 5. Plan structure

The plan follows the same TDD pattern as the C.0 plan that just shipped:

8 tasks, each task = (failing test → run to see failure → minimal implementation → run to see pass → commit). Each task adds 3–7 new unit tests. Tasks are sized so a focused subagent session can complete one end-to-end.

The plan was written using the Superpowers `writing-plans` skill, which mandates:
- Exact file paths
- Complete code in every step (no placeholders / TBDs)
- Exact pytest commands with expected output
- TDD discipline (test-first, never skip the failing-test verification)
- One commit per task

## 6. Conventions specific to this codebase that may be relevant

- Tests live under `tests/unit/` and `tests/integration/`. The conftest auto-adds repo root to `sys.path`.
- Use `/Users/danield.runion/Projects/bull-bot/.venv/bin/python` as the runner; `.venv` lives at the main repo, not in the worktree.
- The `bars` SQLite table holds OHLCV history keyed `(ticker, timeframe, ts)` with `ticker='VIX', timeframe='1d'` being the volatility-index history.
- The existing v2 codebase pattern is: small single-responsibility modules (~75–150 LOC each), no inheritance, dataclasses for state, plain functions for behavior, dependency injection for I/O (Yahoo, LLM, etc).
- Risk-free rate convention: 4.5% (matches the v1 synthetic chain default).

## 7. Dan's stated preferences (relevant to plan review)

- The bot picks vehicles/sizing/strikes autonomously — don't propose forcing operator interaction.
- Risk caps are temporary and will expand — don't hardcode "$1k max loss" into business logic.
- MSTR/IBIT LEAPS thesis means deep-ITM long calls are first-class instruments, not edge cases.
- Backtest discipline matters: a plan that introduces new trading logic must include tests that would catch the trading bugs, not just the mechanical ones.
