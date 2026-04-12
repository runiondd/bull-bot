# Exit Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add engine-level position exit logic so the walk-forward backtest produces closed trades with PnL.

**Architecture:** Exit rules (profit target, stop loss, DTE close) are stored per-position at open time. The engine checks these on every bar before calling strategy.evaluate(). Strategies only handle entry; the engine handles exits.

**Tech Stack:** Python, SQLite, Pydantic v2

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `bullbot/data/schemas.py` | Modify | Add 3 exit fields to Signal |
| `bullbot/db/schema.sql` | Modify | Add `exit_rules` column to positions |
| `bullbot/config.py` | Modify | Add 3 default exit constants |
| `bullbot/engine/exit_manager.py` | Create | `check_exits()` function |
| `bullbot/engine/step.py` | Modify | Store exit rules on open, call `check_exits()` before evaluate |
| `bullbot/strategies/put_credit_spread.py` | Modify | Pass exit params onto Signal |
| `bullbot/strategies/call_credit_spread.py` | Modify | Pass exit params onto Signal |
| `bullbot/strategies/iron_condor.py` | Modify | Pass exit params onto Signal |
| `bullbot/strategies/cash_secured_put.py` | Modify | Pass exit params onto Signal |
| `bullbot/strategies/long_call.py` | Modify | Pass exit params onto Signal |
| `bullbot/strategies/long_put.py` | Modify | Pass exit params onto Signal |
| `bullbot/evolver/proposer.py` | Modify | Add exit params to prompt |
| `tests/unit/test_exit_manager.py` | Create | Unit tests for exit logic |
| `tests/integration/test_exit_integration.py` | Create | Integration test: open → exit → PnL |

---

### Task 1: Add Exit Fields to Signal and Schema

**Files:**
- Modify: `bullbot/data/schemas.py:86-94`
- Modify: `bullbot/db/schema.sql:146-159`
- Modify: `bullbot/config.py`

- [ ] **Step 1: Add exit fields to Signal**

In `bullbot/data/schemas.py`, change Signal to:

```python
class Signal(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    intent: Literal["open", "close"]
    strategy_class: str
    legs: list[Leg]
    max_loss_per_contract: float = Field(ge=0)
    rationale: str
    position_id_to_close: int | None = None
    profit_target_pct: float | None = None
    stop_loss_mult: float | None = None
    min_dte_close: int | None = None
```

- [ ] **Step 2: Add exit_rules column to positions table**

In `bullbot/db/schema.sql`, change the positions table to add the `exit_rules` column after `mark_to_mkt`:

```sql
CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY,
    run_id          TEXT    NOT NULL DEFAULT 'live',
    ticker          TEXT    NOT NULL,
    strategy_id     INTEGER REFERENCES strategies (id),
    legs            TEXT,           -- JSON array of leg objects
    contracts       INTEGER NOT NULL DEFAULT 1,
    open_price      REAL    NOT NULL,
    close_price     REAL,
    mark_to_mkt     REAL    NOT NULL DEFAULT 0.0,
    exit_rules      TEXT,           -- JSON: {"profit_target_pct": 0.5, ...}
    opened_at       INTEGER NOT NULL,
    closed_at       INTEGER,
    pnl_realized    REAL
) STRICT;
```

- [ ] **Step 3: Add default exit constants to config**

In `bullbot/config.py`, add after the `MIN_SPREAD_FRAC` line (line 70):

```python
DEFAULT_PROFIT_TARGET_PCT = 0.50
DEFAULT_STOP_LOSS_MULT = 2.0
DEFAULT_MIN_DTE_CLOSE = 7
```

- [ ] **Step 4: Run tests to verify schema change doesn't break existing tests**

Run: `pytest tests/ -x -q`
Expected: 218 passed (existing tests use in-memory DB, schema auto-applied)

- [ ] **Step 5: Commit**

```bash
git add bullbot/data/schemas.py bullbot/db/schema.sql bullbot/config.py
git commit -m "add exit rule fields to Signal, positions schema, and config defaults"
```

---

### Task 2: Create Exit Manager Module

**Files:**
- Create: `bullbot/engine/exit_manager.py`
- Create: `tests/unit/test_exit_manager.py`

- [ ] **Step 1: Write unit tests for exit manager**

Create `tests/unit/test_exit_manager.py`:

```python
"""Tests for engine-level exit manager."""
import json
import sqlite3

import pytest

from bullbot.db import connection as db_connection
from bullbot.engine import exit_manager, fill_model
from bullbot.data.schemas import Leg, OptionContract


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from bullbot.db.migrations import apply_schema
    apply_schema(c)
    return c


def _insert_position(conn, *, run_id="test", ticker="SPY", strategy_id=1,
                      opened_at=1000, legs_json, contracts=1, open_price,
                      exit_rules_json=None):
    conn.execute(
        "INSERT INTO positions (run_id, ticker, strategy_id, opened_at, legs, "
        "contracts, open_price, mark_to_mkt, exit_rules) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, ticker, strategy_id, opened_at, legs_json,
         contracts, open_price, open_price, exit_rules_json),
    )


def _make_chain(short_sym, short_bid, short_ask, long_sym, long_bid, long_ask):
    """Build a chain_rows dict for a 2-leg spread."""
    return {
        short_sym: {"nbbo_bid": short_bid, "nbbo_ask": short_ask},
        long_sym: {"nbbo_bid": long_bid, "nbbo_ask": long_ask},
    }


def _spread_legs_json(short_sym="SPY260620P00670000", long_sym="SPY260620P00665000",
                       short_strike=670.0, long_strike=665.0):
    return json.dumps([
        {"option_symbol": short_sym, "side": "short", "quantity": 1,
         "strike": short_strike, "expiry": "2026-06-20", "kind": "P"},
        {"option_symbol": long_sym, "side": "long", "quantity": 1,
         "strike": long_strike, "expiry": "2026-06-20", "kind": "P"},
    ])


# --- Profit target tests ---

class TestProfitTarget:
    def test_no_exit_below_target(self, conn):
        """50% profit target, spread worth 60% of credit -> no exit (only 40% profit)."""
        legs_json = _spread_legs_json()
        # Credit received: $1.18 per share -> open_price = -118.0
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)

        # Current spread worth ~$0.70 (60% of $1.18) -> only 40% profit, below 50% target
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.65, 0.75,  # short put worth $0.70
            "SPY260620P00665000", 0.25, 0.35,  # long put worth $0.30
        )

        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert closed == []

    def test_exit_at_target(self, conn):
        """50% profit target, spread worth 20% of credit -> 80% profit, fires."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)

        # Short put decayed to $0.15, long put to $0.05 -> spread worth ~$0.10
        # Profit = $1.18 - $0.10 = $1.08, target = 50% of $1.18 = $0.59
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.14, 0.16,
            "SPY260620P00665000", 0.04, 0.06,
        )

        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert len(closed) == 1

        # Verify position was actually closed in DB
        pos = conn.execute("SELECT * FROM positions WHERE id=?", (closed[0],)).fetchone()
        assert pos["closed_at"] == 2000
        assert pos["pnl_realized"] is not None


# --- Stop loss tests ---

class TestStopLoss:
    def test_no_exit_below_stop(self, conn):
        """2x stop, loss at 1.5x credit -> no exit."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)

        # Spread widened to ~$2.95 -> loss = $2.95 - $1.18 = $1.77, stop = 2 * $1.18 = $2.36
        chain_rows = _make_chain(
            "SPY260620P00670000", 2.50, 2.60,
            "SPY260620P00665000", 0.30, 0.40,
        )

        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert closed == []

    def test_exit_at_stop(self, conn):
        """2x stop, loss exceeds 2x credit -> exit fires."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)

        # Spread widened to ~$4.80 -> loss = $4.80 - $1.18 = $3.62, stop = $2.36
        chain_rows = _make_chain(
            "SPY260620P00670000", 4.50, 4.70,
            "SPY260620P00665000", 0.35, 0.45,
        )

        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert len(closed) == 1


# --- DTE close tests ---

class TestDteClose:
    def test_no_exit_above_dte(self, conn):
        """min_dte_close=7, cursor at 10 DTE -> no exit."""
        legs_json = _spread_legs_json()  # expiry 2026-06-20
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)

        # June 10 = 10 DTE from June 20
        cursor_10dte = 1781251200  # 2026-06-10 00:00:00 UTC
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.65, 0.75,
            "SPY260620P00665000", 0.25, 0.35,
        )

        closed = exit_manager.check_exits(conn, "test", "SPY", cursor_10dte, chain_rows)
        assert closed == []

    def test_exit_at_dte(self, conn):
        """min_dte_close=7, cursor at 5 DTE -> exit fires."""
        legs_json = _spread_legs_json()  # expiry 2026-06-20
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)

        # June 15 = 5 DTE from June 20
        cursor_5dte = 1781683200  # 2026-06-15 00:00:00 UTC
        chain_rows = _make_chain(
            "SPY260620P00670000", 0.65, 0.75,
            "SPY260620P00665000", 0.25, 0.35,
        )

        closed = exit_manager.check_exits(conn, "test", "SPY", cursor_5dte, chain_rows)
        assert len(closed) == 1


# --- None rules tests ---

class TestNoneRules:
    def test_no_exit_rules_means_no_exit(self, conn):
        """Position with exit_rules=NULL never exits."""
        legs_json = _spread_legs_json()
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=None)

        chain_rows = _make_chain(
            "SPY260620P00670000", 0.04, 0.06,
            "SPY260620P00665000", 0.01, 0.03,
        )

        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert closed == []

    def test_partial_none_rules(self, conn):
        """Only profit_target set, no stop or DTE -> profit target still works."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)

        chain_rows = _make_chain(
            "SPY260620P00670000", 0.04, 0.06,
            "SPY260620P00665000", 0.01, 0.03,
        )

        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows)
        assert len(closed) == 1


# --- Fill rejected tests ---

class TestFillRejected:
    def test_skip_when_no_chain_data(self, conn):
        """If chain_rows doesn't have the position's legs, skip gracefully."""
        legs_json = _spread_legs_json()
        exit_rules = json.dumps({"profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7})
        _insert_position(conn, legs_json=legs_json, open_price=-118.0,
                          exit_rules_json=exit_rules)

        closed = exit_manager.check_exits(conn, "test", "SPY", 2000, chain_rows={})
        assert closed == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_exit_manager.py -v`
Expected: All tests FAIL with `ModuleNotFoundError: No module named 'bullbot.engine.exit_manager'`

- [ ] **Step 3: Implement exit_manager module**

Create `bullbot/engine/exit_manager.py`:

```python
"""
Engine-level exit manager.

Checks open positions against their stored exit rules on every bar.
Executes closes through the existing fill model when conditions are met.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from bullbot.data.schemas import Leg
from bullbot.engine import fill_model

log = logging.getLogger("bullbot.exit_manager")


def check_exits(
    conn: sqlite3.Connection,
    run_id: str,
    ticker: str,
    cursor: int,
    chain_rows: dict[str, dict[str, Any]],
) -> list[int]:
    """Check all open positions for exit conditions. Returns list of closed position IDs."""
    positions = conn.execute(
        "SELECT * FROM positions WHERE run_id=? AND ticker=? AND closed_at IS NULL",
        (run_id, ticker),
    ).fetchall()

    closed_ids: list[int] = []
    for pos in positions:
        exit_rules_raw = pos["exit_rules"]
        if exit_rules_raw is None:
            continue
        rules = json.loads(exit_rules_raw)
        if not rules:
            continue

        legs = [Leg(**l) for l in json.loads(pos["legs"])]
        reason = _should_exit(pos, legs, rules, cursor, chain_rows)
        if reason is None:
            continue

        try:
            _execute_close(conn, pos, legs, run_id, cursor, chain_rows, reason)
            closed_ids.append(pos["id"])
        except fill_model.FillRejected:
            log.debug("exit fill rejected for position %d: %s", pos["id"], reason)

    return closed_ids


def _should_exit(
    pos: sqlite3.Row,
    legs: list[Leg],
    rules: dict[str, Any],
    cursor: int,
    chain_rows: dict[str, dict[str, Any]],
) -> str | None:
    """Return exit reason string if any condition is met, else None."""
    # --- DTE close (check first: no chain pricing needed) ---
    min_dte = rules.get("min_dte_close")
    if min_dte is not None:
        cursor_date = datetime.fromtimestamp(cursor, tz=timezone.utc).date()
        nearest_expiry = min(
            datetime.strptime(l.expiry, "%Y-%m-%d").date() for l in legs
        )
        dte = (nearest_expiry - cursor_date).days
        if dte <= min_dte:
            return f"dte_close: {dte} DTE <= {min_dte}"

    # --- Price-based exits need current mark ---
    try:
        close_cost, _ = fill_model.simulate_close_multi_leg(
            legs, chain_rows, pos["contracts"],
        )
    except (fill_model.FillRejected, KeyError):
        return None

    open_price = pos["open_price"]
    is_credit = open_price < 0
    credit = abs(open_price)

    if is_credit:
        unrealized_pnl = credit - close_cost
    else:
        unrealized_pnl = -close_cost - open_price

    # --- Stop loss ---
    stop_mult = rules.get("stop_loss_mult")
    if stop_mult is not None:
        max_loss = stop_mult * credit if is_credit else stop_mult * open_price
        if unrealized_pnl < 0 and abs(unrealized_pnl) >= max_loss:
            return f"stop_loss: loss ${abs(unrealized_pnl):.2f} >= {stop_mult}x ${credit:.2f}"

    # --- Profit target ---
    target_pct = rules.get("profit_target_pct")
    if target_pct is not None:
        target_profit = target_pct * credit if is_credit else target_pct * open_price
        if unrealized_pnl >= target_profit:
            return f"profit_target: profit ${unrealized_pnl:.2f} >= {target_pct:.0%} of ${credit:.2f}"

    return None


def _execute_close(
    conn: sqlite3.Connection,
    pos: sqlite3.Row,
    legs: list[Leg],
    run_id: str,
    cursor: int,
    chain_rows: dict[str, dict[str, Any]],
    reason: str,
) -> None:
    """Close a position and record the order."""
    net_close, _ = fill_model.simulate_close_multi_leg(
        legs, chain_rows, pos["contracts"],
    )
    pnl = pos["open_price"] - net_close
    comm = fill_model.commission(pos["contracts"], len(legs))

    conn.execute(
        "UPDATE positions SET closed_at=?, close_price=?, pnl_realized=?, mark_to_mkt=0.0 WHERE id=?",
        (cursor, net_close, pnl - comm, pos["id"]),
    )
    conn.execute(
        "INSERT INTO orders (run_id, ticker, strategy_id, placed_at, legs, intent, status, commission, pnl_realized) "
        "VALUES (?, ?, ?, ?, ?, 'close', 'filled', ?, ?)",
        (run_id, pos["ticker"], pos["strategy_id"], cursor, pos["legs"], comm, pnl - comm),
    )
    log.info("exit_manager closed position %d: %s (pnl=%.2f)", pos["id"], reason, pnl - comm)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_exit_manager.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: 218+ passed

- [ ] **Step 6: Commit**

```bash
git add bullbot/engine/exit_manager.py tests/unit/test_exit_manager.py
git commit -m "add exit manager: check_exits() with profit target, stop loss, DTE close"
```

---

### Task 3: Wire Exit Manager into Engine Step

**Files:**
- Modify: `bullbot/engine/step.py:198-254`

- [ ] **Step 1: Import exit_manager and store exit_rules on open**

In `bullbot/engine/step.py`, add import at top:

```python
from bullbot.engine import exit_manager
```

Then modify the `step()` function. In the `signal.intent == "open"` branch, change the INSERT INTO positions to include exit_rules:

Replace lines 243-249 (the positions INSERT):

```python
        exit_rules = json.dumps({
            k: v for k, v in {
                "profit_target_pct": signal.profit_target_pct,
                "stop_loss_mult": signal.stop_loss_mult,
                "min_dte_close": signal.min_dte_close,
            }.items() if v is not None
        }) or None
        cur = conn.execute(
            "INSERT INTO positions (run_id, ticker, strategy_id, opened_at, legs, contracts, open_price, mark_to_mkt, exit_rules) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, ticker, strategy_id, cursor,
             json.dumps([l.model_dump() for l in signal.legs]),
             contracts, net_cash, net_cash, exit_rules),
        )
```

- [ ] **Step 2: Call check_exits() before strategy.evaluate()**

In `step()`, after building the snapshot and before calling evaluate, add exit checking. Replace lines 208-215:

```python
    snap = _build_snapshot(conn, ticker, cursor)
    if snap is None:
        return StepResult(signal=None, filled=False)

    chain_rows = _build_chain_rows(snap.chain)
    exit_manager.check_exits(conn, run_id, ticker, cursor, chain_rows)

    open_positions = _load_open_positions(conn, run_id, ticker)
    signal = strategy.evaluate(snap, open_positions)
    if signal is None:
        return StepResult(signal=None, filled=False)
```

Note: `chain_rows` is now built once and reused. Remove the duplicate `chain_rows = _build_chain_rows(snap.chain)` line from within the `signal.intent == "open"` block (was line 226). The open block should use the already-built `chain_rows`.

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: 218+ passed

- [ ] **Step 4: Commit**

```bash
git add bullbot/engine/step.py
git commit -m "wire exit manager into engine step: check exits before evaluate, store exit rules"
```

---

### Task 4: Update Strategies to Pass Exit Params

**Files:**
- Modify: `bullbot/strategies/put_credit_spread.py:101-127`
- Modify: `bullbot/strategies/call_credit_spread.py:102-128`
- Modify: `bullbot/strategies/iron_condor.py:121-164`
- Modify: `bullbot/strategies/cash_secured_put.py:75-93`
- Modify: `bullbot/strategies/long_call.py:70-88`
- Modify: `bullbot/strategies/long_put.py:70-88`

All 6 strategies need the same change: read exit params from `self.params` with config defaults, pass them on the Signal.

- [ ] **Step 1: Update PutCreditSpread**

In `bullbot/strategies/put_credit_spread.py`, add import at top:

```python
from bullbot import config
```

(Already imported.) Change the `return Signal(...)` at lines 101-127 to include exit params:

```python
        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[
                Leg(
                    option_symbol=short_option,
                    side="short",
                    quantity=1,
                    strike=best.strike,
                    expiry=chosen_expiry,
                    kind="P",
                ),
                Leg(
                    option_symbol=long_option,
                    side="long",
                    quantity=1,
                    strike=long_leg.strike,
                    expiry=chosen_expiry,
                    kind="P",
                ),
            ],
            max_loss_per_contract=width * 100,
            rationale=(
                f"Short {best.strike}P / Long {long_leg.strike}P {chosen_expiry} "
                f"(width={width}, iv_rank={snapshot.iv_rank:.0f})"
            ),
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=int(self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE)),
        )
```

- [ ] **Step 2: Update CallCreditSpread**

In `bullbot/strategies/call_credit_spread.py`, same change to the `return Signal(...)` at lines 102-128. Add after `rationale`:

```python
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=int(self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE)),
```

Note: `config` is already imported in this file.

- [ ] **Step 3: Update IronCondor**

In `bullbot/strategies/iron_condor.py`, same change to the `return Signal(...)` at lines 121-164. Add after `rationale`:

```python
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=int(self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE)),
```

Note: `config` is already imported in this file.

- [ ] **Step 4: Update CashSecuredPut**

In `bullbot/strategies/cash_secured_put.py`, add `from bullbot import config` at the top (not yet imported). Then change the `return Signal(...)` at lines 75-93 to add after `rationale`:

```python
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=int(self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE)),
```

- [ ] **Step 5: Update LongCall**

In `bullbot/strategies/long_call.py`, add `from bullbot import config` at the top (not yet imported). Then change the `return Signal(...)` at lines 70-88 to add after `rationale`:

```python
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=int(self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE)),
```

- [ ] **Step 6: Update LongPut**

In `bullbot/strategies/long_put.py`, add `from bullbot import config` at the top (not yet imported). Then change the `return Signal(...)` at lines 70-88 to add after `rationale`:

```python
            profit_target_pct=self.params.get("profit_target_pct", config.DEFAULT_PROFIT_TARGET_PCT),
            stop_loss_mult=self.params.get("stop_loss_mult", config.DEFAULT_STOP_LOSS_MULT),
            min_dte_close=int(self.params.get("min_dte_close", config.DEFAULT_MIN_DTE_CLOSE)),
```

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: 218+ passed

- [ ] **Step 8: Commit**

```bash
git add bullbot/strategies/put_credit_spread.py bullbot/strategies/call_credit_spread.py \
    bullbot/strategies/iron_condor.py bullbot/strategies/cash_secured_put.py \
    bullbot/strategies/long_call.py bullbot/strategies/long_put.py
git commit -m "all 6 strategies pass exit params (profit target, stop loss, DTE) onto Signal"
```

---

### Task 5: Update Proposer Prompt

**Files:**
- Modify: `bullbot/evolver/proposer.py:74-92`

- [ ] **Step 1: Update system prompt to include exit params**

In `bullbot/evolver/proposer.py`, change `_SYSTEM_PROMPT` (lines 74-92) to:

```python
_SYSTEM_PROMPT = """You are an expert algorithmic options trader and quantitative researcher.
Your job is to propose a *single* options strategy variant for the Bull-Bot evolver.

You MUST respond with ONLY a valid JSON object — no prose, no markdown, no code fences.
The JSON must have exactly these three keys:

  "class_name"  — one of the registered strategy class names
  "params"      — a flat dict of strategy parameters (all numeric values)
  "rationale"   — 1-3 sentence justification for this proposal

The params dict should include BOTH entry params (dte, delta, width, iv_rank_min, etc.)
AND exit params:
  - profit_target_pct: fraction of max profit to close at (e.g. 0.50 = 50%)
  - stop_loss_mult: multiple of credit/debit to stop at (e.g. 2.0 = 2x loss)
  - min_dte_close: close position at this many days to expiry (e.g. 7)

Registered strategies: {strategy_names}

Example response:
{{
  "class_name": "PutCreditSpread",
  "params": {{"dte": 21, "short_delta": 0.30, "width": 5, "iv_rank_min": 50, "profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7}},
  "rationale": "Selling premium with defined risk. 50% profit target captures theta decay efficiently."
}}
"""
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: 218+ passed

- [ ] **Step 3: Commit**

```bash
git add bullbot/evolver/proposer.py
git commit -m "update proposer prompt to include exit params in strategy proposals"
```

---

### Task 6: Integration Test — Open, Exit, PnL

**Files:**
- Create: `tests/integration/test_exit_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/integration/test_exit_integration.py`:

```python
"""Integration test: open a position, advance cursor, verify exit fires and PnL recorded."""
import json
import sqlite3

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.engine import step as engine_step
from bullbot.strategies.put_credit_spread import PutCreditSpread
from bullbot.data.schemas import Bar, OptionContract


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def _insert_bar(conn, ticker, ts, close, *, open_=None, high=None, low=None, volume=1000):
    open_ = open_ or close
    high = high or close
    low = low or close
    conn.execute(
        "INSERT OR REPLACE INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
        "VALUES (?, '1d', ?, ?, ?, ?, ?, ?)",
        (ticker, ts, open_, high, low, close, volume),
    )


def _insert_option(conn, ticker, expiry, strike, kind, ts, bid, ask, iv=0.20):
    db_kind = "call" if kind == "C" else "put"
    conn.execute(
        "INSERT OR REPLACE INTO option_contracts "
        "(ticker, expiry, strike, kind, ts, bid, ask, iv) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ticker, expiry, strike, db_kind, ts, bid, ask, iv),
    )


def _insert_strategy(conn):
    conn.execute(
        "INSERT INTO strategies (class_name, class_version, params, params_hash, created_at) "
        "VALUES ('PutCreditSpread', 1, '{}', 'test', 1000)"
    )
    return 1


def test_position_opens_then_exits_on_profit_target(conn):
    """Full cycle: bar data + options -> open position -> price decays -> exit fires -> PnL recorded."""
    strategy_id = _insert_strategy(conn)
    strategy = PutCreditSpread({
        "dte": 30, "short_delta": 0.25, "width": 5, "iv_rank_min": 0,
        "profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7,
    })

    # Insert 80 bars of SPY history (need 60 minimum for snapshot)
    base_ts = 1746057600  # 2025-05-01 00:00:00 UTC
    for i in range(80):
        _insert_bar(conn, "SPY", base_ts + i * 86400, 560.0 + i * 0.1)

    # Insert option chain for expiry ~30 days out
    expiry = "2025-07-18"
    open_ts = base_ts + 79 * 86400  # last bar

    # Short put at 545 (OTM), long put at 540
    _insert_option(conn, "SPY", expiry, 545.0, "P", open_ts, 2.15, 2.25, 0.20)
    _insert_option(conn, "SPY", expiry, 540.0, "P", open_ts, 0.95, 1.05, 0.20)
    # Add a few more strikes for delta selection
    for strike in [550.0, 555.0, 560.0, 565.0]:
        _insert_option(conn, "SPY", expiry, strike, "P", open_ts, 3.0, 3.20, 0.20)

    # Step 1: should open a position
    result1 = engine_step.step(
        conn=conn, client=None, cursor=open_ts, ticker="SPY",
        strategy=strategy, strategy_id=strategy_id, run_id="test",
    )
    assert result1.filled, "Position should have opened"

    # Verify position exists
    pos = conn.execute("SELECT * FROM positions WHERE run_id='test' AND closed_at IS NULL").fetchone()
    assert pos is not None
    assert pos["exit_rules"] is not None

    # Step 2: advance cursor, options have decayed (profit target should fire)
    next_ts = open_ts + 20 * 86400
    _insert_bar(conn, "SPY", next_ts, 565.0)

    # Insert decayed option prices (spread worth ~20% of original -> >50% profit)
    legs = json.loads(pos["legs"])
    for leg in legs:
        sym_parts = leg["option_symbol"]
        if leg["side"] == "short":
            _insert_option(conn, "SPY", leg["expiry"], leg["strike"], leg["kind"],
                          next_ts, 0.10, 0.20, 0.15)
        else:
            _insert_option(conn, "SPY", leg["expiry"], leg["strike"], leg["kind"],
                          next_ts, 0.02, 0.08, 0.15)

    result2 = engine_step.step(
        conn=conn, client=None, cursor=next_ts, ticker="SPY",
        strategy=strategy, strategy_id=strategy_id, run_id="test",
    )

    # Position should have been closed by exit manager
    closed_pos = conn.execute("SELECT * FROM positions WHERE run_id='test' AND closed_at IS NOT NULL").fetchone()
    assert closed_pos is not None, "Position should have been closed by exit manager"
    assert closed_pos["pnl_realized"] is not None
    assert closed_pos["pnl_realized"] > 0, "Should be a profitable close"

    # Verify close order exists
    close_order = conn.execute("SELECT * FROM orders WHERE run_id='test' AND intent='close'").fetchone()
    assert close_order is not None
    assert close_order["pnl_realized"] > 0
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/integration/test_exit_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_exit_integration.py
git commit -m "add integration test: position open -> exit manager closes on profit target"
```

---

### Task 7: Reset SPY and Re-run Evolver

**Files:** None (operational)

- [ ] **Step 1: Reset SPY ticker state**

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('cache/bullbot.db')
conn.execute(\"DELETE FROM ticker_state WHERE ticker='SPY'\")
conn.execute(\"DELETE FROM evolver_proposals WHERE ticker='SPY'\")
conn.execute(\"DELETE FROM strategies\")
conn.commit()
print('SPY state reset')
conn.close()
"
```

- [ ] **Step 2: Run 5 evolver iterations**

```bash
python scripts/backfill_and_run.py --evolver-only --iterations 5
```

Expected: iterations complete with `trade_count > 0` and `pf_oos > 0` for at least some proposals. The exit manager should be closing positions during walk-forward backtests.

- [ ] **Step 3: Check results**

```bash
python3 -c "
import sqlite3, json
conn = sqlite3.connect('cache/bullbot.db')
conn.row_factory = sqlite3.Row
row = conn.execute('SELECT * FROM ticker_state WHERE ticker=\"SPY\"').fetchone()
if row:
    print(f'Phase: {row[\"phase\"]}')
    print(f'Iterations: {row[\"iteration_count\"]}')
    print(f'Best PF OOS: {row[\"best_pf_oos\"]}')
for p in conn.execute('''
    SELECT ep.iteration, s.class_name, s.params, ep.pf_oos, ep.trade_count, ep.passed_gate
    FROM evolver_proposals ep JOIN strategies s ON ep.strategy_id = s.id
    WHERE ep.ticker=\"SPY\" ORDER BY ep.iteration
''').fetchall():
    gate = 'PASS' if p['passed_gate'] else 'FAIL'
    print(f'  iter {p[\"iteration\"]}: {p[\"class_name\"]} pf_oos={p[\"pf_oos\"]} trades={p[\"trade_count\"]} [{gate}]')
conn.close()
"
```

- [ ] **Step 4: Commit handoff update**

```bash
git add .claude/handoff.md
git commit -m "session 7: exit manager complete, evolver producing trades"
```
