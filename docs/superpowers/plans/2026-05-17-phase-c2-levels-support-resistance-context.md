# Bull-Bot v2 Phase C.2 — Context for External Reviewer

This document gives an outside reviewer (Grok, a human consultant, or another model) the project background needed to review the C.2 levels-module implementation plan critically.

## 1. What is Bull-Bot

Bull-Bot is a personal automated trading research project. It is built and operated by one person (Dan), runs on a single Mac mini ("pasture") via launchd, paper-trades a fixed universe of US equity tickers, and maintains a SQLite database (`cache/bullbot.db`) as its single source of truth.

The bot is a learning project, not a commercial product. There is no broker integration. All trades are simulated against Yahoo Finance bar and chain data. The goal is to develop trading judgment that could later be deployed with real capital, and to learn AI engineering by building agentic systems against a domain Dan cares about (markets).

Dan is a Product Manager by background, not a backend engineer. The bot is asked to communicate state in plain language ("we made $X today on AAPL"), and to make autonomous strategy/parameter decisions itself rather than asking the operator to pick deltas, DTEs, vehicles, or sizing — these are explicitly the bot's job to discover.

## 2. Where Phase C stands today

The Phase C design + Grok review response are committed at:
- `docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md`
- `docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md`
- `docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-context.md`

Phase C is broken into 7 sub-steps:
- **C.0 — Schema + positions.py + risk.py.** **MERGED** in PR [bull-bot#1](https://github.com/runiondd/bull-bot/pull/1) (2026-05-16).
- **C.1 — Chains module (Yahoo + BS).** **MERGED** in PR [bull-bot#3](https://github.com/runiondd/bull-bot/pull/3) (2026-05-17, formerly stacked on #1 as #2).
- **C.2 — Support/resistance module (`levels.py`).** **THIS PLAN.**
- C.3 — Earnings + vehicle agent (LLM) + exits.
- C.4 — Backtest harness (including event-day IV bump per Grok Tier 1 Finding 3).
- C.5 — Forward runner + dashboard tabs.
- C.6 — Ship to pasture + verify live.

## 3. What the C.2 plan delivers

The plan ships `bullbot/v2/levels.py` — a pure-function support/resistance calculator. Single public entry point:

`compute_sr(bars: list, lookback: int = 60) -> list[Level]`

Where `Level(price, kind, strength)` and `kind ∈ {swing_high, swing_low, sma_20, sma_50, sma_200, round_number}`, `strength ∈ [0.0, 1.0]`.

Pipeline:
1. **Swing extrema** with 3-bar confirmation on each side. Strength scales with touch count (how many bars sit within 0.5% of the swing).
2. **SMA values** at 20 / 50 / 200 windows. Strength scales with window length (200 > 50 > 20).
3. **Round-number snaps** within 2% of spot. Step size scales with spot magnitude ($1 / $5 / $10 / $50 across price tiers). Fixed strength = 0.3.
4. **Dedup** within 0.5% — clusters collapse to the level with highest strength (ties broken by kind priority: swing > sma_200 > sma_50 > sma_20 > round_number).
5. **Sort** by absolute distance to the most recent close.

Key design choices baked into the plan:
- **Stdlib only.** No NumPy, no pandas, no third-party libraries.
- **Pure function.** No DB reads, no I/O, no LLM. Bars are passed in.
- **Duck-typed bars.** Same SimpleNamespace shape (`.high`, `.low`, `.close`) the rest of the v2 codebase uses.
- **No persistence.** S/R levels are computed on demand. If C.5 dashboard wants to display them, that's a C.5 decision.
- **Strength is heuristic, not statistical.** The 0–1 scale is for the LLM's interpretability. Not over-engineered — C.3's prompt design will reveal what actually matters and can drive refinement.

## 4. What the plan does NOT touch

- `bullbot/db/migrations.py` — no schema changes.
- `bullbot/v2/positions.py`, `risk.py`, `chains.py` — finalized in C.0 / C.1.
- LLM / vehicle agent — C.3 scope.
- Backtest synth_chain (with event-day bump) — C.4 scope.
- Forward MtM loop, dashboard tabs — C.5 scope.

## 5. Plan structure

The plan follows the same TDD pattern as the C.0 and C.1 plans that have already shipped:

7 tasks. Each task = (failing test → run to see failure → minimal implementation → run to see pass → commit). Each task adds 5–7 new unit tests. Tasks are sized so a focused subagent session can complete one end-to-end.

The plan was written using the Superpowers `writing-plans` skill (same skill that produced C.0 and C.1 plans), which mandates:
- Exact file paths
- Complete code in every step (no placeholders / TBDs)
- Exact pytest commands with expected output
- TDD discipline (test-first, never skip the failing-test verification)
- One commit per task

## 6. Conventions specific to this codebase that may be relevant

- Tests live under `tests/unit/` and `tests/integration/`. The conftest auto-adds repo root to `sys.path`.
- Use `/Users/danield.runion/Projects/bull-bot/.venv/bin/python` as the runner; `.venv` lives at the main repo, not in the worktree.
- The existing v2 codebase pattern is: small single-responsibility modules (~75–200 LOC each), no inheritance, dataclasses for state, plain functions for behavior.
- For S/R specifically: trader convention uses N-bar confirmation (typically N=3) for swing detection and 20/50/200 as the canonical SMA windows. These constants are intentionally locked, not configurable in C.2 — if backtest reveals different windows matter, that's a C.4 tuning loop.

## 7. Dan's stated preferences (relevant to plan review)

- The bot picks vehicles/sizing/strikes/strategy autonomously — the plan does not expose tunable parameters to the operator.
- The S/R strengths and thresholds (touch count, dedup 0.5%, round-number 2%) are opinionated heuristics — Grok's review should challenge them with reasoning, not propose making them all configurable.
- MSTR/IBIT thesis means S/R may need to handle very wide ranges (e.g., $400 spot, $50 round-number steps); the plan's step-size table covers this.
