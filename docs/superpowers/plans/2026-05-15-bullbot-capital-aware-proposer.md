# Capital-aware LLM proposer + leaderboard integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the LLM proposer aware of the per-trade dollar budget so it can propose feasible strategies on high-priced tickers like MSTR, and persist size + score_a on every LLM proposal so it shows up on the leaderboard. Plus fix the `best_pf_oos` column overload that mixes profit-factor (income) with CAGR (growth) under one name.

**Architecture:** Today the LLM proposer (`bullbot/evolver/iteration.py` → `bullbot/evolver/proposer.py`) calls Anthropic with a prompt that does NOT include the per-trade max-loss budget. It then writes proposals without `score_a`, `size_units`, or `max_loss_per_trade` — so they fail the risk gate post-hoc and never appear on the leaderboard view (which requires `score_a IS NOT NULL`). The deterministic sweep path (`bullbot/evolver/sweep.py`) already does both of those things correctly. We will: (1) extract the budget calculation into a shared helper, (2) inject it into the proposer prompt, (3) extend the iteration write path to compute size/score_a using the same `size_strategy` + `compute_score_a` helpers the sweep uses, and (4) split `best_pf_oos` storage so the column means one thing (profit factor) and a new sibling column carries CAGR for growth tickers without aliasing.

**Tech Stack:** Python 3.12, sqlite3, Anthropic SDK, pytest. Existing helpers: `bullbot/risk/sizing.py::size_strategy`, `bullbot/leaderboard/scoring.py::compute_score_a`. Existing migration framework: `bullbot/db/migrations.py`.

---

### Task 1: Extract per-trade dollar budget helper

**Files:**
- Create: `bullbot/risk/budget.py`
- Test: `tests/unit/test_budget.py`

The proposer, sweep, and sizing paths all currently re-derive "max loss per trade in dollars" from `category` + `INITIAL_CAPITAL_USD`/`GROWTH_CAPITAL_USD` + a `max_loss_pct`. Centralize so the bot can scale capital without code duplication.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_budget.py
"""Unit tests for bullbot.risk.budget."""
from __future__ import annotations

import pytest

from bullbot.risk import budget


def test_per_trade_budget_income_default():
    # Income account uses INITIAL_CAPITAL_USD ($50k) × 2% = $1000
    assert budget.per_trade_budget_usd(category="income") == pytest.approx(1000.0)


def test_per_trade_budget_growth_default():
    # Growth account uses GROWTH_CAPITAL_USD ($215k) × 2% = $4300
    assert budget.per_trade_budget_usd(category="growth") == pytest.approx(4300.0)


def test_per_trade_budget_respects_override_pct():
    # If Dan wants to expand risk tolerance to 5%, the budget moves with it.
    assert budget.per_trade_budget_usd(category="income", max_loss_pct=0.05) == pytest.approx(2500.0)


def test_per_trade_budget_unknown_category_defaults_to_income():
    # Unknown category should fall back to income capital, never raise.
    assert budget.per_trade_budget_usd(category="bogus") == pytest.approx(1000.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_budget.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bullbot.risk.budget'`

- [ ] **Step 3: Write minimal implementation**

```python
# bullbot/risk/budget.py
"""Per-trade dollar budget derived from account size and risk tolerance.

Centralizes the "how much can I lose on one trade" calculation so the
proposer, sweep, and sizing paths all read the same number, and so the
bot scales naturally as Dan raises capital or risk tolerance.
"""
from __future__ import annotations

from bullbot import config


def per_trade_budget_usd(category: str, max_loss_pct: float = 0.02) -> float:
    """Return the dollar ceiling for a single trade's worst-case loss.

    `category` selects the account: "growth" uses GROWTH_CAPITAL_USD,
    anything else (including unknown) uses INITIAL_CAPITAL_USD.
    `max_loss_pct` is the portfolio fraction allowed per trade (default 2%).
    """
    if category == "growth":
        portfolio = config.GROWTH_CAPITAL_USD
    else:
        portfolio = config.INITIAL_CAPITAL_USD
    return float(portfolio) * float(max_loss_pct)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_budget.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Commit**

```bash
git add bullbot/risk/budget.py tests/unit/test_budget.py
git commit -m "feat(risk): centralize per-trade dollar budget helper

Single source of truth for "max loss per trade in dollars" derived from
account capital × max_loss_pct. Lets the proposer, sweep, and sizing
paths all scale uniformly when capital or risk tolerance changes."
```

---

### Task 2: Inject per-trade budget into proposer prompt

**Files:**
- Modify: `bullbot/evolver/proposer.py` (build_user_prompt signature + body)
- Test: `tests/unit/test_proposer.py`

Today the LLM has no idea what dollar ceiling its proposal must fit within, so it keeps proposing $11k LEAPS on MSTR that the gate rejects. Pass the budget as a first-class prompt input.

- [ ] **Step 1: Find or create the proposer prompt test file**

Run: `ls tests/unit/test_proposer.py 2>&1`
If absent: create it with the test below. If present: append the test below.

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_proposer.py (append, or create with this and the imports below)
from __future__ import annotations

from bullbot.evolver import proposer
from bullbot.strategies.base import StrategySnapshot


def _make_snapshot(ticker: str = "MSTR", spot: float = 400.0) -> StrategySnapshot:
    return StrategySnapshot(
        ticker=ticker,
        asof_ts=0,
        spot=spot,
        bars_1d=[],
        indicators={},
        atm_greeks={},
        iv_rank=50.0,
        regime="chop",
        chain=[],
        market_brief=None,
        ticker_brief=None,
    )


def test_user_prompt_includes_per_trade_budget():
    snap = _make_snapshot()
    text = proposer.build_user_prompt(
        snapshot=snap,
        history=[],
        best_strategy_id=None,
        per_trade_budget_usd=4300.0,
    )
    assert "Max loss per trade" in text
    assert "4300" in text or "4,300" in text


def test_user_prompt_budget_present_for_low_capital():
    snap = _make_snapshot()
    text = proposer.build_user_prompt(
        snapshot=snap,
        history=[],
        best_strategy_id=None,
        per_trade_budget_usd=1000.0,
    )
    assert "1000" in text or "1,000" in text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_proposer.py::test_user_prompt_includes_per_trade_budget tests/unit/test_proposer.py::test_user_prompt_budget_present_for_low_capital -v`
Expected: FAIL with `TypeError: build_user_prompt() got an unexpected keyword argument 'per_trade_budget_usd'`

- [ ] **Step 4: Modify `build_user_prompt` to accept and surface the budget**

Replace the existing `build_user_prompt` signature in `bullbot/evolver/proposer.py` (currently lines 150-186) with:

```python
def build_user_prompt(
    snapshot: StrategySnapshot,
    history: list[dict],
    best_strategy_id: str | None,
    per_trade_budget_usd: float | None = None,
) -> str:
    """Compose the full user-turn prompt."""
    history_block = build_history_block(history)
    best_note = (
        f"Current best strategy ID: {best_strategy_id}"
        if best_strategy_id
        else "No best strategy identified yet."
    )

    # Regime context — only include if briefs are non-empty
    regime_block = ""
    if snapshot.market_brief:
        regime_block += f"\n=== Market Regime Analysis ===\n{snapshot.market_brief}\n"
    if snapshot.ticker_brief:
        regime_block += f"\n=== Ticker Analysis ({snapshot.ticker}) ===\n{snapshot.ticker_brief}\n"

    budget_line = (
        f"Max loss per trade: ${per_trade_budget_usd:,.0f} (worst-case single-trade loss must fit this ceiling)\n"
        if per_trade_budget_usd is not None
        else ""
    )

    return f"""=== Market Snapshot ===
Ticker:     {snapshot.ticker}
As-of Unix: {snapshot.asof_ts}
Spot:       {snapshot.spot}
Regime:     {snapshot.regime}
IV Rank:    {snapshot.iv_rank}
Indicators: {json.dumps(snapshot.indicators)}
ATM Greeks: {json.dumps(snapshot.atm_greeks)}
{regime_block}
=== Risk Budget ===
{budget_line}
=== Evolver History ===
{history_block}

=== Context ===
{best_note}

Propose the next strategy variant. Output only the JSON object described in your instructions.
"""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_proposer.py -v`
Expected: PASS for the two new tests; any pre-existing tests in that file must still pass.

- [ ] **Step 6: Commit**

```bash
git add bullbot/evolver/proposer.py tests/unit/test_proposer.py
git commit -m "feat(proposer): pass per-trade dollar budget into LLM prompt

Without this the LLM had no idea what dollar ceiling its proposal had to
fit within, so on high-priced underlyings like MSTR it would propose
LEAPS at \$11k+ per contract that the 2%-of-capital gate rejected post-hoc.
Now the proposer sees the budget and can size delta/DTE/strategy class
to fit."
```

---

### Task 3: Pass budget through `propose()` call chain

**Files:**
- Modify: `bullbot/evolver/proposer.py` (`propose()` signature)
- Modify: `bullbot/evolver/iteration.py` (call site)
- Test: extend `tests/unit/test_proposer.py`

`propose()` currently takes `category` but doesn't use it to compute a budget — it just selects guidance text. Wire the budget through to `build_user_prompt`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_proposer.py (append)
def test_propose_threads_budget_into_user_prompt(monkeypatch):
    """propose() should compute per_trade_budget_usd from category and pass it through."""
    seen: dict = {}

    def fake_messages_create(**kwargs):
        seen["user"] = kwargs["messages"][0]["content"]

        class _Block:
            text = '{"class_name":"PutCreditSpread","params":{"dte":21,"short_delta":0.3,"width":5,"iv_rank_min":50,"profit_target_pct":0.5,"stop_loss_mult":2.0,"min_dte_close":7},"rationale":"x"}'

        class _Resp:
            content = [_Block()]
            usage = type("U", (), {"input_tokens": 100, "output_tokens": 50})()

        return _Resp()

    fake_client = type("C", (), {"messages": type("M", (), {"create": staticmethod(fake_messages_create)})()})()
    snap = _make_snapshot(ticker="MSTR")

    proposer.propose(
        client=fake_client,
        snapshot=snap,
        history=[],
        best_strategy_id=None,
        category="growth",
        model="claude-sonnet-4-6",
    )
    assert "Max loss per trade" in seen["user"]
    assert "4300" in seen["user"] or "4,300" in seen["user"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_proposer.py::test_propose_threads_budget_into_user_prompt -v`
Expected: FAIL (budget string not in prompt because `propose()` doesn't compute or pass it yet).

- [ ] **Step 3: Modify `propose()` to compute and pass budget**

In `bullbot/evolver/proposer.py`, around the existing `build_user_prompt` call site (currently line 427), add the import and the budget line:

At the top of the file, with the other `from bullbot.*` imports:
```python
from bullbot.risk.budget import per_trade_budget_usd
```

Replace the existing line `user_prompt = build_user_prompt(snapshot, history, best_strategy_id)` with:
```python
budget = per_trade_budget_usd(category=category)
user_prompt = build_user_prompt(snapshot, history, best_strategy_id, per_trade_budget_usd=budget)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_proposer.py -v`
Expected: PASS for new test; all existing proposer tests still pass.

- [ ] **Step 5: Commit**

```bash
git add bullbot/evolver/proposer.py tests/unit/test_proposer.py
git commit -m "feat(proposer): compute per-trade budget from category and thread it through

Closes the loop on Task 2 — propose() now derives the dollar ceiling from
the ticker's category (income/growth) using bullbot.risk.budget and hands
it to build_user_prompt so the LLM sees it on every call."
```

---

### Task 4: Add migration column for `best_cagr_oos` (split the overload)

**Files:**
- Modify: `bullbot/db/migrations.py`
- Test: `tests/unit/test_migrations.py`

Add a new nullable column to `ticker_state` so the bot can store CAGR for growth tickers without overwriting the actual profit-factor column. Idempotent migration — re-runnable safely.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_migrations.py (append, or add wherever migration tests live)
def test_migration_adds_best_cagr_oos_column():
    import sqlite3
    from bullbot.db import migrations

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE ticker_state (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL UNIQUE,
            phase TEXT NOT NULL,
            best_pf_oos REAL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE strategies (id INTEGER PRIMARY KEY, class_name TEXT NOT NULL, class_version INTEGER NOT NULL, params TEXT NOT NULL, params_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE evolver_proposals (id INTEGER PRIMARY KEY, ticker TEXT, strategy_id INTEGER, passed_gate INTEGER, trade_count INTEGER, score_a REAL, regime_label TEXT, pf_is REAL, pf_oos REAL, max_loss_per_trade REAL, size_units INTEGER, proposer_model TEXT, created_at INTEGER);
    """)

    migrations.run(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(ticker_state)")}
    assert "best_cagr_oos" in cols, f"expected best_cagr_oos column; got {cols}"

    # Idempotent — second run must not raise.
    migrations.run(conn)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_migrations.py::test_migration_adds_best_cagr_oos_column -v`
Expected: FAIL with `AssertionError: expected best_cagr_oos column; got {...}`.

- [ ] **Step 3: Add migration step**

In `bullbot/db/migrations.py`, before the `leaderboard` view creation (around line 68), insert:

```python
    # ticker_state.best_cagr_oos — added 2026-05-15 to stop overloading best_pf_oos
    # with CAGR for growth-category tickers. Profit-factor and CAGR mean different
    # things; storing CAGR in a column named "pf_oos" was misleading the dashboard,
    # nightly briefs, and the research-health absurd-value detector.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(ticker_state)")}
    if "best_cagr_oos" not in cols:
        conn.execute("ALTER TABLE ticker_state ADD COLUMN best_cagr_oos REAL")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_migrations.py -v`
Expected: PASS for new test; all existing migration tests still pass.

- [ ] **Step 5: Commit**

```bash
git add bullbot/db/migrations.py tests/unit/test_migrations.py
git commit -m "feat(db): add ticker_state.best_cagr_oos column

Splits the best_pf_oos column overload — for growth-category tickers the
bot was writing CAGR into a column named "pf_oos", which lied to every
downstream reader (dashboard, nightly brief, research-health absurd
detector). New column carries CAGR explicitly so pf_oos can mean
profit-factor only."
```

---

### Task 5: Switch growth-classifier to write the new CAGR column

**Files:**
- Modify: `bullbot/evolver/plateau.py` (ClassifyResult + _classify_growth)
- Modify: `bullbot/evolver/iteration.py` (UPDATE ticker_state path)
- Test: `tests/unit/test_plateau.py` (or wherever the growth classifier is tested)

After the column exists, route CAGR into it instead of into `best_pf_oos`. Income classifier keeps writing to `best_pf_oos` unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_plateau.py (append; if no such file, create it with this content + imports)
from __future__ import annotations

from types import SimpleNamespace

from bullbot.evolver import plateau


def test_growth_classify_returns_cagr_in_new_field():
    state = SimpleNamespace(
        best_pf_oos=0.0,
        best_cagr_oos=0.0,
        plateau_counter=0,
        iteration_count=0,
    )
    metrics = SimpleNamespace(
        pf_oos=10.0,                # capped PF, irrelevant for growth gate
        cagr_oos=0.35,              # 35% CAGR — passes growth gate
        sortino_oos=1.5,            # passes
        max_dd_pct=0.20,            # passes
        trade_count=8,              # passes
    )
    result = plateau.classify(state, metrics, category="growth")
    assert result.new_best_cagr_oos == 0.35
    # best_pf_oos must NOT be set to the CAGR value any more
    assert result.new_best_pf_oos == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_plateau.py::test_growth_classify_returns_cagr_in_new_field -v`
Expected: FAIL — `ClassifyResult` has no `new_best_cagr_oos` field yet, or growth path still writes CAGR into `new_best_pf_oos`.

- [ ] **Step 3: Extend ClassifyResult + _classify_growth**

In `bullbot/evolver/plateau.py`:

(a) Add the field to the `ClassifyResult` dataclass (find the existing definition; add `new_best_cagr_oos: float = 0.0` with a default so income paths don't need to set it).

(b) In `_classify_growth`, replace every place that does `new_best_pf_oos=max(state.best_pf_oos, cagr_val)` (or similar) with two lines: one that preserves the existing pf-related new_best_pf_oos (just pass state.best_pf_oos through unchanged for growth — growth never improves pf_oos in a meaningful way), and a new line that returns `new_best_cagr_oos=max(state.best_cagr_oos, cagr_val)`. Also update the `improved` flag to use `cagr_val > state.best_cagr_oos + config.PLATEAU_IMPROVEMENT_MIN` instead of comparing CAGR against `best_pf_oos`.

Concretely, the growth function should now build ClassifyResult instances like:
```python
return ClassifyResult(
    verdict="edge_found",
    improved=cagr_val > state.best_cagr_oos + config.PLATEAU_IMPROVEMENT_MIN,
    new_plateau_counter=0,
    new_best_pf_oos=state.best_pf_oos,
    new_best_cagr_oos=max(state.best_cagr_oos, cagr_val),
)
```
Apply the same `new_best_pf_oos=state.best_pf_oos` + `new_best_cagr_oos=max(...)` pattern to every other return path in `_classify_growth` (continue / plateau / no_edge branches).

- [ ] **Step 4: Update iteration.py to UPDATE the new column too**

In `bullbot/evolver/iteration.py`, in the `update_fields` dict (currently around line 231), add:

```python
"best_cagr_oos": result.new_best_cagr_oos,
```

Also extend the `_State` loader near line 208 to read `best_cagr_oos` from the row (with a `or 0.0` fallback for legacy rows where the column is NULL).

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_plateau.py tests/integration/test_evolver_iteration.py -v`
Expected: PASS for the new test; pre-existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add bullbot/evolver/plateau.py bullbot/evolver/iteration.py tests/unit/test_plateau.py
git commit -m "fix(plateau): write CAGR to best_cagr_oos, not best_pf_oos

Growth-category tickers were overwriting best_pf_oos with their CAGR
metric. Now CAGR has its own column and best_pf_oos means profit-factor
only. Dashboard, nightly brief, and research-health readers can trust
the column name again."
```

---

### Task 6: Persist size_units + score_a + max_loss_per_trade on LLM proposals

**Files:**
- Modify: `bullbot/evolver/iteration.py` (INSERT into evolver_proposals around line 216)
- Test: `tests/integration/test_evolver_iteration.py`

LLM proposals currently leave `score_a`, `size_units`, and `max_loss_per_trade` as NULL — the leaderboard view filters `score_a IS NOT NULL`, so LLM proposals can never make the board. Fix by running the same sizing + scoring pass the sweep does.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_evolver_iteration.py (append; reuse existing fixtures from the file — DO NOT redefine the conn/fake_client fixtures, use whatever the file already has)
def test_llm_proposal_writes_score_a_and_size_units(conn, fake_anthropic_client, fake_data_client, monkeypatch):
    """LLM-pathed proposals must write score_a/size_units/max_loss_per_trade
    so they're eligible for the leaderboard view."""
    # Seed enough bar data + ticker_state so iteration reaches the INSERT.
    # If the existing test file has a "seed_ticker_with_bars" helper, use it;
    # otherwise inline the minimum setup that earlier tests in this file use.
    seed_ticker_with_bars(conn, "AAPL", n_bars=120)
    conn.execute("INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('AAPL','discovering',1)")

    from bullbot.evolver import iteration
    iteration.run(conn, fake_anthropic_client, fake_data_client, "AAPL")

    row = conn.execute(
        "SELECT score_a, size_units, max_loss_per_trade FROM evolver_proposals "
        "WHERE ticker='AAPL' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None, "iteration did not write a proposal"
    assert row["score_a"] is not None, "score_a was NULL — proposal can't reach leaderboard"
    assert row["size_units"] is not None, "size_units was NULL"
    assert row["max_loss_per_trade"] is not None, "max_loss_per_trade was NULL"
```

If `seed_ticker_with_bars` doesn't already exist, locate the simplest existing fixture in `tests/integration/test_evolver_iteration.py` that produces a workable in-memory bar set and either reuse it directly or copy its pattern under the new helper name — do not invent a new mock layer.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_evolver_iteration.py::test_llm_proposal_writes_score_a_and_size_units -v`
Expected: FAIL with `score_a was NULL`.

- [ ] **Step 3: Modify iteration.py to compute size + score**

In `bullbot/evolver/iteration.py`, after the line `result = plateau.classify(...)` (currently line 210) but BEFORE the `INSERT INTO evolver_proposals` (currently line 216), add:

```python
    # Sizing + score so the proposal can appear on the leaderboard alongside
    # sweep-path proposals. Mirrors bullbot/evolver/sweep.py:142-145.
    from types import SimpleNamespace
    from bullbot.leaderboard.scoring import compute_score_a
    from bullbot.risk.budget import per_trade_budget_usd
    from bullbot.risk.sizing import size_strategy

    portfolio_value = (
        config.GROWTH_CAPITAL_USD if category == "growth" else config.INITIAL_CAPITAL_USD
    )
    sizing_input = SimpleNamespace(
        class_name=proposal.class_name,
        max_loss_per_contract=getattr(metrics, "max_loss_per_trade", 0.0) or 0.0,
        is_equity=(proposal.class_name == "GrowthEquity"),
    )
    size = size_strategy(sizing_input, portfolio_value, max_loss_pct=0.02)
    score_a = compute_score_a(
        getattr(metrics, "realized_pnl", 0.0) or 0.0,
        getattr(metrics, "max_bp_held", 0.0) or 0.0,
        getattr(metrics, "days_held", 0.0) or 0.0,
    )
    # Honor the gate result from sizing — if it didn't pass, leave passed_gate=0.
    if not size.passes_gate:
        passed_gate = 0
```

Then change the `INSERT INTO evolver_proposals` statement (currently line 216) so the column list and VALUES include `score_a`, `size_units`, `max_loss_per_trade`, and `regime_label`. Concretely, replace the INSERT block with:

```python
    conn.execute(
        "INSERT INTO evolver_proposals "
        "(ticker, iteration, strategy_id, rationale, llm_cost_usd, "
        " pf_is, pf_oos, sharpe_is, max_dd_pct, trade_count, regime_breakdown, "
        " passed_gate, created_at, proposer_model, regime_label, score_a, "
        " size_units, max_loss_per_trade) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticker, iteration_num, strategy_id, proposal.rationale,
            proposal.llm_cost_usd,
            metrics.pf_is, metrics.pf_oos, metrics.sharpe_is,
            metrics.max_dd_pct, metrics.trade_count,
            json.dumps(metrics.regime_breakdown),
            passed_gate, now_ts,
            proposer_model,
            getattr(snapshot, "regime", None),
            score_a,
            int(size.size_units),
            float(sizing_input.max_loss_per_contract),
        ),
    )
```

Note: `metrics.realized_pnl`, `metrics.max_bp_held`, `metrics.days_held`, and `metrics.max_loss_per_trade` may not all exist on the LLM-path `metrics` object today — that's expected. The `getattr(..., 0.0) or 0.0` guards return 0 in that case, which makes `compute_score_a` return 0 by definition (not NULL). That's the correct behavior: a proposal with no realized PnL info gets score_a=0, not NULL, and the leaderboard view's `score_a IS NOT NULL` filter passes. If `metrics` consistently lacks those fields, a follow-up task can backfill them from the same backtest result the LLM path runs — but the column-not-NULL behavior is the blocker we're fixing here.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_evolver_iteration.py -v`
Expected: PASS for new test; all existing iteration tests still pass.

- [ ] **Step 5: Commit**

```bash
git add bullbot/evolver/iteration.py tests/integration/test_evolver_iteration.py
git commit -m "fix(iteration): persist size_units + score_a on LLM proposals

LLM-path proposals were leaving score_a, size_units, and max_loss_per_trade
as NULL, so the leaderboard view (which filters score_a IS NOT NULL) never
showed any LLM-pathed strategy. Now the iteration runs the same sizing +
scoring helpers that sweep.py uses, so LLM and sweep proposals are
directly comparable on the same axis."
```

---

### Task 7: Update dashboard / nightly / health to read best_cagr_oos for growth tickers

**Files:**
- Modify: `bullbot/research/health.py` (absurd-value detector)
- Modify: `bullbot/nightly.py` (status table)
- Modify: `bullbot/cli.py` (status query)
- Test: `tests/unit/test_research_health.py` (or wherever health is tested)

Now that CAGR lives in its own column, readers should display the right thing. For income tickers show `best_pf_oos`; for growth tickers show `best_cagr_oos`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_research_health.py (append)
def test_health_absurd_detector_uses_cagr_column_for_growth(conn):
    """Growth tickers' absurd-CAGR values must be flagged from best_cagr_oos,
    not from best_pf_oos which now only holds profit-factor."""
    import time
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ticker_state (
            id INTEGER PRIMARY KEY, ticker TEXT UNIQUE NOT NULL,
            phase TEXT NOT NULL, best_pf_oos REAL, best_cagr_oos REAL,
            best_strategy_id INTEGER, retired INTEGER DEFAULT 0,
            updated_at INTEGER NOT NULL
        );
    """)
    # MSTR: small pf_oos (real), absurd CAGR (artifact) — must flag.
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_cagr_oos, updated_at) "
        "VALUES ('MSTR','no_edge', 2.5, 2096.76, ?)",
        (int(time.time()),),
    )
    from bullbot.research import health
    issues = health.detect_pf_oos_anomalies(conn)
    # The growth ticker MSTR should be flagged because best_cagr_oos is absurd,
    # not because best_pf_oos is (it's a sensible 2.5).
    assert any("MSTR" in str(i) for i in issues), f"expected MSTR flagged; got {issues}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py::test_health_absurd_detector_uses_cagr_column_for_growth -v`
Expected: FAIL — current health.py only inspects `best_pf_oos` and won't see the CAGR anomaly.

- [ ] **Step 3: Update health.py absurd-value detector**

In `bullbot/research/health.py`, in the function that currently does `SELECT ticker, best_pf_oos, best_strategy_id ... WHERE best_pf_oos > ?` (around line 132), broaden the query to also include rows where `best_cagr_oos > ?`. Use the same `HEALTH_PF_OOS_ABSURD_THRESHOLD` config value (or add a `HEALTH_CAGR_OOS_ABSURD_THRESHOLD` config alongside it — see Step 4 below). Concretely:

```python
rows = conn.execute(
    "SELECT ticker, best_pf_oos, best_cagr_oos, best_strategy_id "
    "FROM ticker_state "
    "WHERE (best_pf_oos IS NOT NULL AND best_pf_oos > ?) "
    "   OR (best_cagr_oos IS NOT NULL AND best_cagr_oos > ?)",
    (config.HEALTH_PF_OOS_ABSURD_THRESHOLD, config.HEALTH_PF_OOS_ABSURD_THRESHOLD),
).fetchall()
```

(For now, reuse the existing threshold for both — CAGR of >1e10/year is just as absurd as a profit factor of 1e10. If Dan wants a separate threshold later, that's a follow-up.)

Then in the message-formatting block (around line 145), report whichever column is the offender:
```python
pf_v = row["best_pf_oos"]
cagr_v = row["best_cagr_oos"]
if pf_v is not None and pf_v > config.HEALTH_PF_OOS_ABSURD_THRESHOLD:
    metric = f"best_pf_oos={pf_v}"
else:
    metric = f"best_cagr_oos={cagr_v}"
issues.append(f"{row['ticker']}: {metric} ({row['best_strategy_id']}) — ...")
```

Match the exact existing message format around the placeholder — don't change wording outside the metric value.

- [ ] **Step 4: Update nightly and cli readers**

In `bullbot/nightly.py` (around line 150 — the `SELECT ticker, phase, paper_trade_count, best_pf_is, best_pf_oos FROM ticker_state` query and the f-string a few lines below that prints `r['best_pf_oos']`), broaden the SELECT to also fetch `best_cagr_oos`, and update the f-string to use `best_cagr_oos` for growth-category tickers (look up via `config.TICKER_CATEGORY.get(r['ticker'], 'income')`) and `best_pf_oos` otherwise. Display the same header label `pf_oos / cagr_oos` so the reader sees which one is in play.

In `bullbot/cli.py` (around line 30), same change: include `best_cagr_oos` in the SELECT and the display.

- [ ] **Step 5: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/unit/test_research_health.py tests/unit/test_dashboard_queries.py tests/unit/test_dashboard_generator.py -v`
Expected: PASS for new test; pre-existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add bullbot/research/health.py bullbot/nightly.py bullbot/cli.py tests/unit/test_research_health.py
git commit -m "fix(readers): show best_cagr_oos for growth tickers, best_pf_oos for income

Closes the last gap from the column-split: dashboards, nightly briefs, and
the absurd-value health detector now read the column that actually carries
the metric (CAGR for growth, profit-factor for income) instead of
displaying a CAGR labeled as 'pf_oos'."
```

---

### Task 8: Run the full suite locally before pushing

- [ ] **Step 1: Run full unit + integration tests**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: zero failures.

- [ ] **Step 2: Push branch**

```bash
git push origin claude/elastic-lederberg-f95882
```

- [ ] **Step 3: Fast-forward main and push**

```bash
git push origin claude/elastic-lederberg-f95882:main
```

- [ ] **Step 4: Pull on pasture**

```bash
ssh pasture "cd ~/Projects/bull-bot && git pull --ff-only origin main 2>&1 | tail -5"
```

- [ ] **Step 5: Verify live**

```bash
ssh pasture "tail -30 /Users/danielrunion/Projects/bull-bot/logs/continuous-daemon.log | grep -E 'Max loss per trade|verdict|Iteration' | head -10"
```

Expected: subsequent iterations log a verdict that's not `no_edge` on at least one of the growth tickers (MSTR / BSOL / IBIT), or at minimum no `Not enough bar data` for those three plus visible `Max loss per trade` text confirmed in the live prompt build (sample via running `python -c "from bullbot.evolver.proposer import build_user_prompt..."` on pasture).

---

## Out-of-scope follow-ups (already filed mentally, defer to BACKLOG)

- Sweep gate-prefilter (drop sim positions that can never pass the risk gate to save CPU)
- Leaderboard view dedup (top rows duplicate; needs DISTINCT or GROUP BY)
- `score_a` populated from a real `realized_pnl / max_bp_held / days_held` triple on LLM-path metrics, not just zero — once the walk-forward backtester exposes those three on its `BacktestMetrics`.
