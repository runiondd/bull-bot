# Bull-Bot v2 Phase C.6 — Pasture deploy + verify live — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Tasks marked PASTURE-TOUCHING require explicit user go-ahead before SSH actions — do not auto-execute.**

**Goal:** Wire C.5's `runner_c.run_once_phase_c` into the daily CLI entry so the existing `com.bullbot.v2-daily` launchd job fires Phase C (vehicle pick → validate → open → MtM) every morning. Deploy to pasture (Mac mini). Verify via smoke run + dashboard regen + monitor first automated fire.

**Architecture:** No new files. `bullbot/cli.py:cmd_run_v2_daily` currently calls `bullbot.v2.runner.run_once(conn)` (Phase A signal pass). Extend it to *also* call `bullbot.v2.runner_c.run_once_phase_c(conn=conn, asof_ts=int(time.time()))`. Phase A populates `directional_signals` first; Phase C consumes them. One CLI command, two runners.

**Tech Stack:** No new deps. Touches `bullbot/cli.py` (1 file), pasture launchd reload, pasture DB schema apply, pasture dashboard regen.

**Spec reference:** [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md`](../specs/2026-05-16-phase-c-vehicle-agent-design.md) §7 ("C.6 — ship to pasture + verify live").

---

## Pre-flight context

- **Pasture**: Mac mini at SSH alias `pasture` (HostName Daniels-macBook-Pro-2.local, user danielrunion, LAN IP 192.168.1.220). Bull-bot repo at `~/Projects/bull-bot`.
- **Pasture Python**: `.venv/bin/python` explicitly. Non-interactive SSH PATH has no Homebrew; system `python3` is too old.
- **Existing launchd jobs**:
  - `com.bullbot.v2-daily` — fires at 07:35 local, runs `bullbot.cli run-v2-daily` → currently `runner.run_once`. **This is the one we extend.**
  - `com.bullbot.daily` — Phase A/B daily run (older path).
  - `com.bullbot.dashboard` — serves static HTML on port 8080.
- **Existing schema on pasture**: needs `apply_schema` run to pick up C.4b's `backtest_llm_cache` table (added 2026-05-17). All other Phase C tables (`v2_positions`, `v2_position_legs`, `v2_position_events`, `v2_position_mtm`, `v2_chain_snapshots`) already exist from C.0.
- **Anthropic spend**: First smoke run will hit Haiku ~once per UNIVERSE ticker on flat tickers. UNIVERSE is ~10-15 tickers. Estimated cost ≤ $0.10 per run. Daily cron at 07:35 will repeat this daily.
- **Reports dir**: `~/Projects/bull-bot/reports/` exists on pasture (used by `com.bullbot.daily` for nightly markdown). C.5's `v2_backtest_latest` will look there for `backtest_*` subdirs; none exist yet (no backtest has been run on pasture). Empty-state in dashboard is expected.

---

## File Structure

| Path | Responsibility | Status |
|---|---|---|
| `bullbot/cli.py` | Extend `cmd_run_v2_daily` to also call `runner_c.run_once_phase_c`. | **Modify** |
| `tests/unit/test_cli_run_v2_daily.py` | New CLI smoke test (calls both runners). | **Create** |
| Pasture launchd | Reload `com.bullbot.v2-daily.plist` after `git pull`. | **Pasture action** |
| Pasture DB | Run `apply_schema` for `backtest_llm_cache` table. | **Pasture action** |
| Pasture dashboard | Regen by running `com.bullbot.daily` (or wait for its 07:35 fire). | **Pasture action** |

---

## Task 1: Extend `cmd_run_v2_daily` to call `run_once_phase_c` (LOCAL)

**Files:**
- Modify: `bullbot/cli.py:111-122` (`cmd_run_v2_daily`)
- Create: `tests/unit/test_cli_run_v2_daily.py`

After `runner.run_once(conn)` completes (Phase A signals written), call `runner_c.run_once_phase_c(conn=conn, asof_ts=int(time.time()))` (Phase C dispatch). Log the action counts dict. Continue past Phase C exceptions (Phase A side already wrote signals; Phase C failure must not unwind that).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_run_v2_daily.py`:

```python
"""Smoke test for `bullbot.cli run-v2-daily` — ensures both runner.run_once
(Phase A) and runner_c.run_once_phase_c (Phase C) are invoked."""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_cmd_run_v2_daily_invokes_both_runners(monkeypatch):
    """Single CLI call must hit Phase A signal runner AND Phase C dispatcher."""
    from bullbot import cli

    a_called = {"n": 0}
    c_called = {"n": 0}

    def fake_a_run_once(conn):
        a_called["n"] += 1
        return 5

    def fake_c_run_once_phase_c(*, conn, asof_ts, **kwargs):
        c_called["n"] += 1
        return {"pass": 3, "opened": 1}

    monkeypatch.setattr("bullbot.v2.runner.run_once", fake_a_run_once)
    monkeypatch.setattr("bullbot.v2.runner_c.run_once_phase_c", fake_c_run_once_phase_c)

    # Use an in-memory DB; bypass _open_db
    import sqlite3
    from bullbot.db.migrations import apply_schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_schema(conn)
    monkeypatch.setattr(cli, "_open_db", lambda: conn)

    rc = cli.cmd_run_v2_daily(args=None)
    assert rc == 0
    assert a_called["n"] == 1
    assert c_called["n"] == 1


def test_cmd_run_v2_daily_returns_zero_even_when_phase_c_raises(monkeypatch):
    """Phase A success must not be undone by Phase C exceptions."""
    from bullbot import cli

    def fake_a_run_once(conn):
        return 5

    def fake_c_boom(*, conn, asof_ts, **kwargs):
        raise RuntimeError("anthropic 500")

    monkeypatch.setattr("bullbot.v2.runner.run_once", fake_a_run_once)
    monkeypatch.setattr("bullbot.v2.runner_c.run_once_phase_c", fake_c_boom)

    import sqlite3
    from bullbot.db.migrations import apply_schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_schema(conn)
    monkeypatch.setattr(cli, "_open_db", lambda: conn)

    rc = cli.cmd_run_v2_daily(args=None)
    assert rc == 0  # Phase A already succeeded; CLI returns 0
```

- [ ] **Step 2: Run failing**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_cli_run_v2_daily.py -v`
Expected: FAIL — the second test fails because current `cmd_run_v2_daily` does NOT call Phase C. First test fails because second runner isn't invoked.

- [ ] **Step 3: Edit `bullbot/cli.py`**

In `cmd_run_v2_daily` (lines 111-122), replace the existing body with:

```python
def cmd_run_v2_daily(args):
    """v2 daily entry point — emit DirectionalSignal per UNIVERSE ticker
    (Phase A), then dispatch the Phase C agent loop (vehicle pick → validate
    → open → MtM)."""
    from bullbot.v2 import runner, runner_c

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("bullbot.cli.run_v2_daily")

    conn = _open_db()
    n = runner.run_once(conn)
    log.info("run-v2-daily: wrote %d signals", n)
    conn.commit()

    try:
        counts = runner_c.run_once_phase_c(conn=conn, asof_ts=int(time.time()))
        log.info("run-v2-daily: phase C counts %s", counts)
    except Exception:
        log.exception("run-v2-daily: phase C dispatcher failed (Phase A already persisted)")

    return 0
```

- [ ] **Step 4: Run tests pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_cli_run_v2_daily.py -v`
Expected: 2 pass.

- [ ] **Step 5: Run full unit suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit -q`
Expected: 835 + 2 = 837 pass (no regressions).

- [ ] **Step 6: Commit**

```bash
git add bullbot/cli.py tests/unit/test_cli_run_v2_daily.py
git commit -m "feat(v2/c6): CLI run-v2-daily now invokes Phase C dispatcher after Phase A"
```

---

## Task 2: Merge to main (LOCAL)

After T1 passes:

- [ ] **Step 1: Push branch + merge to main**

```bash
git -C /Users/danield.runion/Projects/bull-bot merge --no-ff claude/v2-phase-c6-pasture -m "Merge branch 'claude/v2-phase-c6-pasture' — Phase C.6 CLI wiring"
```

- [ ] **Step 2: Verify tests on main**

```bash
cd /Users/danield.runion/Projects/bull-bot && /Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit -q
```

Expected: 837/837.

- [ ] **Step 3: Cleanup worktree**

```bash
git worktree remove /Users/danield.runion/Projects/bull-bot/.claude/worktrees/c6-pasture-deploy --force
git -C /Users/danield.runion/Projects/bull-bot branch -d claude/v2-phase-c6-pasture
```

---

## Task 3: Pasture — git pull + apply schema (PASTURE-TOUCHING — get user OK first)

**Requires user confirmation.** Touches prod DB schema (additive, but real).

- [ ] **Step 1: SSH + git pull**

```bash
ssh pasture 'cd ~/Projects/bull-bot && git pull --ff-only origin main 2>&1'
```

Expected: prints "Updating <sha>..<sha>" and lists files changed (runner_c.py, report.py, runner.py [backtest], etc.).

- [ ] **Step 2: Apply schema (idempotent)**

```bash
ssh pasture '.venv/bin/python -c "import sqlite3; from bullbot.db.migrations import apply_schema; c = sqlite3.connect(\"cache/bullbot.db\"); apply_schema(c); c.commit(); print(\"schema applied\")"'
```

Expected: prints `schema applied`. New table `backtest_llm_cache` appears.

- [ ] **Step 3: Verify schema delta**

```bash
ssh pasture '.venv/bin/python -c "import sqlite3; c = sqlite3.connect(\"cache/bullbot.db\"); rows = c.execute(\"SELECT name FROM sqlite_master WHERE type=\\\"table\\\" AND name LIKE \\\"backtest_%\\\" OR name LIKE \\\"v2_%\\\"\").fetchall(); [print(r[0]) for r in rows]"'
```

Expected: `backtest_llm_cache` + the 5 v2_ tables.

---

## Task 4: Pasture — manual smoke run (PASTURE-TOUCHING — costs ~$0.10 in Anthropic credits)

- [ ] **Step 1: Run the CLI once manually**

```bash
ssh pasture 'cd ~/Projects/bull-bot && .venv/bin/python -m bullbot.cli run-v2-daily 2>&1 | tail -40'
```

Expected output last lines: `run-v2-daily: wrote N signals` (Phase A) and `run-v2-daily: phase C counts {'pass': N, ...}` (Phase C).

If `phase C counts` line is missing → Phase C dispatcher silently failed; check stderr log.

- [ ] **Step 2: Verify Phase C wrote something**

```bash
ssh pasture '.venv/bin/python -c "import sqlite3; c = sqlite3.connect(\"cache/bullbot.db\"); print(\"open positions:\", c.execute(\"SELECT COUNT(*) FROM v2_positions WHERE closed_ts IS NULL\").fetchone()[0]); print(\"mtm rows today:\", c.execute(\"SELECT COUNT(*) FROM v2_position_mtm WHERE asof_ts > strftime(\\\"%s\\\", \\\"now\\\", \\\"-1 day\\\")\").fetchone()[0])"'
```

Expected: open positions count >= 0 (could be 0 if all tickers passed). MtM rows count = open positions count (one MtM row per open position from today's run).

If Phase C opened a position, also check `v2_positions.opened_ts >= today's morning`.

---

## Task 5: Pasture — regen dashboard + verify new tabs (PASTURE-TOUCHING)

- [ ] **Step 1: Manually regenerate dashboard**

```bash
ssh pasture 'cd ~/Projects/bull-bot && .venv/bin/python -m bullbot.cli generate-dashboard 2>&1 | tail -10'
```

(If `generate-dashboard` is not the exact CLI subcommand, check `bullbot/cli.py` for the right one; pasture launchd job uses it via `com.bullbot.daily`.)

Expected: dashboard HTML file regenerated; no traceback.

- [ ] **Step 2: Verify tabs render**

```bash
ssh pasture 'grep -c "V2 Positions\|V2 Backtest" ~/Projects/bull-bot/dashboard/index.html 2>/dev/null || grep -c "V2 Positions\|V2 Backtest" ~/Projects/bull-bot/reports/dashboard.html 2>/dev/null'
```

(Wherever the dashboard HTML lives; check `generator.py` for output path.)

Expected: ≥2 (one match for each tab nav label).

- [ ] **Step 3: Visual verification (manual, Dan)**

Dan opens dashboard at `http://pasture:8080/` (or whatever the dashboard URL is post-Starlink migration) and:
1. Clicks **V2 Positions** tab — sees empty state ("No open positions") OR a row per open position.
2. Clicks **V2 Backtest** tab — sees empty state ("No backtest report yet") since no backtest run on pasture.

---

## Task 6: Wait + verify next automated daily fire (24h observation)

- [ ] **Step 1: Wait until next 07:35 local fire**

The `com.bullbot.v2-daily` launchd job fires at 07:35 daily. After waiting (or trigger manually for testing):

```bash
ssh pasture 'launchctl list | grep com.bullbot.v2-daily'
```

Expected: LastExitCode = 0.

- [ ] **Step 2: Inspect logs from the run**

```bash
ssh pasture 'tail -50 ~/Projects/bull-bot/logs/bullbot.v2-daily.stdout.log'
```

Expected: includes both `run-v2-daily: wrote N signals` AND `run-v2-daily: phase C counts {...}` lines from today.

- [ ] **Step 3: Confirm MtM rows + dashboard reflect today's run**

```bash
ssh pasture '.venv/bin/python -c "import sqlite3, time; c = sqlite3.connect(\"cache/bullbot.db\"); rows = c.execute(\"SELECT position_id, asof_ts, mtm_value, source FROM v2_position_mtm WHERE asof_ts > ? ORDER BY asof_ts DESC LIMIT 10\", (int(time.time()) - 86400,)).fetchall(); [print(r) for r in rows]"'
```

Expected: rows from today's run.

- [ ] **Step 4: Marker commit (LOCAL)**

```bash
cd /Users/danield.runion/Projects/bull-bot && git commit --allow-empty -m "chore(v2/c6): Phase C.6 complete — runner_c live on pasture, verified"
```

---

## Acceptance criteria

C.6 complete when ALL of the following hold:

1. `bullbot/cli.py:cmd_run_v2_daily` calls both `runner.run_once` and `runner_c.run_once_phase_c`. Tests pass.
2. Branch merged to main; 837 unit + 80 integration tests still pass.
3. Pasture has the latest code (`git pull --ff-only` clean) and the latest schema (`apply_schema` clean).
4. Manual smoke run on pasture completes without traceback; logs show both Phase A + Phase C lines; `v2_position_mtm` table has rows from today.
5. Dashboard tabs `V2 Positions` and `V2 Backtest` render on pasture (Dan visual confirms).
6. Next-day automated 07:35 launchd fire completes with LastExitCode=0 and logs show both phases ran.

## What this completes

Phase C ship-readiness — Dan can open the dashboard tomorrow and see the agent's daily picks + MtM. Deferred follow-ups carried into Phase D backlog:
- LLM-cache divergence from full-prompt sha (vehicle prompt template change risk)
- Hardcoded risk caps (move to config)
- Earnings calendar wiring (currently days_to_earnings=999)
- Yahoo chain real-fetch validation (Phase C.1 ships fetch_chain; first real-pasture run is the first prod exercise)
- BS-vs-real validation_summary.txt (needs manual chain snapshots)
- PNG equity curve + SPY benchmark overlay (dashboard render polish)

## Notes for the implementer

- **Worktree `.venv` path** (for local T1): `/Users/danield.runion/Projects/bull-bot/.venv/bin/python`.
- **Pasture user / repo path** is `danielrunion` (not `danield.runion`) — different from local user. SSH alias abstracts this away.
- **`generate-dashboard` CLI** — check actual subcommand name in `bullbot/cli.py`; might be `generate-dashboard` or `dashboard-regen` or similar. Pasture's `com.bullbot.daily.plist` shows the canonical invocation.
- **Pasture-touching tasks (T3-T6) require explicit user go-ahead.** The plan is written for autonomous execution but the controller MUST pause and ask Dan before running T3 (touches DB) and T4 (spends real Anthropic credits).
