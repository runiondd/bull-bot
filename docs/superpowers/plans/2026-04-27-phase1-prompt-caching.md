# Phase 1: Prompt Caching + Skip-Retired-Briefs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add prompt-caching plumbing to the proposer + ticker_brief callers, and stop generating regime briefs for retired tickers. Foundational work for Phases 3-4 (batched proposals + iterations-per-tick) which will multiply the cache benefit.

**Architecture:** Anthropic's API supports `cache_control: {"type": "ephemeral"}` on individual content blocks. We refactor the existing string-based `system=` and `messages=[{content: str}]` calls into structured content-block lists, marking the static parts (system prompt, strategy catalog, format instructions) as cached. Within-tick reuse is the win; first call per tick is a cache miss, subsequent calls in the same 5-minute window get ~90% off.

**Tech Stack:** Python 3.12, Anthropic SDK (`anthropic.Anthropic`), pytest with the existing `FakeAnthropicClient` fixture.

**Spec:** `docs/superpowers/specs/2026-04-27-agentic-throughput-design.md` (read this first — everything below implements Phase 1 of that spec).

**Honest expectation:** Phase 1 alone produces ~$0.05/day in cost savings (small system-prompt cache hits across the 5 sequential proposer calls per tick). The big savings come once Phase 3 (batched proposals) and Phase 4 (iterations-per-tick) ship, because those create many more calls within each cache window. Phase 1 is the plumbing that lets those phases work.

---

## File Structure

```
bullbot/
├── llm/
│   ├── __init__.py        (NEW, empty)
│   └── cache.py           (NEW, ~80 LOC) — content-block builders with cache_control markers
├── evolver/
│   └── proposer.py        (MODIFIED) — use cache.py to build structured prompt
├── features/
│   └── regime_agent.py    (MODIFIED) — use cache.py for ticker_brief; skip retired
└── config.py              (MODIFIED) — new knobs

tests/unit/
├── test_llm_cache.py            (NEW)
├── test_evolver_proposer.py     (MODIFIED — assert cache markers in client kwargs)
├── test_regime_agent.py         (MODIFIED — assert retired tickers are skipped)
└── test_config.py               (MODIFIED — new knob assertions)
```

---

## Task 1: Add Phase 1 config knobs

**Files:**
- Modify: `bullbot/config.py`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_config.py`:

```python
def test_phase1_caching_config():
    assert config.PROPOSER_CACHE_ENABLED is True
    assert config.SKIP_BRIEFS_FOR_RETIRED is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_config.py::test_phase1_caching_config -v`
Expected: FAIL with `AttributeError: module 'bullbot.config' has no attribute 'PROPOSER_CACHE_ENABLED'`.

- [ ] **Step 3: Add the config knobs**

In `bullbot/config.py`, append after the existing `HEALTH_PF_OOS_ABSURD_THRESHOLD` line (around line 99):

```python
# --- Agentic throughput (Phase 1: caching + retired-ticker brief skip) ---

PROPOSER_CACHE_ENABLED = True       # mark static prompt blocks as ephemeral-cacheable
SKIP_BRIEFS_FOR_RETIRED = True      # don't generate regime briefs for no_edge / killed tickers
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_config.py -v`
Expected: all pass (including the new test).

- [ ] **Step 5: Commit**

```bash
git add bullbot/config.py tests/unit/test_config.py
git commit -m "config: add Phase 1 agentic-throughput knobs (cache + skip retired briefs)"
```

---

## Task 2: `bullbot/llm/cache.py` — content-block builder

**Files:**
- Create: `bullbot/llm/__init__.py` (empty)
- Create: `bullbot/llm/cache.py`
- Create: `tests/unit/test_llm_cache.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_llm_cache.py`:

```python
"""Tests for bullbot.llm.cache — content-block builders with cache_control."""
from __future__ import annotations

from bullbot.llm import cache


def test_cached_text_block_basic():
    block = cache.cached_text_block("hello world")
    assert block == {
        "type": "text",
        "text": "hello world",
        "cache_control": {"type": "ephemeral"},
    }


def test_text_block_uncached():
    block = cache.text_block("hello world")
    assert block == {"type": "text", "text": "hello world"}
    assert "cache_control" not in block


def test_build_system_blocks_marks_last_block_cached(monkeypatch):
    """When caching is enabled, the LAST block in the system list gets cache_control.
    Anthropic's cache markers are *cumulative* — marking the final block caches
    everything before it as well."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_CACHE_ENABLED", True)
    blocks = cache.build_system_blocks(["base instructions", "strategy catalog"])
    assert len(blocks) == 2
    # Final block has cache_control; earlier blocks do not
    assert "cache_control" not in blocks[0]
    assert blocks[-1].get("cache_control") == {"type": "ephemeral"}


def test_build_system_blocks_no_caching_when_disabled(monkeypatch):
    """When caching is disabled, no block gets cache_control."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_CACHE_ENABLED", False)
    blocks = cache.build_system_blocks(["base instructions", "strategy catalog"])
    for b in blocks:
        assert "cache_control" not in b


def test_build_system_blocks_empty():
    """Empty input → empty list, not an error."""
    blocks = cache.build_system_blocks([])
    assert blocks == []


def test_build_user_content_marks_last_static_block(monkeypatch):
    """User content has a static prefix and a fresh suffix; mark the last static block cached."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_CACHE_ENABLED", True)
    blocks = cache.build_user_content(
        static_blocks=["instructions", "history"],
        fresh_blocks=["current snapshot"],
    )
    assert len(blocks) == 3
    # Last static block (index 1) gets cache_control; fresh block does not
    assert "cache_control" not in blocks[0]
    assert blocks[1].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in blocks[2]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_llm_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bullbot.llm'`.

- [ ] **Step 3: Create the module**

Create `bullbot/llm/__init__.py` as empty file.

Create `bullbot/llm/cache.py`:

```python
"""Anthropic prompt-caching helpers.

Anthropic's API supports `cache_control: {"type": "ephemeral"}` on individual
content blocks. The cache covers everything from the start of the prompt up
to and including the marked block (it's cumulative, not block-specific).
First call writes the cache; subsequent calls within ~5 minutes hit it for
~10% of the original input-token cost.

Usage:
    system_blocks = build_system_blocks([SYSTEM_PROMPT, STRATEGY_CATALOG])
    user_content = build_user_content(
        static_blocks=[FORMAT_INSTRUCTIONS, history_text],
        fresh_blocks=[current_snapshot_text],
    )
    response = client.messages.create(
        model=...,
        system=system_blocks,
        messages=[{"role": "user", "content": user_content}],
    )

The static parts get cached after the first call; only `current_snapshot_text`
is freshly billed on each subsequent call within the cache window.
"""
from __future__ import annotations


def text_block(text: str) -> dict:
    """A plain (uncached) text content block."""
    return {"type": "text", "text": text}


def cached_text_block(text: str) -> dict:
    """A text content block marked for ephemeral caching."""
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }


def build_system_blocks(static_parts: list[str]) -> list[dict]:
    """Build a system content-block list. The LAST block is cache-marked
    (so everything in `static_parts` is cached cumulatively).

    If `config.PROPOSER_CACHE_ENABLED` is False, returns plain text blocks
    without cache_control. If `static_parts` is empty, returns an empty list.
    """
    from bullbot import config

    if not static_parts:
        return []

    if not config.PROPOSER_CACHE_ENABLED:
        return [text_block(p) for p in static_parts]

    # Mark only the last block; cache covers everything up to and including it.
    blocks = [text_block(p) for p in static_parts[:-1]]
    blocks.append(cached_text_block(static_parts[-1]))
    return blocks


def build_user_content(
    static_blocks: list[str],
    fresh_blocks: list[str],
) -> list[dict]:
    """Build a user-message content list with a cached static prefix and an
    uncached fresh suffix.

    The last block in `static_blocks` is cache-marked; `fresh_blocks` are
    always plain. If caching is disabled, all blocks are plain.
    """
    from bullbot import config

    static_part = build_system_blocks(static_blocks) if config.PROPOSER_CACHE_ENABLED \
        else [text_block(s) for s in static_blocks]
    fresh_part = [text_block(f) for f in fresh_blocks]
    return static_part + fresh_part
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_llm_cache.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add bullbot/llm/__init__.py bullbot/llm/cache.py tests/unit/test_llm_cache.py
git commit -m "llm/cache: add ephemeral-cache content-block builders for Anthropic API"
```

---

## Task 3: Refactor proposer to use cached content blocks

**Files:**
- Modify: `bullbot/evolver/proposer.py` (replace the `client.messages.create` call to use structured content blocks)
- Modify: `tests/unit/test_evolver_proposer.py` (assert cache markers reach the client)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_evolver_proposer.py`:

```python
def test_proposer_uses_cached_system_blocks_when_enabled(
    fake_anthropic, sample_indicators, sample_key_levels, monkeypatch
):
    """When PROPOSER_CACHE_ENABLED=True, the system kwarg is a list of content
    blocks where the last block has cache_control."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_CACHE_ENABLED", True)

    from bullbot.evolver import proposer
    from bullbot.strategies.base import StrategySnapshot

    fake_anthropic.queue_response(
        '{"class_name": "PutCreditSpread", '
        '"params": {"dte": 21, "short_delta": 0.30, "width": 5}, '
        '"rationale": "test"}'
    )

    snap = StrategySnapshot(
        ticker="SPY", asof_ts=0, spot=500.0, bars_1d=[],
        indicators={}, atm_greeks={}, iv_rank=50.0,
        regime="up_low_vix", chain=[], market_brief="", ticker_brief="",
    )

    proposer.propose(fake_anthropic, snap, history=[], best_strategy_id=None)

    # The fake client logs each call; inspect the system kwarg
    call = fake_anthropic.call_log[-1]
    system = call["system"]
    assert isinstance(system, list), "system should be a list of blocks when cached"
    assert len(system) >= 1
    # Last block is cache-marked
    assert system[-1].get("cache_control") == {"type": "ephemeral"}


def test_proposer_uses_string_system_when_caching_disabled(
    fake_anthropic, monkeypatch
):
    """When PROPOSER_CACHE_ENABLED=False, fall back to plain string system arg
    (backward-compatible with the original implementation)."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_CACHE_ENABLED", False)

    from bullbot.evolver import proposer
    from bullbot.strategies.base import StrategySnapshot

    fake_anthropic.queue_response(
        '{"class_name": "PutCreditSpread", '
        '"params": {"dte": 21, "short_delta": 0.30, "width": 5}, '
        '"rationale": "test"}'
    )

    snap = StrategySnapshot(
        ticker="SPY", asof_ts=0, spot=500.0, bars_1d=[],
        indicators={}, atm_greeks={}, iv_rank=50.0,
        regime="up_low_vix", chain=[], market_brief="", ticker_brief="",
    )

    proposer.propose(fake_anthropic, snap, history=[], best_strategy_id=None)

    call = fake_anthropic.call_log[-1]
    # Either a plain string (legacy path) or a list of plain blocks (no cache_control)
    system = call["system"]
    if isinstance(system, list):
        for block in system:
            assert "cache_control" not in block
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_evolver_proposer.py -v -k cached`
Expected: FAIL — current proposer passes `system` as a plain string.

- [ ] **Step 3: Refactor `propose()` to use cached blocks**

In `bullbot/evolver/proposer.py`, find the `propose()` function (line ~231) and replace its call to `client.messages.create` with a version that uses the new cache helper.

Replace these lines (around line 251-268):

```python
    guidance = _GROWTH_GUIDANCE if category == "growth" else _INCOME_GUIDANCE
    system_prompt = _SYSTEM_PROMPT.format(
        strategy_names=", ".join(registry.list_all_names())
    ) + guidance
    user_prompt = build_user_prompt(snapshot, history, best_strategy_id)

    total_input_tokens = 0
    total_output_tokens = 0
    parsed: dict | None = None

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=PROPOSER_MODEL,
                max_tokens=PROPOSER_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
```

With:

```python
    from bullbot.llm import cache as llm_cache
    from bullbot import config as bb_config

    guidance = _GROWTH_GUIDANCE if category == "growth" else _INCOME_GUIDANCE
    system_prompt = _SYSTEM_PROMPT.format(
        strategy_names=", ".join(registry.list_all_names())
    ) + guidance
    user_prompt = build_user_prompt(snapshot, history, best_strategy_id)

    # Build cached/uncached system arg per config
    if bb_config.PROPOSER_CACHE_ENABLED:
        system_arg = llm_cache.build_system_blocks([system_prompt])
    else:
        system_arg = system_prompt

    total_input_tokens = 0
    total_output_tokens = 0
    parsed: dict | None = None

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=PROPOSER_MODEL,
                max_tokens=PROPOSER_MAX_TOKENS,
                system=system_arg,
                messages=[{"role": "user", "content": user_prompt}],
            )
```

(For Phase 1 we only cache the system block. The user-message body still goes through as a plain string — it's per-ticker per-iteration content that doesn't repeat. Phase 3 will refactor the user message into static-history + fresh-snapshot blocks for cross-iteration caching once iterations-per-tick > 1.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_evolver_proposer.py -v`
Expected: all pass (including the 2 new cache tests).

- [ ] **Step 5: Run full unit suite**

Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: full suite passes (no regressions).

- [ ] **Step 6: Commit**

```bash
git add bullbot/evolver/proposer.py tests/unit/test_evolver_proposer.py
git commit -m "evolver/proposer: cache system prompt via ephemeral cache_control blocks"
```

---

## Task 4: Skip ticker briefs for retired tickers

**Files:**
- Modify: `bullbot/scheduler.py` (the `_refresh_regime` function — its per-ticker loop)
- Modify: `tests/integration/test_regime_scheduler.py` (or `tests/unit/test_regime_agent.py` — wherever tests for `_refresh_regime` live; check both)

- [ ] **Step 1: Find the existing test file**

Run: `grep -rln "_refresh_regime\|refresh_regime\|refresh_ticker_brief" tests/`

Identify the file that tests `scheduler._refresh_regime`. It's likely `tests/integration/test_regime_scheduler.py` or `tests/integration/test_regime_integration.py`.

- [ ] **Step 2: Write the failing test**

Append to that test file (adjust the imports to match the file's existing patterns):

```python
def test_refresh_regime_skips_retired_tickers(db_conn, fake_anthropic, monkeypatch):
    """Retired tickers (phase=no_edge or killed) shouldn't get briefs generated
    when SKIP_BRIEFS_FOR_RETIRED is True."""
    from bullbot import config, scheduler
    monkeypatch.setattr(config, "SKIP_BRIEFS_FOR_RETIRED", True)
    monkeypatch.setattr(config, "UNIVERSE", ["SPY", "AAPL"])

    # Seed: SPY active, AAPL retired
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'discovering', 0)"
    )
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('AAPL', 'no_edge', 0)"
    )

    # Seed minimal bars so regime computation can proceed
    base_ts = 1_700_000_000
    for ticker in ("SPY", "AAPL"):
        for i in range(60):
            db_conn.execute(
                "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
                "VALUES (?, '1d', ?, 100, 101, 99, 100, 1000000)",
                (ticker, base_ts + i * 86400),
            )

    # Queue enough fake responses for whatever briefs are generated
    for _ in range(20):
        fake_anthropic.queue_response("brief text")

    scheduler._refresh_regime(db_conn, fake_anthropic)

    # Inspect call_log: ticker_brief calls include the ticker in the prompt.
    # AAPL should NOT appear in any prompt; SPY may.
    aapl_brief_calls = [
        c for c in fake_anthropic.call_log
        if "AAPL" in str(c.get("messages", ""))
    ]
    assert aapl_brief_calls == [], (
        "AAPL is retired but its brief was still generated"
    )


def test_refresh_regime_does_not_skip_when_flag_disabled(db_conn, fake_anthropic, monkeypatch):
    """When SKIP_BRIEFS_FOR_RETIRED=False, retired tickers still get briefs (legacy behavior)."""
    from bullbot import config, scheduler
    monkeypatch.setattr(config, "SKIP_BRIEFS_FOR_RETIRED", False)
    monkeypatch.setattr(config, "UNIVERSE", ["AAPL"])

    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('AAPL', 'no_edge', 0)"
    )
    base_ts = 1_700_000_000
    for i in range(60):
        db_conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES (?, '1d', ?, 100, 101, 99, 100, 1000000)",
            ("AAPL", base_ts + i * 86400),
        )
    for _ in range(20):
        fake_anthropic.queue_response("brief text")

    scheduler._refresh_regime(db_conn, fake_anthropic)

    # AAPL brief SHOULD have been attempted
    aapl_brief_calls = [
        c for c in fake_anthropic.call_log
        if "AAPL" in str(c.get("messages", ""))
    ]
    assert len(aapl_brief_calls) >= 1
```

- [ ] **Step 3: Run to verify the skip test fails**

Run: `.venv/bin/python -m pytest <path-to-test-file>::test_refresh_regime_skips_retired_tickers -v`
Expected: FAIL — current code refreshes briefs for all UNIVERSE tickers regardless of phase.

- [ ] **Step 4: Add the skip logic to `_refresh_regime`**

In `bullbot/scheduler.py`, find the per-ticker loop in `_refresh_regime` (line ~64):

```python
    # --- Per-ticker briefs ---
    for ticker in config.UNIVERSE:
        try:
            ticker_bars = _load_bars_for_ticker(conn, ticker)
            ...
```

Insert a phase check inside the loop, BEFORE `_load_bars_for_ticker`:

```python
    # --- Per-ticker briefs ---
    for ticker in config.UNIVERSE:
        try:
            # Skip retired tickers per config flag
            if config.SKIP_BRIEFS_FOR_RETIRED:
                phase_row = conn.execute(
                    "SELECT phase FROM ticker_state WHERE ticker=?", (ticker,)
                ).fetchone()
                if phase_row and phase_row["phase"] in ("no_edge", "killed"):
                    log.debug("scheduler: skipping brief for retired ticker %s", ticker)
                    continue
            ticker_bars = _load_bars_for_ticker(conn, ticker)
            ...
```

- [ ] **Step 5: Run both new tests**

Run: `.venv/bin/python -m pytest <path-to-test-file>::test_refresh_regime_skips_retired_tickers -v`
Expected: PASS.

Run: `.venv/bin/python -m pytest <path-to-test-file>::test_refresh_regime_does_not_skip_when_flag_disabled -v`
Expected: PASS.

- [ ] **Step 6: Run full unit + integration suite**

Run: `.venv/bin/python -m pytest tests/unit/ tests/integration/ -q`
Expected: all pass (no regressions).

- [ ] **Step 7: Commit**

```bash
git add bullbot/scheduler.py <path-to-test-file>
git commit -m "scheduler: skip ticker briefs for retired tickers (no_edge / killed)"
```

---

## Task 5: Wire-up integration test (full tick under cache mode)

**Files:**
- Create: `tests/integration/test_phase1_caching.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_phase1_caching.py`:

```python
"""Integration test: verify Phase 1 wiring — proposer system arg is structured,
retired ticker briefs are skipped — within a full scheduler.tick().
"""
from __future__ import annotations

import sqlite3

import pytest

from bullbot import config


def test_full_tick_uses_cached_system_blocks_for_proposer(
    db_conn, fake_anthropic, fake_uw, monkeypatch, tmp_path
):
    """Full tick() under PROPOSER_CACHE_ENABLED=True should pass cache-marked
    blocks to every proposer call."""
    monkeypatch.setattr(config, "PROPOSER_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "SKIP_BRIEFS_FOR_RETIRED", True)
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(config, "UNIVERSE", ["SPY"])

    # Seed minimal state: one discovering ticker
    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, iteration_count, updated_at) "
        "VALUES ('SPY', 'discovering', 0, 0)"
    )

    # Seed bars (60 days)
    base_ts = 1_700_000_000
    for i in range(60):
        db_conn.execute(
            "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
            "VALUES ('SPY', '1d', ?, 500, 502, 498, 500, 1000000)",
            (base_ts + i * 86400,),
        )

    # Queue brief + proposer responses
    for _ in range(10):
        fake_anthropic.queue_response("brief text")
    fake_anthropic.queue_response(
        '{"class_name": "PutCreditSpread", '
        '"params": {"dte": 21, "short_delta": 0.30, "width": 5, '
        '"profit_target_pct": 0.5, "stop_loss_mult": 2.0, "min_dte_close": 7}, '
        '"rationale": "test"}'
    )

    from bullbot import scheduler
    scheduler.tick(db_conn, fake_anthropic, fake_uw)

    # Find the proposer call (it's the one with PROPOSER_MODEL or with system arg
    # containing the strategy catalog)
    proposer_calls = [
        c for c in fake_anthropic.call_log
        if isinstance(c.get("system"), list)
        and any("class_name" in str(b.get("text", "")) for b in c["system"])
    ]
    assert len(proposer_calls) >= 1, "proposer was not invoked"

    # The proposer call's system must be a list of blocks with cache_control on the last
    call = proposer_calls[0]
    system = call["system"]
    assert isinstance(system, list)
    assert system[-1].get("cache_control") == {"type": "ephemeral"}
```

- [ ] **Step 2: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_phase1_caching.py -v`
Expected: PASS.

If FAIL: debug — likely the FakeAnthropicClient call_log structure doesn't match the assumption, or scheduler.tick takes additional args. Inspect with `print(fake_anthropic.call_log)` and adjust accordingly.

- [ ] **Step 3: Run full integration suite**

Run: `.venv/bin/python -m pytest tests/integration/ -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_phase1_caching.py
git commit -m "tests: add Phase 1 integration test for cached proposer + retired-ticker skip"
```

---

## Task 6: Deploy + observe cache hit rate on tomorrow's run

**Files:** None (operational)

- [ ] **Step 1: Push the feature branch**

```bash
git push origin feature/phase1-caching
```

- [ ] **Step 2: Merge to main and push**

```bash
git checkout main
git merge --ff-only feature/phase1-caching
git push origin main
```

- [ ] **Step 3: Pull on pasture and run the test suite**

```bash
ssh pasture 'cd ~/Projects/bull-bot && git pull origin main && .venv/bin/python -m pytest tests/unit/ tests/integration/ -q | tail -3'
```
Expected: full suite passes on pasture.

- [ ] **Step 4: Verify retired-ticker briefs are skipped**

Run a manual one-shot tick on pasture and watch the log:

```bash
ssh pasture 'cd ~/Projects/bull-bot && .venv/bin/python -m bullbot.cli run-daily 2>&1 | grep -E "skipping brief|ticker brief synthesized" | head -20'
```
Expected: no `ticker brief synthesized` for the 8 `no_edge` tickers; instead `skipping brief for retired ticker <X>` (at debug level — may need to bump log level to see).

To be sure, count the brief lines:
```bash
ssh pasture 'cd ~/Projects/bull-bot && grep -c "ticker brief synthesized" logs/bullbot.daily.stderr.log | tail -5'
```

The latest run should show ~8 brief lines (5 discovering + 5 paper_trial + 3 = 8 active tickers, or similar — not 16).

- [ ] **Step 5: After tomorrow's 07:30 run, confirm cache markers in API requests**

There's no easy way to observe Anthropic's cache hit rate from outside, but the `cost_ledger` should show a cost shape change. The proposer LLM cost per call should drop modestly (system prompt is small, ~350 tokens, and the 2nd-Nth proposer call within a tick will hit the cache).

Compare day-over-day:
```bash
ssh pasture 'cd ~/Projects/bull-bot && sqlite3 cache/bullbot.db "SELECT date(ts,\"unixepoch\") AS day, ROUND(SUM(amount_usd),4) AS cost FROM cost_ledger WHERE category=\"llm\" AND ts > strftime(\"%s\",\"now\",\"-3 days\") GROUP BY day ORDER BY day;"'
```
Expected: today's cost ≤ yesterday's, modulo any change in number of evolver iterations.

- [ ] **Step 6: No commit needed — deploy is stateless**

---

## Self-review

**Spec coverage (Phase 1 only):**

| Spec requirement (Phase 1) | Task |
|---|---|
| Prompt caching helper module | Task 2 (`bullbot/llm/cache.py`) |
| Mark static system blocks as ephemeral | Task 2 + Task 3 |
| Proposer uses cached blocks when enabled | Task 3 |
| Backward-compat: caching disabled still works | Task 3 (string fallback) |
| Skip ticker_briefs for retired tickers | Task 4 |
| Both behaviors gated by config | Task 1 (knobs) + Tasks 3, 4 (gates) |
| Tests covering both enabled/disabled paths | Tasks 2, 3, 4 |
| Integration test for full tick under Phase 1 | Task 5 |
| Deploy + observe | Task 6 |

Phases 2-5 are explicitly NOT in this plan and will be planned separately.

**Placeholder scan:** No "TBD" / "TODO" / "fill in details" / "handle edge cases" without specifics. One callout: Task 4 has a "find the test file" step rather than a hardcoded path because the test for `_refresh_regime` could be in either `tests/integration/test_regime_scheduler.py` or `tests/integration/test_regime_integration.py` and I'd rather the implementer verify than guess.

**Type / signature consistency:**
- `cache.text_block(text: str) -> dict` and `cache.cached_text_block(text: str) -> dict` — used consistently.
- `cache.build_system_blocks(static_parts: list[str]) -> list[dict]` — used in Task 2 tests and Task 3 production code.
- `config.PROPOSER_CACHE_ENABLED` — defined in Task 1, read in Tasks 2/3.
- `config.SKIP_BRIEFS_FOR_RETIRED` — defined in Task 1, read in Task 4.
- `_refresh_regime` skip logic uses `phase IN ('no_edge', 'killed')` — matches existing schema constraint.

**Open follow-ups (deferred to later phases):**
- The user-message content is still passed as a plain string in this phase. Phase 3 (batched proposals) will refactor it into static-history-blocks + fresh-snapshot-blocks.
- Cache hit rate metric isn't exposed in our cost_ledger — would need to inspect Anthropic response headers if surfaced. For Phase 1 we accept "indirect" observability via aggregate cost trend.
- The `regime_agent.refresh_market_brief` and `refresh_ticker_brief` calls aren't yet refactored to use cached blocks. Not strictly necessary for Phase 1 (their cost is small, $0.005 each), but Phase 2 or Phase 3 can add this if A/B harness shows headroom.
