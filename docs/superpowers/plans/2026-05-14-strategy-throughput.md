# Strategy Throughput Implementation Plan — SUPERSEDED

> **SUPERSEDED 2026-05-14:** This file was a premature plan written before the Superpowers brainstorming cycle landed the design. The real, approved plan is at `docs/superpowers/plans/2026-05-14-strategy-search-implementation.md`, derived from `docs/superpowers/specs/2026-05-14-strategy-search-design.md`. Use that file. This one is kept for traceability only.

---

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the bot from ~1 strategy tested per day to 1,000+ per day (single-machine) and 10,000+ per day (agent-fan-out), so the testing-and-learning trail produces enough proposals to find a real edge in weeks instead of years.

**Architecture:** Three layers stacked on top of the existing `evolver.iteration` loop. (1) `ParamSweeper` turns one LLM proposal into a deterministic grid of N backtests. (2) `ProcessPool` runs that grid in parallel across CPU cores. (3) Agent fan-out, where each subagent owns a ticker × strategy-class × regime slice and runs its own Phases 1–2 independently. The LLM stops doing parameter selection (which it's bad at) and only does class + regime-informed range selection (which it's good at).

**Tech Stack:** Python 3.10, pandas, joblib (new dep) for ProcessPool, existing `walk_forward` engine, Anthropic SDK, SQLite, Claude Agent SDK for Phase 4.

---

## Phase 0 — Grid baseline (1 day, ~$0 LLM)

Sanity-check that a pure grid search can find gate-passers without the LLM at all. This is the validation baseline for everything else: if a 200-cell grid finds 0 gate-passers, the gate is wrong, not the proposer.

### Task 0.1: Write `scripts/grid_baseline.py`

**Files:**
- Create: `scripts/grid_baseline.py`
- Test: `tests/unit/test_grid_baseline.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_grid_baseline.py
from scripts.grid_baseline import build_grid

def test_grid_covers_known_param_space():
    grid = build_grid(
        klass="PutCreditSpread",
        deltas=[0.15, 0.20, 0.25, 0.30],
        widths=[5, 10],
        dtes=[21, 30, 45],
        ivr_floors=[5, 15, 25],
        pts=[0.5],
        sls=[2.0],
    )
    assert len(grid) == 4 * 2 * 3 * 3 * 1 * 1  # 72 cells
    cell = grid[0]
    assert cell["class"] == "PutCreditSpread"
    assert "short_delta" in cell
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_grid_baseline.py::test_grid_covers_known_param_space -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'scripts.grid_baseline'"

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/grid_baseline.py
from itertools import product
from typing import Iterable

def build_grid(klass: str, deltas: Iterable[float], widths: Iterable[int],
               dtes: Iterable[int], ivr_floors: Iterable[float],
               pts: Iterable[float], sls: Iterable[float]) -> list[dict]:
    return [
        {
            "class": klass,
            "short_delta": d,
            "width": w,
            "dte": dte,
            "iv_rank_min": ivr,
            "profit_target_pct": pt,
            "stop_loss_mult": sl,
        }
        for d, w, dte, ivr, pt, sl in product(deltas, widths, dtes, ivr_floors, pts, sls)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_grid_baseline.py::test_grid_covers_known_param_space -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/grid_baseline.py tests/unit/test_grid_baseline.py
git commit -m "feat: grid_baseline.build_grid for deterministic param sweep"
```

### Task 0.2: Wire grid_baseline into existing `walk_forward.run()`

**Files:**
- Modify: `scripts/grid_baseline.py`
- Test: `tests/unit/test_grid_baseline.py`

- [ ] **Step 1: Write the failing test**

```python
def test_run_grid_writes_proposals_to_db(tmp_path):
    from scripts.grid_baseline import run_grid
    from bullbot.db import schema, connection
    db_path = tmp_path / "test.db"
    conn = connection.open_persistent_connection(str(db_path))
    schema.create_all(conn)
    # seed minimal ticker_state + bars for AAPL
    # ...
    n_written = run_grid(conn, ticker="AAPL", klass="PutCreditSpread", n_cells=4)
    assert n_written == 4
    rows = list(conn.execute(
        "SELECT COUNT(*) FROM evolver_proposals WHERE ticker='AAPL' "
        "AND created_at > strftime('%s','now') - 60"
    ))
    assert rows[0][0] == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_grid_baseline.py::test_run_grid_writes_proposals_to_db -v`
Expected: FAIL with "AttributeError: module has no attribute 'run_grid'"

- [ ] **Step 3: Write minimal implementation**

```python
# add to scripts/grid_baseline.py
from bullbot import config
from bullbot.evolver import walk_forward
from bullbot.strategies import registry

def run_grid(conn, ticker: str, klass: str, n_cells: int = 72) -> int:
    grid = build_grid(
        klass=klass,
        deltas=[0.15, 0.20, 0.25, 0.30],
        widths=[5, 10],
        dtes=[21, 30, 45],
        ivr_floors=[5, 15, 25],
        pts=[0.5],
        sls=[2.0],
    )[:n_cells]
    written = 0
    for params in grid:
        strat = registry.build(params["class"], params)
        metrics = walk_forward.run(conn, ticker=ticker, strategy=strat,
                                    run_id=f"grid:baseline:{params['class']}")
        conn.execute(
            "INSERT INTO evolver_proposals (ticker, iteration, strategy_id, "
            "rationale, llm_cost_usd, pf_is, pf_oos, sharpe_is, max_dd_pct, "
            "trade_count, regime_breakdown, passed_gate, created_at, proposer_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "strftime('%s','now'), ?)",
            (ticker, 0, strat.id, f"grid baseline cell={params}", 0.0,
             metrics.pf_is, metrics.pf_oos, metrics.sharpe_is, metrics.max_dd_pct,
             metrics.trade_count, metrics.regime_breakdown, int(metrics.passed_gate),
             "grid:baseline"),
        )
        written += 1
    conn.commit()
    return written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_grid_baseline.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/grid_baseline.py tests/unit/test_grid_baseline.py
git commit -m "feat: grid_baseline.run_grid writes one row per param cell"
```

### Task 0.3: CLI entry + ad-hoc run against AAPL/META/NVDA

**Files:**
- Modify: `scripts/grid_baseline.py` (add `__main__` block)

- [ ] **Step 1: Add CLI**

```python
# bottom of scripts/grid_baseline.py
if __name__ == "__main__":
    import argparse, sys
    from bullbot import config
    from bullbot.db import connection
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--classes", nargs="+",
                        default=["PutCreditSpread", "IronCondor", "CallCreditSpread"])
    parser.add_argument("--cells", type=int, default=72)
    args = parser.parse_args()
    conn = connection.open_persistent_connection(config.DB_PATH)
    total = 0
    for t in args.tickers:
        for k in args.classes:
            total += run_grid(conn, ticker=t, klass=k, n_cells=args.cells)
    print(f"wrote {total} grid baseline rows")
```

- [ ] **Step 2: Run end-to-end**

Run: `python scripts/grid_baseline.py AAPL META NVDA --cells 36`
Expected: `wrote 324 grid baseline rows` (3 tickers × 3 classes × 36 cells), runtime ~10-15 min.

- [ ] **Step 3: Inspect — did the grid find anything the LLM missed?**

Run:
```sql
SELECT ticker, strategy_id, pf_is, pf_oos, trade_count
FROM evolver_proposals
WHERE proposer_model = 'grid:baseline' AND passed_gate = 1
ORDER BY pf_oos DESC LIMIT 20;
```

Expected: 0–20 rows. If 0, the gate is wrong. If >5, the grid alone is competitive with the LLM.

- [ ] **Step 4: Commit**

```bash
git add scripts/grid_baseline.py
git commit -m "feat: grid_baseline CLI; ad-hoc runs against discovering tickers"
```

---

## Phase 1 — ParamSweeper layer (3 days)

Make the LLM propose *ranges* instead of *points*; have Python walk the cartesian product.

### Task 1.1: New proposer JSON schema

**Files:**
- Modify: `bullbot/evolver/proposer.py` — change the JSON schema in the system prompt and the response parser.
- Test: `tests/unit/test_proposer_schema.py`

- [ ] **Step 1: Write failing test**

```python
def test_proposer_returns_ranges_not_points():
    payload = {
        "class": "PutCreditSpread",
        "rationale": "META bull regime",
        "ranges": {
            "short_delta": [0.20, 0.25, 0.30],
            "width": [5, 10],
            "dte": [21, 30, 45],
            "iv_rank_min": [10, 20, 30, 40],
            "profit_target_pct": [0.5],
            "stop_loss_mult": [2.0]
        }
    }
    spec = parse_proposer_response(payload)
    assert spec.class_name == "PutCreditSpread"
    assert spec.ranges["short_delta"] == [0.20, 0.25, 0.30]
    assert spec.cell_count() == 3 * 2 * 3 * 4 * 1 * 1  # 72
```

- [ ] **Step 2-5:** Standard TDD cycle. Add `parse_proposer_response`, `StrategySpec` dataclass, `cell_count()`. Commit.

### Task 1.2: New `bullbot.evolver.sweep` module

**Files:**
- Create: `bullbot/evolver/sweep.py`
- Test: `tests/unit/test_sweep.py`

Module exposes `sweep(conn, ticker, spec, n_cells_max=200) → list[Metrics]`. Internally iterates the spec's cartesian product (up to `n_cells_max`), calls existing `walk_forward.run()` for each, writes one `evolver_proposals` row per cell with `proposer_model=f'sweep:{base_model}'`.

(Full task breakdown below in Phase 1 detail — see appendix A.)

### Task 1.3: Inject IV-rank floor from the ticker's actual distribution

**Files:**
- Modify: `bullbot/evolver/proposer.py` — system prompt now gets `iv_rank_history[ticker]` for the past 252 bars, computes percentiles, sends them to the LLM so it stops proposing floors above the ticker's actual top decile.

### Task 1.4: Wire ParamSweeper into `scheduler.tick()`

**Files:**
- Modify: `bullbot/scheduler.py:tick()`
- Test: `tests/integration/test_scheduler_sweep.py`

Replace single-call evolver iteration with: (a) LLM proposes spec, (b) sweep runs grid, (c) best cell is the "candidate" judged against the gate, (d) all cells are persisted for the trail. Gate logic unchanged.

### Task 1.5: Update brief to show sweep coverage

**Files:**
- Modify: `.mentor/DAILY_PROMPT.md` Step 7 — "Strategies considered (lifetime trail)" section now reports `proposals_per_class × cells_per_proposal` so the daily volume is visible.

---

## Phase 2 — ProcessPool parallelism (1 day)

Sweep cells are independent → run them in `joblib.Parallel(n_jobs=-1)`. On an 8-core M-series Mac this is ~6-7× wall-clock speedup.

### Task 2.1: Add joblib dep + wrap `sweep` in Parallel

**Files:**
- Modify: `requirements.txt` (add `joblib>=1.3`)
- Modify: `bullbot/evolver/sweep.py:sweep()`

### Task 2.2: Backtest determinism guarantee

**Files:**
- Modify: `bullbot/evolver/walk_forward.py`

`walk_forward.run()` must be functionally pure given (bars, strategy, run_id) so parallel workers don't race on shared state. Add a regression test that runs the same cell twice and asserts byte-identical metrics.

---

## Phase 3 — Batched LLM proposals (2 days)

Per `docs/superpowers/specs/2026-04-27-agentic-throughput-design.md` Phase 3 — already designed.

### Task 3.1: LLM returns `{"proposals": [{class, ranges, rationale}, ...]}` with up to 5 specs per call

**Files:**
- Modify: `bullbot/evolver/proposer.py` system prompt + parser.

### Task 3.2: Per-spec sweep is run; gate evaluated against the best cell of each spec

**Files:**
- Modify: `bullbot/scheduler.py:tick()`.

---

## Phase 4 — Agent fan-out (3 days, the killer move)

This is Dan's "hundreds of agents" point. The Claude Agent SDK supports subagent dispatch. Each subagent owns a `(ticker, class, regime)` triple and runs its own Phases 1–2 independently, then reports back.

**Math:** 12 tickers × 4 classes × 3 regimes = **144 agent slots**. Each agent runs a 72-cell sweep = 10,368 backtests per cycle. 4 cycles/day = **41,472 backtests/day**. LLM cost: 144 × $0.02 ≈ $2.88/cycle, $11.50/day.

### Task 4.1: New `bullbot.agents.search_agent` — subagent entry point

**Files:**
- Create: `bullbot/agents/search_agent.py` — accepts `(ticker, class, regime)` as args, runs a Phase-1+2 sweep, returns top-5 cells as a JSON blob to the dispatcher.
- Create: `bullbot/agents/dispatcher.py` — runs in `scheduler.tick()`, dispatches N subagents in parallel via `claude_agent_sdk.spawn()`, collates results, picks gate-passers.
- Test: `tests/integration/test_agent_dispatch.py`

### Task 4.2: Regime-aware slicing

**Files:**
- Modify: `bullbot/regime/classifier.py` — exposes `current_regime(ticker) → 'bull'|'chop'|'bear'` so the dispatcher fans out only to regime-appropriate agents.

### Task 4.3: Cost cap + budget telemetry

**Files:**
- Modify: `bullbot/agents/dispatcher.py` — hard cap of `BUDGET_USD_PER_TICK = 5.0` (configurable). Stops dispatching new subagents once cumulative LLM spend in the current tick hits the cap.

### Task 4.4: Append-only results journal

**Files:**
- Modify: `schemas/agent_runs.py` — new SQLite table `agent_runs` for the trail: `(agent_id, ticker, class, regime, n_cells, n_passed, top_pf_oos, llm_cost_usd, started_at, finished_at)`.

---

## Phase 5 — Continuous-cycle daemon (1 day)

Run Phases 1–4 every market-hour automatically. No human in the loop.

### Task 5.1: Replace one-off `run_one_tick.py` with `run_continuous.py`

**Files:**
- Create: `scripts/run_continuous.py` — loop that calls `scheduler.tick()` every 60 minutes during market hours (09:35–16:00 ET), skips weekends and holidays, writes a heartbeat to `cache/last_continuous_run.txt`.
- Modify: `.mentor/DAILY_PROMPT.md` Step 2 — first sense action becomes "check heartbeat freshness; if stale, restart the daemon."

### Task 5.2: Mentor daily run fires the daemon if it's down

**Files:**
- Modify: `.mentor/DAILY_PROMPT.md` Step 4 — auto-launch action when heartbeat is >12h stale: `nohup python scripts/run_continuous.py > logs/continuous.log 2>&1 &`.

---

## Self-Review

**Spec coverage:** every Dan-question maps to a phase.
- "Test thousands of strategies per day" → Phases 1 + 2 + 3 get to ~1,000/day on a single laptop. Phase 4 gets to 10,000+/day with agent fan-out.
- "Hundreds of agents" → Phase 4 specifically. 144 agent slots × 4 cycles/day = 576 agent-cycles/day.
- "Running all the time, every hour" → Phase 5 continuous-cycle daemon.
- "Money on the floor" → cumulative effect: ~40,000 backtests/day means a 0.1% hit-rate finds ~40 edge candidates/day, even if only 1% of *those* survive paper-trial that's still ~12 promotions/month. Today the bot has promoted 3 strategies in 33 days.

**Placeholder scan:** Tasks 1.2, 4.1, 5.1 are sketched rather than detailed — they're 2-3 sub-tasks each. Acceptable because each is a self-contained module with a clear interface; an executing-plans subagent can detail them.

**Open questions for Dan before execution:**
1. Do you want all 5 phases, or stop after Phase 3 (single-machine, ~1,000/day)? Phase 4 is where it gets agent-y and where my Anthropic API spend will be highest.
2. Cost cap — `BUDGET_USD_PER_TICK = 5.0`? Or higher / lower?
3. Do you want me to dispatch a subagent to *execute this plan*, or do you want to read it through first and adjust?

---

## Execution Handoff

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task; review between tasks; this plan + the existing Superpowers `subagent-driven-development` skill drives the work.
2. **Inline Execution** — I work the plan task-by-task in the mentor daily-run sandbox over multiple days.

Default if you don't pick: Inline Execution starting tomorrow, Phase 0 first.
