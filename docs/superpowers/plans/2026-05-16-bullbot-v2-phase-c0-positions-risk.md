# Bull-Bot v2 Phase C.0 — Positions, Schema, Risk Caps — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the data foundation for Phase C: five new SQLite tables, the generic `OptionLeg` / `Position` primitives with lifecycle-event support, and a deterministic `risk.py` module with three hard caps plus `compute_max_loss` and `size_position`. After this plan lands, `positions.py` and `risk.py` are fully unit-tested and ready for `chains.py` (C.1) to start writing real Yahoo / BS pricing into legs.

**Architecture:** Pure data + math layer. No I/O beyond SQLite. No LLM. No Yahoo. `positions.py` owns the leg/position dataclasses, the SQL for the new tables, and the helpers to record assignment/exercise events (the *triggers* for those events live in `exits.py`, shipped in C.3 — C.0 only writes the rows when something else asks it to). `risk.py` is pure math over a `list[OptionLeg]` plus three configurable caps read from `bullbot.config`. The five new tables (`v2_positions`, `v2_position_legs`, `v2_position_events`, `v2_position_mtm`, `v2_chain_snapshots`) are additive — the Phase B `v2_paper_trades` table stays in place for back-compat; migration of those rows into the new model happens in C.5 once the runner is wired.

**Tech Stack:** Python 3.11+, SQLite via stdlib `sqlite3`, dataclasses, `pytest`. No new third-party dependencies in this phase.

**Spec reference:** [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md`](../specs/2026-05-16-phase-c-vehicle-agent-design.md) sections 3, 4.3, 4.6 (validation step 1 inputs only — sanity rules live in C.3), 4.7 (assignment/exercise *event-record* helpers only — triggers live in C.3), 6 (positions + CSP-assignment unit tests).

---

## File Structure

| Path | Responsibility | Status |
|---|---|---|
| `bullbot/db/migrations.py` | Existing migration runner. **Modify**: add five new `CREATE TABLE IF NOT EXISTS` blocks under the existing v2 block. | Modify |
| `bullbot/v2/positions.py` | `OptionLeg`, `Position`, lifecycle helpers (open, close, query, record_event). | **Create** |
| `bullbot/v2/risk.py` | Three deterministic caps + `compute_max_loss(legs)` + `size_position(legs, cap, nav)`. | **Create** |
| `tests/unit/test_v2_positions.py` | Unit tests for `positions.py` (insert / load / event-record / net_basis preservation). | **Create** |
| `tests/unit/test_v2_risk.py` | Unit tests for `risk.py` (max-loss per structure, sizing, cap evaluation). | **Create** |
| `tests/unit/test_v2_migrations_phase_c0.py` | Smoke test asserting all five new tables exist after `apply_schema` + columns match. | **Create** |

Each test file imports from `bullbot.v2.positions` / `bullbot.v2.risk` and uses in-memory SQLite with the same fixture style as `tests/unit/test_v2_trades.py`.

---

## Task 1: Schema migration — add five new tables

**Files:**
- Modify: `bullbot/db/migrations.py:74-150` (append new `CREATE TABLE IF NOT EXISTS` blocks at the end of the v2 section)
- Test: `tests/unit/test_v2_migrations_phase_c0.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_v2_migrations_phase_c0.py`:

```python
"""Smoke tests that Phase C.0 schema migration creates all five new tables
with the expected columns."""
from __future__ import annotations

import sqlite3

import pytest

from bullbot.db.migrations import apply_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def test_v2_positions_table_exists_with_phase_c0_columns(conn):
    cols = _columns(conn, "v2_positions")
    assert cols == {
        "id", "ticker", "intent", "structure_kind",
        "exit_plan_version", "profit_target_price", "stop_price",
        "time_stop_dte", "assignment_acceptable",
        "nearest_leg_expiry_dte", "exit_plan_extra_json",
        "opened_ts", "closed_ts", "close_reason",
        "linked_position_id", "rationale",
    }


def test_v2_position_legs_table_exists_with_phase_c0_columns(conn):
    cols = _columns(conn, "v2_position_legs")
    assert cols == {
        "id", "position_id", "action", "kind",
        "strike", "expiry", "qty", "entry_price",
        "net_basis", "exit_price",
    }


def test_v2_position_events_table_exists_with_phase_c0_columns(conn):
    cols = _columns(conn, "v2_position_events")
    assert cols == {
        "id", "position_id", "linked_position_id",
        "event_kind", "occurred_ts", "source_leg_id",
        "original_credit_per_contract", "notes",
    }


def test_v2_position_mtm_table_exists(conn):
    cols = _columns(conn, "v2_position_mtm")
    assert cols == {"position_id", "asof_ts", "mtm_value", "source"}


def test_v2_chain_snapshots_table_exists(conn):
    cols = _columns(conn, "v2_chain_snapshots")
    assert cols == {
        "ticker", "asof_ts", "expiry", "strike", "kind",
        "bid", "ask", "last", "iv", "oi", "source",
    }


def test_intent_check_constraint_rejects_unknown_intent(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO v2_positions "
            "(ticker, intent, structure_kind, opened_ts) "
            "VALUES ('AAPL', 'speculate', 'long_call', 1000)"
        )


def test_event_kind_check_constraint_rejects_unknown_kind(conn):
    conn.execute(
        "INSERT INTO v2_positions (id, ticker, intent, structure_kind, opened_ts) "
        "VALUES (1, 'AAPL', 'accumulate', 'csp', 1000)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO v2_position_events "
            "(position_id, event_kind, occurred_ts) "
            "VALUES (1, 'detonated', 1001)"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_v2_migrations_phase_c0.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: v2_positions` on the first test, with the others erroring on the missing tables similarly.

- [ ] **Step 3: Implement the migration**

Open `bullbot/db/migrations.py`. Find the existing v2 `CREATE TABLE IF NOT EXISTS v2_paper_trades` block. Immediately after that block (still inside `apply_schema`), append:

```python
    # Phase C.0 — Vehicle agent data model.
    # Five additive tables; v2_paper_trades remains untouched (Phase B back-compat).
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS v2_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            intent TEXT NOT NULL CHECK(intent IN ('trade', 'accumulate')),
            structure_kind TEXT NOT NULL,
            exit_plan_version INTEGER NOT NULL DEFAULT 1,
            profit_target_price REAL,
            stop_price REAL,
            time_stop_dte INTEGER,
            assignment_acceptable INTEGER,
            nearest_leg_expiry_dte INTEGER,
            exit_plan_extra_json TEXT,
            opened_ts INTEGER NOT NULL,
            closed_ts INTEGER,
            close_reason TEXT,
            linked_position_id INTEGER,
            rationale TEXT,
            FOREIGN KEY (linked_position_id) REFERENCES v2_positions(id)
        );

        CREATE TABLE IF NOT EXISTS v2_position_legs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('buy', 'sell')),
            kind TEXT NOT NULL CHECK(kind IN ('call', 'put', 'share')),
            strike REAL,
            expiry TEXT,
            qty INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            net_basis REAL,
            exit_price REAL,
            FOREIGN KEY (position_id) REFERENCES v2_positions(id)
        );

        CREATE TABLE IF NOT EXISTS v2_position_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL,
            linked_position_id INTEGER,
            event_kind TEXT NOT NULL CHECK(event_kind IN (
                'assigned', 'called_away', 'exercised', 'expired_worthless'
            )),
            occurred_ts INTEGER NOT NULL,
            source_leg_id INTEGER,
            original_credit_per_contract REAL,
            notes TEXT,
            FOREIGN KEY (position_id) REFERENCES v2_positions(id),
            FOREIGN KEY (linked_position_id) REFERENCES v2_positions(id),
            FOREIGN KEY (source_leg_id) REFERENCES v2_position_legs(id)
        );

        CREATE TABLE IF NOT EXISTS v2_position_mtm (
            position_id INTEGER NOT NULL,
            asof_ts INTEGER NOT NULL,
            mtm_value REAL NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('yahoo', 'bs', 'mixed')),
            PRIMARY KEY (position_id, asof_ts),
            FOREIGN KEY (position_id) REFERENCES v2_positions(id)
        );

        CREATE TABLE IF NOT EXISTS v2_chain_snapshots (
            ticker TEXT NOT NULL,
            asof_ts INTEGER NOT NULL,
            expiry TEXT NOT NULL,
            strike REAL NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('call', 'put')),
            bid REAL, ask REAL, last REAL, iv REAL, oi INTEGER,
            source TEXT NOT NULL CHECK(source IN ('yahoo', 'bs')),
            PRIMARY KEY (ticker, asof_ts, expiry, strike, kind)
        );
    """)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_v2_migrations_phase_c0.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Run the full unit suite to confirm no regression**

Run: `pytest tests/unit -q`
Expected: All previously-passing tests still pass; only the 7 new tests added.

- [ ] **Step 6: Commit**

```bash
git add bullbot/db/migrations.py tests/unit/test_v2_migrations_phase_c0.py
git commit -m "feat(v2/c0): schema migration for Phase C positions + legs + events + mtm + chains"
```

---

## Task 2: `OptionLeg` dataclass + serialization round-trip

**Files:**
- Create: `bullbot/v2/positions.py` (initial — leg primitive only)
- Test: `tests/unit/test_v2_positions.py` (initial — leg primitive only)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_v2_positions.py`:

```python
"""Unit tests for bullbot.v2.positions."""
from __future__ import annotations

import sqlite3

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.v2 import positions


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def test_optionleg_rejects_unknown_action():
    with pytest.raises(ValueError, match="action must be one of"):
        positions.OptionLeg(
            action="hold", kind="call", strike=100.0,
            expiry="2026-06-19", qty=1, entry_price=2.50,
        )


def test_optionleg_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be one of"):
        positions.OptionLeg(
            action="buy", kind="future", strike=100.0,
            expiry="2026-06-19", qty=1, entry_price=2.50,
        )


def test_optionleg_share_leg_requires_null_strike_and_expiry():
    with pytest.raises(ValueError, match="share leg must have strike=None and expiry=None"):
        positions.OptionLeg(
            action="buy", kind="share", strike=100.0,
            expiry=None, qty=100, entry_price=100.0,
        )


def test_optionleg_option_leg_requires_strike_and_expiry():
    with pytest.raises(ValueError, match="option leg must have non-None strike and expiry"):
        positions.OptionLeg(
            action="buy", kind="call", strike=None,
            expiry="2026-06-19", qty=1, entry_price=2.50,
        )


def test_optionleg_net_basis_defaults_to_none():
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=100.0,
        expiry="2026-06-19", qty=1, entry_price=2.50,
    )
    assert leg.net_basis is None


def test_optionleg_effective_basis_uses_net_basis_when_set():
    leg = positions.OptionLeg(
        action="buy", kind="share", strike=None, expiry=None,
        qty=100, entry_price=100.0, net_basis=98.0,
    )
    assert leg.effective_basis() == 98.0


def test_optionleg_effective_basis_falls_back_to_entry_price():
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=100.0,
        expiry="2026-06-19", qty=1, entry_price=2.50,
    )
    assert leg.effective_basis() == 2.50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_v2_positions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bullbot.v2.positions'`.

- [ ] **Step 3: Write the minimal `OptionLeg`**

Create `bullbot/v2/positions.py`:

```python
"""Position / leg primitives for v2 Phase C — vehicle agent.

OptionLeg models a single leg of any atomic options structure. Multi-leg
structures (verticals, ICs, etc.) are represented as list[OptionLeg].

net_basis is non-None only on legs born from an assignment or exercise event
(see record_event in this module and the assignment/exercise paths in
exits.py shipped in C.3). When non-None, P&L and exit-plan targets are
computed against net_basis instead of entry_price.
"""
from __future__ import annotations

from dataclasses import dataclass

VALID_ACTIONS = ("buy", "sell")
VALID_KINDS = ("call", "put", "share")


@dataclass
class OptionLeg:
    action: str
    kind: str
    strike: float | None
    expiry: str | None
    qty: int
    entry_price: float
    net_basis: float | None = None
    id: int | None = None
    position_id: int | None = None
    exit_price: float | None = None

    def __post_init__(self) -> None:
        if self.action not in VALID_ACTIONS:
            raise ValueError(f"action must be one of {VALID_ACTIONS}; got {self.action!r}")
        if self.kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}; got {self.kind!r}")
        if self.kind == "share":
            if self.strike is not None or self.expiry is not None:
                raise ValueError("share leg must have strike=None and expiry=None")
        else:
            if self.strike is None or self.expiry is None:
                raise ValueError("option leg must have non-None strike and expiry")

    def effective_basis(self) -> float:
        """Return net_basis if set, else entry_price.

        Used by exits.py + risk.py whenever a P&L or stop-target needs to be
        computed in basis-aware terms (assigned shares carry net_basis; freshly
        opened shares carry only entry_price).
        """
        return self.net_basis if self.net_basis is not None else self.entry_price
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_v2_positions.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/positions.py tests/unit/test_v2_positions.py
git commit -m "feat(v2/c0): OptionLeg dataclass with net_basis + effective_basis()"
```

---

## Task 3: `Position` dataclass + open/load helpers (single-leg)

**Files:**
- Modify: `bullbot/v2/positions.py` (append `Position` + `open_position` + `load_position`)
- Modify: `tests/unit/test_v2_positions.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_positions.py`:

```python
def test_open_position_inserts_position_and_legs(conn):
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=190.0,
        expiry="2026-06-19", qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL",
        intent="trade",
        structure_kind="long_call",
        legs=[leg],
        opened_ts=1_700_000_000,
        profit_target_price=200.0,
        stop_price=180.0,
        time_stop_dte=21,
        assignment_acceptable=False,
        nearest_leg_expiry_dte=30,
        rationale="bullish breakout above 50sma",
    )
    assert pos.id is not None
    assert pos.legs[0].id is not None
    assert pos.legs[0].position_id == pos.id

    row = conn.execute(
        "SELECT * FROM v2_positions WHERE id=?", (pos.id,)
    ).fetchone()
    assert row["ticker"] == "AAPL"
    assert row["intent"] == "trade"
    assert row["structure_kind"] == "long_call"
    assert row["profit_target_price"] == 200.0
    assert row["stop_price"] == 180.0
    assert row["time_stop_dte"] == 21
    assert row["assignment_acceptable"] == 0
    assert row["nearest_leg_expiry_dte"] == 30
    assert row["exit_plan_version"] == 1
    assert row["closed_ts"] is None
    assert row["rationale"] == "bullish breakout above 50sma"


def test_open_position_with_multi_leg_spread(conn):
    legs = [
        positions.OptionLeg(
            action="buy", kind="call", strike=190.0,
            expiry="2026-06-19", qty=1, entry_price=4.00,
        ),
        positions.OptionLeg(
            action="sell", kind="call", strike=200.0,
            expiry="2026-06-19", qty=1, entry_price=1.50,
        ),
    ]
    pos = positions.open_position(
        conn,
        ticker="AAPL",
        intent="trade",
        structure_kind="bull_call_spread",
        legs=legs,
        opened_ts=1_700_000_000,
        profit_target_price=200.0,
        stop_price=185.0,
        time_stop_dte=21,
        assignment_acceptable=False,
        nearest_leg_expiry_dte=30,
        rationale="defined-risk bull",
    )
    rows = conn.execute(
        "SELECT * FROM v2_position_legs WHERE position_id=? ORDER BY id",
        (pos.id,),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["action"] == "buy"
    assert rows[0]["strike"] == 190.0
    assert rows[1]["action"] == "sell"
    assert rows[1]["strike"] == 200.0


def test_load_position_round_trips_all_fields(conn):
    legs = [
        positions.OptionLeg(
            action="sell", kind="put", strike=180.0,
            expiry="2026-06-19", qty=1, entry_price=2.00,
        ),
    ]
    opened = positions.open_position(
        conn,
        ticker="AAPL", intent="accumulate", structure_kind="csp",
        legs=legs, opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=True,
        nearest_leg_expiry_dte=30,
        rationale="basis-lowering CSP",
    )
    loaded = positions.load_position(conn, opened.id)
    assert loaded.ticker == "AAPL"
    assert loaded.intent == "accumulate"
    assert loaded.structure_kind == "csp"
    assert loaded.assignment_acceptable is True
    assert loaded.profit_target_price is None
    assert len(loaded.legs) == 1
    assert loaded.legs[0].action == "sell"
    assert loaded.legs[0].kind == "put"
    assert loaded.legs[0].strike == 180.0
    assert loaded.legs[0].entry_price == 2.00


def test_load_position_returns_none_for_unknown_id(conn):
    assert positions.load_position(conn, 99999) is None


def test_open_position_rejects_empty_legs(conn):
    with pytest.raises(ValueError, match="at least one leg required"):
        positions.open_position(
            conn,
            ticker="AAPL", intent="trade", structure_kind="long_call",
            legs=[], opened_ts=1_700_000_000,
            profit_target_price=200.0, stop_price=180.0,
            time_stop_dte=21, assignment_acceptable=False,
            nearest_leg_expiry_dte=30, rationale="",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_v2_positions.py -v`
Expected: FAIL on the new tests — `AttributeError: module 'bullbot.v2.positions' has no attribute 'open_position'` etc.

- [ ] **Step 3: Implement `Position` + helpers**

First, add `import sqlite3` to the imports at the top of `bullbot/v2/positions.py` (it's needed for `sqlite3.Connection` type hints in the helpers below).

Then append to the end of `bullbot/v2/positions.py`:

```python
@dataclass
class Position:
    ticker: str
    intent: str
    structure_kind: str
    opened_ts: int
    nearest_leg_expiry_dte: int | None
    legs: list[OptionLeg]
    id: int | None = None
    exit_plan_version: int = 1
    profit_target_price: float | None = None
    stop_price: float | None = None
    time_stop_dte: int | None = None
    assignment_acceptable: bool = False
    exit_plan_extra_json: str | None = None
    closed_ts: int | None = None
    close_reason: str | None = None
    linked_position_id: int | None = None
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.intent not in ("trade", "accumulate"):
            raise ValueError(
                f"intent must be 'trade' or 'accumulate'; got {self.intent!r}"
            )


def open_position(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    intent: str,
    structure_kind: str,
    legs: list[OptionLeg],
    opened_ts: int,
    profit_target_price: float | None,
    stop_price: float | None,
    time_stop_dte: int | None,
    assignment_acceptable: bool,
    nearest_leg_expiry_dte: int | None,
    rationale: str,
    linked_position_id: int | None = None,
    exit_plan_extra_json: str | None = None,
) -> Position:
    """Insert a new position and its legs in one transaction. Returns the
    Position with `id` populated on it and on each leg."""
    if not legs:
        raise ValueError("at least one leg required")

    cur = conn.execute(
        "INSERT INTO v2_positions "
        "(ticker, intent, structure_kind, exit_plan_version, "
        "profit_target_price, stop_price, time_stop_dte, "
        "assignment_acceptable, nearest_leg_expiry_dte, exit_plan_extra_json, "
        "opened_ts, linked_position_id, rationale) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticker, intent, structure_kind,
            profit_target_price, stop_price, time_stop_dte,
            1 if assignment_acceptable else 0,
            nearest_leg_expiry_dte, exit_plan_extra_json,
            opened_ts, linked_position_id, rationale,
        ),
    )
    pid = cur.lastrowid
    for leg in legs:
        leg_cur = conn.execute(
            "INSERT INTO v2_position_legs "
            "(position_id, action, kind, strike, expiry, qty, entry_price, net_basis) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pid, leg.action, leg.kind, leg.strike, leg.expiry,
                leg.qty, leg.entry_price, leg.net_basis,
            ),
        )
        leg.id = leg_cur.lastrowid
        leg.position_id = pid
    conn.commit()

    return Position(
        id=pid, ticker=ticker, intent=intent, structure_kind=structure_kind,
        opened_ts=opened_ts, nearest_leg_expiry_dte=nearest_leg_expiry_dte,
        legs=legs, profit_target_price=profit_target_price, stop_price=stop_price,
        time_stop_dte=time_stop_dte, assignment_acceptable=assignment_acceptable,
        exit_plan_extra_json=exit_plan_extra_json,
        linked_position_id=linked_position_id, rationale=rationale,
    )


def load_position(conn: sqlite3.Connection, position_id: int) -> Position | None:
    """Read a position + its legs back. Returns None if no such position."""
    row = conn.execute(
        "SELECT * FROM v2_positions WHERE id=?", (position_id,)
    ).fetchone()
    if row is None:
        return None
    leg_rows = conn.execute(
        "SELECT * FROM v2_position_legs WHERE position_id=? ORDER BY id",
        (position_id,),
    ).fetchall()
    legs = [
        OptionLeg(
            id=lr["id"],
            position_id=lr["position_id"],
            action=lr["action"],
            kind=lr["kind"],
            strike=lr["strike"],
            expiry=lr["expiry"],
            qty=lr["qty"],
            entry_price=lr["entry_price"],
            net_basis=lr["net_basis"],
            exit_price=lr["exit_price"],
        )
        for lr in leg_rows
    ]
    return Position(
        id=row["id"],
        ticker=row["ticker"],
        intent=row["intent"],
        structure_kind=row["structure_kind"],
        exit_plan_version=row["exit_plan_version"],
        profit_target_price=row["profit_target_price"],
        stop_price=row["stop_price"],
        time_stop_dte=row["time_stop_dte"],
        assignment_acceptable=bool(row["assignment_acceptable"]) if row["assignment_acceptable"] is not None else False,
        nearest_leg_expiry_dte=row["nearest_leg_expiry_dte"],
        exit_plan_extra_json=row["exit_plan_extra_json"],
        opened_ts=row["opened_ts"],
        closed_ts=row["closed_ts"],
        close_reason=row["close_reason"],
        linked_position_id=row["linked_position_id"],
        rationale=row["rationale"] or "",
        legs=legs,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_v2_positions.py -v`
Expected: PASS (12 tests total now — the original 7 plus 5 new).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/positions.py tests/unit/test_v2_positions.py
git commit -m "feat(v2/c0): Position dataclass + open_position + load_position helpers"
```

---

## Task 4: Close + query helpers (open_for_ticker, open_count, close_position)

**Files:**
- Modify: `bullbot/v2/positions.py` (append helpers)
- Modify: `tests/unit/test_v2_positions.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_positions.py`:

```python
def _open_simple(conn, ticker="AAPL", intent="trade", structure_kind="long_call"):
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=190.0,
        expiry="2026-06-19", qty=1, entry_price=2.50,
    )
    return positions.open_position(
        conn,
        ticker=ticker, intent=intent, structure_kind=structure_kind,
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=200.0, stop_price=180.0,
        time_stop_dte=21, assignment_acceptable=(intent == "accumulate"),
        nearest_leg_expiry_dte=30, rationale="t",
    )


def test_open_for_ticker_returns_open_position(conn):
    pos = _open_simple(conn, ticker="AAPL")
    found = positions.open_for_ticker(conn, "AAPL")
    assert found is not None
    assert found.id == pos.id


def test_open_for_ticker_returns_none_when_flat(conn):
    assert positions.open_for_ticker(conn, "AAPL") is None


def test_open_for_ticker_ignores_closed_positions(conn):
    pos = _open_simple(conn, ticker="AAPL")
    positions.close_position(
        conn, position_id=pos.id, closed_ts=1_700_001_000,
        close_reason="profit_target",
        leg_exit_prices={pos.legs[0].id: 5.00},
    )
    assert positions.open_for_ticker(conn, "AAPL") is None


def test_open_count_counts_only_open(conn):
    _open_simple(conn, ticker="AAPL")
    _open_simple(conn, ticker="MSFT")
    closed = _open_simple(conn, ticker="GOOG")
    positions.close_position(
        conn, position_id=closed.id, closed_ts=1_700_001_000,
        close_reason="stop", leg_exit_prices={closed.legs[0].id: 0.50},
    )
    assert positions.open_count(conn) == 2


def test_close_position_persists_exit_fields(conn):
    pos = _open_simple(conn, ticker="AAPL")
    positions.close_position(
        conn, position_id=pos.id, closed_ts=1_700_001_000,
        close_reason="profit_target",
        leg_exit_prices={pos.legs[0].id: 5.00},
    )
    reloaded = positions.load_position(conn, pos.id)
    assert reloaded.closed_ts == 1_700_001_000
    assert reloaded.close_reason == "profit_target"
    assert reloaded.legs[0].exit_price == 5.00


def test_close_position_rejects_unknown_close_reason(conn):
    pos = _open_simple(conn, ticker="AAPL")
    with pytest.raises(ValueError, match="close_reason must be one of"):
        positions.close_position(
            conn, position_id=pos.id, closed_ts=1_700_001_000,
            close_reason="for_fun",
            leg_exit_prices={pos.legs[0].id: 5.00},
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_v2_positions.py -v`
Expected: FAIL on the 6 new tests — `AttributeError: module 'bullbot.v2.positions' has no attribute 'open_for_ticker'` etc.

- [ ] **Step 3: Implement the helpers**

Append to `bullbot/v2/positions.py`:

```python
VALID_CLOSE_REASONS = (
    "profit_target", "stop", "time_stop", "signal_flip",
    "credit_profit_take", "assigned", "called_away", "exercised",
    "expired_worthless", "safety_stop", "manual",
)


def open_for_ticker(conn: sqlite3.Connection, ticker: str) -> Position | None:
    """Return the single open position for this ticker, or None.

    Phase C invariant: at most one open position per ticker at a time.
    """
    row = conn.execute(
        "SELECT id FROM v2_positions "
        "WHERE ticker=? AND closed_ts IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if row is None:
        return None
    return load_position(conn, row["id"])


def open_count(conn: sqlite3.Connection) -> int:
    """Count positions that are currently open (closed_ts IS NULL)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM v2_positions WHERE closed_ts IS NULL"
    ).fetchone()
    return int(row["n"])


def close_position(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    closed_ts: int,
    close_reason: str,
    leg_exit_prices: dict[int, float],
) -> None:
    """Mark a position closed with the given reason and per-leg exit prices.

    leg_exit_prices maps leg.id -> per-contract (or per-share) exit price.
    Realized P&L computation lives in risk.py / runner_c.py — this helper only
    persists the exit fields.
    """
    if close_reason not in VALID_CLOSE_REASONS:
        raise ValueError(
            f"close_reason must be one of {VALID_CLOSE_REASONS}; got {close_reason!r}"
        )
    conn.execute(
        "UPDATE v2_positions SET closed_ts=?, close_reason=? WHERE id=?",
        (closed_ts, close_reason, position_id),
    )
    for leg_id, exit_price in leg_exit_prices.items():
        conn.execute(
            "UPDATE v2_position_legs SET exit_price=? WHERE id=?",
            (exit_price, leg_id),
        )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_v2_positions.py -v`
Expected: PASS (18 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/positions.py tests/unit/test_v2_positions.py
git commit -m "feat(v2/c0): open_for_ticker + open_count + close_position helpers"
```

---

## Task 5: Lifecycle event recording (CSP-assignment net-basis math)

**Files:**
- Modify: `bullbot/v2/positions.py` (append `record_event` + `assign_csp_to_shares`)
- Modify: `tests/unit/test_v2_positions.py` (append CSP-assignment + net-basis tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_positions.py`:

```python
def test_record_event_inserts_v2_position_events_row(conn):
    pos = _open_simple(conn, ticker="AAPL", intent="accumulate", structure_kind="csp")
    positions.record_event(
        conn,
        position_id=pos.id,
        event_kind="expired_worthless",
        occurred_ts=1_700_002_000,
        source_leg_id=pos.legs[0].id,
        linked_position_id=None,
        original_credit_per_contract=None,
        notes="OTM at expiry",
    )
    rows = conn.execute("SELECT * FROM v2_position_events").fetchall()
    assert len(rows) == 1
    assert rows[0]["event_kind"] == "expired_worthless"
    assert rows[0]["position_id"] == pos.id
    assert rows[0]["source_leg_id"] == pos.legs[0].id
    assert rows[0]["notes"] == "OTM at expiry"


def test_assign_csp_to_shares_creates_linked_shares_with_net_basis(conn):
    """Grok review Tier 1 Finding 1: assigned CSP → linked shares carry
    net_basis = strike - (csp_credit_per_contract / 100). $2.00 credit on a
    $100 strike → shares.net_basis = $98.00. Subsequent P&L computed against
    $98.00, not $100.00."""
    csp_leg = positions.OptionLeg(
        action="sell", kind="put", strike=100.0,
        expiry="2026-06-19", qty=1, entry_price=2.00,
    )
    csp = positions.open_position(
        conn,
        ticker="AAPL", intent="accumulate", structure_kind="csp",
        legs=[csp_leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=True,
        nearest_leg_expiry_dte=30, rationale="lower basis",
    )

    shares_pos = positions.assign_csp_to_shares(
        conn,
        csp_position=csp,
        csp_leg_id=csp_leg.id,
        original_credit_per_contract=200.0,  # $2.00 × 100
        occurred_ts=1_700_500_000,
        # post-assignment exit plan is supplied by the caller — exits.py in C.3
        # derives it from the current signal. For this unit test we pass a
        # plain accumulate-style plan.
        intent="accumulate",
        profit_target_price=None,
        stop_price=96.00,
        time_stop_dte=None,
        nearest_leg_expiry_dte=None,
        rationale="post-assignment shares, signal still bullish",
    )

    # Linked shares position exists with one share leg
    assert shares_pos.structure_kind == "long_shares"
    assert shares_pos.linked_position_id == csp.id
    assert len(shares_pos.legs) == 1
    share_leg = shares_pos.legs[0]
    assert share_leg.kind == "share"
    assert share_leg.action == "buy"
    assert share_leg.qty == 100
    assert share_leg.entry_price == 100.0
    assert share_leg.net_basis == 98.0
    assert share_leg.effective_basis() == 98.0

    # CSP is closed with reason 'assigned'
    csp_reloaded = positions.load_position(conn, csp.id)
    assert csp_reloaded.closed_ts == 1_700_500_000
    assert csp_reloaded.close_reason == "assigned"

    # Event row records the credit so basis math is auditable
    event_row = conn.execute(
        "SELECT * FROM v2_position_events WHERE position_id=?", (csp.id,)
    ).fetchone()
    assert event_row["event_kind"] == "assigned"
    assert event_row["linked_position_id"] == shares_pos.id
    assert event_row["original_credit_per_contract"] == 200.0
    assert event_row["source_leg_id"] == csp_leg.id


def test_assign_csp_handles_multi_contract_csp(conn):
    """3-contract CSP, $1.50 credit each → 300 shares with net_basis = strike − 1.50."""
    csp_leg = positions.OptionLeg(
        action="sell", kind="put", strike=50.0,
        expiry="2026-06-19", qty=3, entry_price=1.50,
    )
    csp = positions.open_position(
        conn,
        ticker="F", intent="accumulate", structure_kind="csp",
        legs=[csp_leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=True,
        nearest_leg_expiry_dte=30, rationale="",
    )
    shares_pos = positions.assign_csp_to_shares(
        conn,
        csp_position=csp,
        csp_leg_id=csp_leg.id,
        original_credit_per_contract=150.0,
        occurred_ts=1_700_500_000,
        intent="accumulate",
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, nearest_leg_expiry_dte=None, rationale="",
    )
    share_leg = shares_pos.legs[0]
    assert share_leg.qty == 300
    assert share_leg.entry_price == 50.0
    assert share_leg.net_basis == pytest.approx(48.50)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_v2_positions.py -v`
Expected: FAIL on the 3 new tests — `AttributeError: module 'bullbot.v2.positions' has no attribute 'record_event'`.

- [ ] **Step 3: Implement event recording + CSP assignment**

Append to `bullbot/v2/positions.py`:

```python
VALID_EVENT_KINDS = (
    "assigned", "called_away", "exercised", "expired_worthless",
)


def record_event(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    event_kind: str,
    occurred_ts: int,
    source_leg_id: int | None,
    linked_position_id: int | None,
    original_credit_per_contract: float | None,
    notes: str | None,
) -> int:
    """Insert a v2_position_events row. Returns the event id.

    The trigger conditions for these events live in exits.py (C.3). This
    helper only persists the row when something else asks it to. Callers
    are responsible for the SQL transaction boundary.
    """
    if event_kind not in VALID_EVENT_KINDS:
        raise ValueError(
            f"event_kind must be one of {VALID_EVENT_KINDS}; got {event_kind!r}"
        )
    cur = conn.execute(
        "INSERT INTO v2_position_events "
        "(position_id, linked_position_id, event_kind, occurred_ts, "
        "source_leg_id, original_credit_per_contract, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            position_id, linked_position_id, event_kind, occurred_ts,
            source_leg_id, original_credit_per_contract, notes,
        ),
    )
    conn.commit()
    return cur.lastrowid


def assign_csp_to_shares(
    conn: sqlite3.Connection,
    *,
    csp_position: Position,
    csp_leg_id: int,
    original_credit_per_contract: float,
    occurred_ts: int,
    intent: str,
    profit_target_price: float | None,
    stop_price: float | None,
    time_stop_dte: int | None,
    nearest_leg_expiry_dte: int | None,
    rationale: str,
) -> Position:
    """Simulate CSP assignment: close the CSP, open a linked long-shares
    position with the basis-adjusted net_basis, and record the event.

    Per Grok review Tier 1 Finding 1:
        net_basis = strike − (original_credit_per_contract / 100)
        share_qty = csp_qty × 100

    The post-assignment exit plan (intent, target, stop, dte) is supplied by
    the caller — derivation from the current signal lives in
    exits.compute_post_assignment_exit_plan in C.3.
    """
    csp_leg = next((leg for leg in csp_position.legs if leg.id == csp_leg_id), None)
    if csp_leg is None or csp_leg.kind != "put" or csp_leg.action != "sell":
        raise ValueError("csp_leg_id must reference a short-put leg on csp_position")
    strike = csp_leg.strike
    csp_qty = csp_leg.qty

    net_basis = strike - (original_credit_per_contract / 100.0)
    share_qty = csp_qty * 100

    share_leg = OptionLeg(
        action="buy", kind="share", strike=None, expiry=None,
        qty=share_qty, entry_price=strike, net_basis=net_basis,
    )
    shares_pos = open_position(
        conn,
        ticker=csp_position.ticker,
        intent=intent,
        structure_kind="long_shares",
        legs=[share_leg],
        opened_ts=occurred_ts,
        profit_target_price=profit_target_price,
        stop_price=stop_price,
        time_stop_dte=time_stop_dte,
        assignment_acceptable=False,
        nearest_leg_expiry_dte=nearest_leg_expiry_dte,
        rationale=rationale,
        linked_position_id=csp_position.id,
    )

    close_position(
        conn,
        position_id=csp_position.id,
        closed_ts=occurred_ts,
        close_reason="assigned",
        leg_exit_prices={csp_leg_id: 0.0},
    )

    record_event(
        conn,
        position_id=csp_position.id,
        event_kind="assigned",
        occurred_ts=occurred_ts,
        source_leg_id=csp_leg_id,
        linked_position_id=shares_pos.id,
        original_credit_per_contract=original_credit_per_contract,
        notes=None,
    )

    return shares_pos
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_v2_positions.py -v`
Expected: PASS (21 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/positions.py tests/unit/test_v2_positions.py
git commit -m "feat(v2/c0): record_event + assign_csp_to_shares with net_basis math"
```

---

## Task 6: `risk.compute_max_loss` — single-leg structures

**Files:**
- Create: `bullbot/v2/risk.py`
- Create: `tests/unit/test_v2_risk.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_v2_risk.py`:

```python
"""Unit tests for bullbot.v2.risk — deterministic max-loss math + caps."""
from __future__ import annotations

import pytest

from bullbot.v2 import risk
from bullbot.v2.positions import OptionLeg


def _call(action, strike, premium, qty=1, expiry="2026-06-19"):
    return OptionLeg(
        action=action, kind="call", strike=strike,
        expiry=expiry, qty=qty, entry_price=premium,
    )


def _put(action, strike, premium, qty=1, expiry="2026-06-19"):
    return OptionLeg(
        action=action, kind="put", strike=strike,
        expiry=expiry, qty=qty, entry_price=premium,
    )


def _shares(action, price, qty=100):
    return OptionLeg(
        action=action, kind="share", strike=None, expiry=None,
        qty=qty, entry_price=price,
    )


def test_max_loss_long_call_equals_premium_paid(spot=190.0):
    leg = _call("buy", strike=190.0, premium=2.50, qty=1)
    # 1 contract × $2.50 premium × 100 multiplier = $250
    assert risk.compute_max_loss([leg], spot=spot) == 250.0


def test_max_loss_long_call_scales_with_qty(spot=190.0):
    leg = _call("buy", strike=190.0, premium=2.50, qty=3)
    assert risk.compute_max_loss([leg], spot=spot) == 750.0


def test_max_loss_long_put_equals_premium_paid(spot=190.0):
    leg = _put("buy", strike=180.0, premium=1.75, qty=2)
    assert risk.compute_max_loss([leg], spot=spot) == 350.0


def test_max_loss_long_shares_uses_15pct_safety_stop(spot=100.0):
    """Phase C safety stop is 15% adverse from entry (design §4.7).
    Max loss is the dollar size of that worst-case move."""
    leg = _shares("buy", price=100.0, qty=100)
    # 100 shares × $100 entry × 15% = $1500
    assert risk.compute_max_loss([leg], spot=spot) == 1500.0


def test_max_loss_short_shares_uses_15pct_safety_stop(spot=100.0):
    leg = _shares("sell", price=100.0, qty=50)
    # 50 shares × $100 entry × 15% = $750
    assert risk.compute_max_loss([leg], spot=spot) == 750.0


def test_max_loss_short_put_csp_is_strike_minus_credit_per_contract(spot=100.0):
    """CSP max loss = (strike − credit) × 100 × qty. Strike $100, credit $2,
    1 contract → $9,800 (the price you'd pay if assigned at zero)."""
    leg = _put("sell", strike=100.0, premium=2.00, qty=1)
    assert risk.compute_max_loss([leg], spot=spot) == pytest.approx(9800.0)


def test_max_loss_short_call_naked_is_unbounded_returns_inf(spot=100.0):
    """Naked short call has theoretically infinite loss. We return inf so
    risk caps will always reject it. (covered_call is handled separately —
    that's a multi-leg structure tested in Task 7.)"""
    leg = _call("sell", strike=110.0, premium=1.50, qty=1)
    assert risk.compute_max_loss([leg], spot=spot) == float("inf")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_v2_risk.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bullbot.v2.risk'`.

- [ ] **Step 3: Implement single-leg max-loss**

Create `bullbot/v2/risk.py`:

```python
"""Deterministic risk math for v2 Phase C.

Two responsibilities:
1. compute_max_loss(legs, spot) — worst-case dollar loss for any list[OptionLeg]
   atomic structure, used by the validation pipeline (vehicle.validate) and
   by size_position to compute qty.
2. Three hard caps + cap-evaluation helpers, fed from config and called by
   vehicle.validate before persisting a position.

Multipliers: options are quoted per-share but contract size is 100. Premium of
$2.50 on 1 contract = $250 cash. Shares are 1:1.

The safety-stop max loss for outright share legs uses SHARE_SAFETY_STOP_PCT
(default 15%) — the same number used by exits.evaluate's safety net (design
§4.7). When config raises that cap, both this module and exits.evaluate
read the new value.
"""
from __future__ import annotations

import math

from bullbot.v2.positions import OptionLeg

CONTRACT_MULTIPLIER = 100
SHARE_SAFETY_STOP_PCT = 0.15


def compute_max_loss(legs: list[OptionLeg], *, spot: float) -> float:
    """Worst-case dollar loss of holding `legs` to expiry (or to safety-stop
    for share legs).

    Returns float('inf') for structures with theoretically unbounded loss
    (naked short call, naked short shares with no defined stop).

    spot is used for share-leg safety-stop sizing and for matching credit
    legs against intrinsic-value at strike crosses.
    """
    if len(legs) == 1:
        return _single_leg_max_loss(legs[0], spot=spot)
    raise NotImplementedError(
        "multi-leg max_loss arrives in Task 7"
    )


def _single_leg_max_loss(leg: OptionLeg, *, spot: float) -> float:
    if leg.kind == "share":
        # 15% safety-stop on share entry (design §4.7). Same for long and
        # short — short shares have unbounded upside risk but the safety
        # stop caps it for sizing purposes.
        return leg.entry_price * leg.qty * SHARE_SAFETY_STOP_PCT
    # Option legs
    premium_dollars = leg.entry_price * leg.qty * CONTRACT_MULTIPLIER
    if leg.action == "buy":
        # Long premium — max loss is the premium paid.
        return premium_dollars
    # action == "sell"
    if leg.kind == "put":
        # Naked short put (CSP). Max loss = (strike − credit) × 100 × qty
        # — the price you'd pay if the stock went to zero, net of the credit.
        return max(0.0, (leg.strike - leg.entry_price) * CONTRACT_MULTIPLIER * leg.qty)
    if leg.kind == "call":
        # Naked short call — theoretically unbounded.
        return math.inf
    return math.inf
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_v2_risk.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/risk.py tests/unit/test_v2_risk.py
git commit -m "feat(v2/c0): risk.compute_max_loss for single-leg structures"
```

---

## Task 7: `risk.compute_max_loss` — multi-leg structures

**Files:**
- Modify: `bullbot/v2/risk.py` (extend `compute_max_loss` for multi-leg)
- Modify: `tests/unit/test_v2_risk.py` (append multi-leg tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_risk.py`:

```python
def test_max_loss_bull_call_spread_is_width_minus_credit(spot=190.0):
    """Bull call spread: buy 190 call @ $4, sell 200 call @ $1.50.
    Net debit = $2.50. Width = $10. Max loss = net debit = $250 per contract."""
    legs = [
        _call("buy", strike=190.0, premium=4.00, qty=1),
        _call("sell", strike=200.0, premium=1.50, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=spot) == pytest.approx(250.0)


def test_max_loss_bear_put_spread_is_net_debit(spot=190.0):
    """Buy 190 put @ $3, sell 180 put @ $1. Net debit = $2 → max loss $200."""
    legs = [
        _put("buy", strike=190.0, premium=3.00, qty=1),
        _put("sell", strike=180.0, premium=1.00, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=spot) == pytest.approx(200.0)


def test_max_loss_bull_put_credit_spread_is_width_minus_credit(spot=190.0):
    """Sell 190 put @ $3, buy 180 put @ $1. Width $10, credit $2.
    Max loss = (width - credit) × 100 = $800 per contract."""
    legs = [
        _put("sell", strike=190.0, premium=3.00, qty=1),
        _put("buy", strike=180.0, premium=1.00, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=spot) == pytest.approx(800.0)


def test_max_loss_bear_call_credit_spread_is_width_minus_credit(spot=190.0):
    legs = [
        _call("sell", strike=200.0, premium=2.50, qty=1),
        _call("buy", strike=210.0, premium=0.75, qty=1),
    ]
    # width $10, credit $1.75 → max loss $825
    assert risk.compute_max_loss(legs, spot=spot) == pytest.approx(825.0)


def test_max_loss_iron_condor_is_max_wing_width_minus_credit(spot=100.0):
    """IC: sell 110c@$2 / buy 115c@$0.50 / sell 90p@$2 / buy 85p@$0.50.
    Credit per side = $1.50; total credit = $3.00. Each wing is $5 wide.
    Max loss on either side = ($5 − $3) × 100 = $200 per contract."""
    legs = [
        _call("sell", strike=110.0, premium=2.00, qty=1),
        _call("buy", strike=115.0, premium=0.50, qty=1),
        _put("sell", strike=90.0, premium=2.00, qty=1),
        _put("buy", strike=85.0, premium=0.50, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=spot) == pytest.approx(200.0)


def test_max_loss_long_call_butterfly_is_net_debit(spot=100.0):
    """Buy 1× 95c @ $6, sell 2× 100c @ $3, buy 1× 105c @ $1.
    Net debit = $6 − 2($3) + $1 = $1 → max loss $100 per contract."""
    legs = [
        _call("buy", strike=95.0, premium=6.00, qty=1),
        _call("sell", strike=100.0, premium=3.00, qty=2),
        _call("buy", strike=105.0, premium=1.00, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=spot) == pytest.approx(100.0)


def test_max_loss_covered_call_is_share_safety_stop_minus_call_credit(spot=100.0):
    """Long 100 shares @ $100 + short 105 call @ $1.50.
    Share safety stop = 100 × 100 × 15% = $1500. Call credit = $150.
    Max loss = $1500 − $150 = $1350. (The short call caps the upside but
    bounds the downside only by the premium received.)"""
    legs = [
        _shares("buy", price=100.0, qty=100),
        _call("sell", strike=105.0, premium=1.50, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=spot) == pytest.approx(1350.0)


def test_max_loss_returns_inf_for_unrecognized_multi_leg_shape(spot=100.0):
    """A leg combo we don't have a rule for falls back to inf so risk caps
    reject it. (validate_structure_sanity in C.3 rejects nonsense at LLM
    output time, before max_loss is ever called.)"""
    legs = [
        _call("buy", strike=100.0, premium=2.0, qty=1),
        _put("buy", strike=110.0, premium=1.0, qty=1),
        _call("sell", strike=120.0, premium=0.5, qty=1),
    ]
    assert risk.compute_max_loss(legs, spot=spot) == float("inf")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_v2_risk.py -v`
Expected: FAIL on the 8 new tests — `NotImplementedError: multi-leg max_loss arrives in Task 7`.

- [ ] **Step 3: Replace `NotImplementedError` with multi-leg logic**

Replace the `raise NotImplementedError` line in `bullbot/v2/risk.py`'s `compute_max_loss` with a dispatch by shape, and add the shape classifier + per-shape functions. Final state of `compute_max_loss` + helpers (replace the existing `compute_max_loss` and add new helpers at the bottom of the module):

```python
def compute_max_loss(legs: list[OptionLeg], *, spot: float) -> float:
    """Worst-case dollar loss of holding `legs` to expiry (or to safety-stop
    for share legs).

    Returns float('inf') for structures with theoretically unbounded loss
    (naked short call, naked short shares, or any multi-leg combo without a
    matching shape rule below). validate_structure_sanity (C.3) rejects
    nonsense at LLM output time, so this fallback only fires if a multi-leg
    structure slipped through validation — which means we should refuse to
    size it.
    """
    if len(legs) == 1:
        return _single_leg_max_loss(legs[0], spot=spot)
    return _multi_leg_max_loss(legs, spot=spot)


def _multi_leg_max_loss(legs: list[OptionLeg], *, spot: float) -> float:
    if _is_vertical_debit_spread(legs):
        return _vertical_debit_max_loss(legs)
    if _is_vertical_credit_spread(legs):
        return _vertical_credit_max_loss(legs)
    if _is_iron_condor(legs):
        return _iron_condor_max_loss(legs)
    if _is_long_butterfly(legs):
        return _long_butterfly_max_loss(legs)
    if _is_covered_call(legs):
        return _covered_call_max_loss(legs, spot=spot)
    return math.inf


def _is_vertical_debit_spread(legs: list[OptionLeg]) -> bool:
    if len(legs) != 2:
        return False
    if any(l.kind == "share" for l in legs):
        return False
    if legs[0].kind != legs[1].kind:
        return False
    if {l.action for l in legs} != {"buy", "sell"}:
        return False
    if legs[0].expiry != legs[1].expiry:
        return False
    buy = next(l for l in legs if l.action == "buy")
    sell = next(l for l in legs if l.action == "sell")
    # Bull call: long lower strike. Bear put: long higher strike.
    if legs[0].kind == "call":
        return buy.strike < sell.strike and (
            buy.entry_price * buy.qty > sell.entry_price * sell.qty
        )
    return buy.strike > sell.strike and (
        buy.entry_price * buy.qty > sell.entry_price * sell.qty
    )


def _vertical_debit_max_loss(legs: list[OptionLeg]) -> float:
    buy = next(l for l in legs if l.action == "buy")
    sell = next(l for l in legs if l.action == "sell")
    # Per-contract net debit in price units. Multiplied by CONTRACT_MULTIPLIER
    # (100) and then by contract qty to get dollar max-loss.
    net_debit_per_contract = buy.entry_price - sell.entry_price
    qty = min(buy.qty, sell.qty)
    return net_debit_per_contract * CONTRACT_MULTIPLIER * qty


def _is_vertical_credit_spread(legs: list[OptionLeg]) -> bool:
    if len(legs) != 2:
        return False
    if any(l.kind == "share" for l in legs):
        return False
    if legs[0].kind != legs[1].kind:
        return False
    if {l.action for l in legs} != {"buy", "sell"}:
        return False
    if legs[0].expiry != legs[1].expiry:
        return False
    buy = next(l for l in legs if l.action == "buy")
    sell = next(l for l in legs if l.action == "sell")
    # Bull put credit: short higher strike. Bear call credit: short lower strike.
    if legs[0].kind == "put":
        return sell.strike > buy.strike and (
            sell.entry_price * sell.qty > buy.entry_price * buy.qty
        )
    # call credit
    return sell.strike < buy.strike and (
        sell.entry_price * sell.qty > buy.entry_price * buy.qty
    )


def _vertical_credit_max_loss(legs: list[OptionLeg]) -> float:
    buy = next(l for l in legs if l.action == "buy")
    sell = next(l for l in legs if l.action == "sell")
    width = abs(buy.strike - sell.strike)
    credit_per_contract = sell.entry_price - buy.entry_price
    qty = min(buy.qty, sell.qty)
    return (width - credit_per_contract) * CONTRACT_MULTIPLIER * qty


def _is_iron_condor(legs: list[OptionLeg]) -> bool:
    if len(legs) != 4:
        return False
    if any(l.kind == "share" for l in legs):
        return False
    calls = [l for l in legs if l.kind == "call"]
    puts = [l for l in legs if l.kind == "put"]
    if len(calls) != 2 or len(puts) != 2:
        return False
    if {l.expiry for l in legs} != {legs[0].expiry}:
        return False
    if {l.action for l in calls} != {"buy", "sell"}:
        return False
    if {l.action for l in puts} != {"buy", "sell"}:
        return False
    return True


def _iron_condor_max_loss(legs: list[OptionLeg]) -> float:
    calls = sorted(
        [l for l in legs if l.kind == "call"], key=lambda l: l.strike,
    )
    puts = sorted(
        [l for l in legs if l.kind == "put"], key=lambda l: l.strike,
    )
    # Calls: lower strike is short, higher is long. Width = long - short.
    short_call = next(l for l in calls if l.action == "sell")
    long_call = next(l for l in calls if l.action == "buy")
    short_put = next(l for l in puts if l.action == "sell")
    long_put = next(l for l in puts if l.action == "buy")
    call_width = long_call.strike - short_call.strike
    put_width = short_put.strike - long_put.strike
    total_credit = (
        (short_call.entry_price - long_call.entry_price)
        + (short_put.entry_price - long_put.entry_price)
    )
    qty = min(l.qty for l in legs)
    # Max loss occurs on whichever wing is wider, minus total credit.
    max_wing = max(call_width, put_width)
    return (max_wing - total_credit) * CONTRACT_MULTIPLIER * qty


def _is_long_butterfly(legs: list[OptionLeg]) -> bool:
    if len(legs) != 3:
        return False
    if any(l.kind == "share" for l in legs):
        return False
    if len({l.kind for l in legs}) != 1:
        return False
    if len({l.expiry for l in legs}) != 1:
        return False
    sorted_legs = sorted(legs, key=lambda l: l.strike)
    return (
        sorted_legs[0].action == "buy" and sorted_legs[0].qty == 1
        and sorted_legs[1].action == "sell" and sorted_legs[1].qty == 2
        and sorted_legs[2].action == "buy" and sorted_legs[2].qty == 1
    )


def _long_butterfly_max_loss(legs: list[OptionLeg]) -> float:
    sorted_legs = sorted(legs, key=lambda l: l.strike)
    low, mid, high = sorted_legs
    net_debit = (
        low.entry_price * low.qty
        - mid.entry_price * mid.qty
        + high.entry_price * high.qty
    )
    return net_debit * CONTRACT_MULTIPLIER


def _is_covered_call(legs: list[OptionLeg]) -> bool:
    if len(legs) != 2:
        return False
    shares = [l for l in legs if l.kind == "share"]
    calls = [l for l in legs if l.kind == "call"]
    if len(shares) != 1 or len(calls) != 1:
        return False
    share = shares[0]
    call = calls[0]
    return (
        share.action == "buy"
        and call.action == "sell"
        and share.qty == call.qty * CONTRACT_MULTIPLIER
    )


def _covered_call_max_loss(legs: list[OptionLeg], *, spot: float) -> float:
    share = next(l for l in legs if l.kind == "share")
    call = next(l for l in legs if l.kind == "call")
    share_safety_loss = share.entry_price * share.qty * SHARE_SAFETY_STOP_PCT
    call_credit = call.entry_price * call.qty * CONTRACT_MULTIPLIER
    return max(0.0, share_safety_loss - call_credit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_v2_risk.py -v`
Expected: PASS (15 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/risk.py tests/unit/test_v2_risk.py
git commit -m "feat(v2/c0): risk.compute_max_loss for verticals, ICs, butterflies, covered calls"
```

---

## Task 8: `risk.size_position` and three-cap evaluators

**Files:**
- Modify: `bullbot/v2/risk.py` (append `size_position` + `evaluate_caps`)
- Modify: `tests/unit/test_v2_risk.py` (append sizing + cap tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_risk.py`:

```python
def test_size_position_returns_qty_fitting_max_loss_cap_long_call(spot=190.0):
    """Cap = 2% of $50,000 NAV = $1,000. A single contract at $2.50 premium
    risks $250. 4 contracts risks $1000. Helper returns 4."""
    leg_template = _call("buy", strike=190.0, premium=2.50, qty=1)
    sized_qty = risk.size_position(
        leg_template=leg_template, nav=50_000.0, max_loss_pct=0.02, spot=spot,
    )
    assert sized_qty == 4


def test_size_position_rounds_down_never_exceeds_cap(spot=190.0):
    """Cap = $1000. Single contract risks $300 (3.00 premium). Should return
    3 contracts ($900 risk), not 4 ($1200)."""
    leg_template = _call("buy", strike=190.0, premium=3.00, qty=1)
    sized_qty = risk.size_position(
        leg_template=leg_template, nav=50_000.0, max_loss_pct=0.02, spot=spot,
    )
    assert sized_qty == 3


def test_size_position_returns_zero_when_single_contract_exceeds_cap(spot=190.0):
    """Premium $15 → $1500 per contract. Cap $1000. Returns 0 → caller emits
    skipped_max_loss_cap."""
    leg_template = _call("buy", strike=190.0, premium=15.00, qty=1)
    sized_qty = risk.size_position(
        leg_template=leg_template, nav=50_000.0, max_loss_pct=0.02, spot=spot,
    )
    assert sized_qty == 0


def test_size_position_returns_zero_for_unbounded_loss(spot=100.0):
    leg_template = _call("sell", strike=110.0, premium=1.5, qty=1)
    sized_qty = risk.size_position(
        leg_template=leg_template, nav=50_000.0, max_loss_pct=0.02, spot=spot,
    )
    assert sized_qty == 0


def test_evaluate_caps_passes_when_all_three_satisfied(spot=190.0):
    legs = [_call("buy", strike=190.0, premium=2.50, qty=2)]
    result = risk.evaluate_caps(
        legs=legs, spot=spot, nav=50_000.0,
        per_trade_pct=0.02, per_ticker_pct=0.15, max_open_positions=12,
        current_ticker_concentration_dollars=0.0,
        current_open_positions=5,
    )
    assert result.ok is True
    assert result.reason is None


def test_evaluate_caps_fails_on_per_trade_overflow(spot=190.0):
    legs = [_call("buy", strike=190.0, premium=8.00, qty=2)]
    # max_loss = $1600; cap = $1000.
    result = risk.evaluate_caps(
        legs=legs, spot=spot, nav=50_000.0,
        per_trade_pct=0.02, per_ticker_pct=0.15, max_open_positions=12,
        current_ticker_concentration_dollars=0.0,
        current_open_positions=5,
    )
    assert result.ok is False
    assert result.reason == "skipped_max_loss_cap"


def test_evaluate_caps_fails_on_ticker_concentration(spot=190.0):
    legs = [_call("buy", strike=190.0, premium=2.50, qty=1)]
    # max_loss = $250. Ticker cap = 15% of $50k = $7500. We already have
    # $7400 deployed in this ticker → $7650 > $7500 → reject.
    result = risk.evaluate_caps(
        legs=legs, spot=spot, nav=50_000.0,
        per_trade_pct=0.02, per_ticker_pct=0.15, max_open_positions=12,
        current_ticker_concentration_dollars=7400.0,
        current_open_positions=5,
    )
    assert result.ok is False
    assert result.reason == "skipped_ticker_concentration"


def test_evaluate_caps_fails_on_max_open_positions(spot=190.0):
    legs = [_call("buy", strike=190.0, premium=2.50, qty=1)]
    result = risk.evaluate_caps(
        legs=legs, spot=spot, nav=50_000.0,
        per_trade_pct=0.02, per_ticker_pct=0.15, max_open_positions=12,
        current_ticker_concentration_dollars=0.0,
        current_open_positions=12,
    )
    assert result.ok is False
    assert result.reason == "skipped_max_positions"


def test_evaluate_caps_checks_in_order_per_trade_first(spot=190.0):
    """If both per-trade and ticker would fail, per-trade is reported (it's
    the cheapest to fix at the prompt level)."""
    legs = [_call("buy", strike=190.0, premium=10.00, qty=1)]
    result = risk.evaluate_caps(
        legs=legs, spot=spot, nav=50_000.0,
        per_trade_pct=0.02, per_ticker_pct=0.15, max_open_positions=12,
        current_ticker_concentration_dollars=7400.0,
        current_open_positions=12,
    )
    assert result.ok is False
    assert result.reason == "skipped_max_loss_cap"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_v2_risk.py -v`
Expected: FAIL on the 9 new tests — `AttributeError: module 'bullbot.v2.risk' has no attribute 'size_position'`.

- [ ] **Step 3: Implement sizing + caps**

First, add `from dataclasses import dataclass` to the existing import block at the top of `bullbot/v2/risk.py`.

Then append to the end of `bullbot/v2/risk.py`:

```python
@dataclass(frozen=True)
class CapEvalResult:
    ok: bool
    reason: str | None  # 'skipped_max_loss_cap' / 'skipped_ticker_concentration' / 'skipped_max_positions'


def size_position(
    *,
    leg_template: OptionLeg,
    nav: float,
    max_loss_pct: float,
    spot: float,
) -> int:
    """Return the largest integer qty such that compute_max_loss for that
    qty is ≤ (nav × max_loss_pct). Returns 0 if even 1 contract exceeds the
    cap or if the single-unit loss is unbounded.

    Operates on single-leg structures only — multi-leg sizing is handled by
    scaling the LLM's qty_ratios proportionally and re-running this for the
    primary leg. That logic lives in vehicle.py (C.3); risk.py exposes only
    the atomic single-leg sizer.
    """
    cap_dollars = nav * max_loss_pct
    unit_leg = OptionLeg(
        action=leg_template.action, kind=leg_template.kind,
        strike=leg_template.strike, expiry=leg_template.expiry,
        qty=1, entry_price=leg_template.entry_price,
        net_basis=leg_template.net_basis,
    )
    unit_loss = compute_max_loss([unit_leg], spot=spot)
    if math.isinf(unit_loss) or unit_loss <= 0:
        return 0
    return int(cap_dollars // unit_loss)


def evaluate_caps(
    *,
    legs: list[OptionLeg],
    spot: float,
    nav: float,
    per_trade_pct: float,
    per_ticker_pct: float,
    max_open_positions: int,
    current_ticker_concentration_dollars: float,
    current_open_positions: int,
) -> CapEvalResult:
    """Run the three Phase C caps in priority order:
        1. per-trade max-loss cap
        2. per-ticker concentration cap
        3. total open-positions cap
    First failure short-circuits and is reported. All three pass → ok=True."""
    proposed_loss = compute_max_loss(legs, spot=spot)
    if proposed_loss > nav * per_trade_pct:
        return CapEvalResult(ok=False, reason="skipped_max_loss_cap")
    new_ticker_concentration = current_ticker_concentration_dollars + proposed_loss
    if new_ticker_concentration > nav * per_ticker_pct:
        return CapEvalResult(ok=False, reason="skipped_ticker_concentration")
    if current_open_positions + 1 > max_open_positions:
        return CapEvalResult(ok=False, reason="skipped_max_positions")
    return CapEvalResult(ok=True, reason=None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_v2_risk.py -v`
Expected: PASS (24 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/risk.py tests/unit/test_v2_risk.py
git commit -m "feat(v2/c0): risk.size_position + risk.evaluate_caps three-cap evaluator"
```

---

## Task 9: Full Phase C.0 regression check

**Files:** none (test-only verification step)

- [ ] **Step 1: Run the entire unit + integration suite**

Run: `pytest tests/unit tests/integration -q`
Expected: All previously-passing tests still pass; the three new test files (test_v2_migrations_phase_c0.py, test_v2_positions.py, test_v2_risk.py) contribute a combined 46 passing tests.

- [ ] **Step 2: Static-import sanity check**

Run: `python -c "from bullbot.v2 import positions, risk; print(positions.OptionLeg, risk.compute_max_loss)"`
Expected: Prints `<class 'bullbot.v2.positions.OptionLeg'> <function compute_max_loss at 0x...>`.

- [ ] **Step 3: Idempotent-migration check (apply_schema on an existing DB)**

Run:
```bash
python -c "
import sqlite3
from bullbot.db.migrations import apply_schema
c = sqlite3.connect(':memory:')
apply_schema(c)
apply_schema(c)  # must be idempotent
rows = c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'v2_%' ORDER BY name\").fetchall()
print([r[0] for r in rows])
"
```
Expected: prints
```
['v2_chain_snapshots', 'v2_paper_trades', 'v2_position_events', 'v2_position_legs', 'v2_position_mtm', 'v2_positions']
```

- [ ] **Step 4: Commit a marker note (optional but recommended)**

If you want a paper trail that C.0 is complete:

```bash
git commit --allow-empty -m "chore(v2/c0): Phase C.0 complete — schema + positions + risk landed"
```

---

## What this gets Dan, in plain language

C.0 lays no trades, picks no vehicles, and changes nothing on the dashboard. It builds the foundation that everything else in Phase C plugs into: the new tables exist in the database, the `Position` and `OptionLeg` primitives can be opened/closed/queried/event-recorded, the wheel's net-basis math works correctly when a CSP gets assigned, and the three risk caps + structure-aware max-loss math are tested against every atomic structure Phase C will trade.

When this lands, the next plan (`C.1 — chains.py`) can immediately start writing real Yahoo and Black-Scholes prices into `entry_price` and `exit_price` fields without any schema or primitive work in the way.
