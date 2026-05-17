# Bull-Bot v2 Phase C.3b — Exits module (`exits.py`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `bullbot/v2/exits.py` — the deterministic exit-rule evaluator that runs daily against every open `Position`. One public entry point: `evaluate(conn, position, signal, spot, atr_14, today, asof_ts) → ExitAction`. Encodes all the per-intent exit logic from the design + Grok findings: safety-stop, profit-target/stop, signal-flip, time-stop, credit profit-take, wheel-style assignment / called-away / exercise / expired-worthless transitions, and post-assignment exit-plan derivation. After this lands, `vehicle.py` (C.3c) only needs to call this once per held position per day; no exit logic lives in the LLM agent.

**Architecture:** Stateful module (calls into `positions.close_position` / `positions.assign_csp_to_shares` / `positions.record_event` — matches the Phase B `trader.dispatch` pattern that writes directly rather than returning a side-effect description). One main dispatcher (`evaluate`) routes by intent (`trade` vs `accumulate`); per-intent helpers handle the specific trigger branches. All P&L and stop math is **net-basis-aware** via `OptionLeg.effective_basis()` (Grok review Tier 1 Finding 1). Wheel sequencing (CSP → linked shares → covered call → called away) reuses `positions.assign_csp_to_shares` which already records the lifecycle event and adjusts the basis (shipped in C.0). New helper `compute_post_assignment_exit_plan` (Grok review Tier 2 Finding 8) derives the shares-position exit plan from the current Phase A signal at assignment time.

**Tech Stack:** Python 3.11+, stdlib `datetime` and `dataclasses`, existing `bullbot.v2.positions` (Position, OptionLeg, close_position, assign_csp_to_shares, record_event), existing `bullbot.v2.signals.DirectionalSignal`, `pytest`. No new third-party dependencies. No DB schema changes.

**Spec reference:** [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-design.md`](../specs/2026-05-16-phase-c-vehicle-agent-design.md) section 4.7 (primary spec — exit-rule evaluator). [`docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md`](../specs/2026-05-16-phase-c-vehicle-agent-grok-review-response.md) Tier 1 Finding 1 (net basis), Tier 2 Finding 6 (credit profit-take), Tier 2 Finding 8 (post-assignment exit plan).

---

## Pre-flight assumptions verified before writing tasks

- **`bullbot.v2.positions` exports** `Position`, `OptionLeg`, `close_position`, `record_event`, `assign_csp_to_shares`, `VALID_CLOSE_REASONS` (verified from C.0 merge).
- **`bullbot.v2.signals.DirectionalSignal`** has `.direction` (`bullish|bearish|chop|no_edge`), `.confidence` (0-1), `.horizon_days`.
- **`OptionLeg.effective_basis()`** returns `net_basis` when non-None, else `entry_price` (shipped in C.0 Task 2).
- **`positions.close_position`** signature is `(conn, *, position_id, closed_ts, close_reason, leg_exit_prices)` where `leg_exit_prices` is `dict[int, float]` per-leg exit price.
- **`positions.assign_csp_to_shares`** signature already accepts `intent, profit_target_price, stop_price, time_stop_dte, nearest_leg_expiry_dte` as kwargs — `compute_post_assignment_exit_plan` outputs those exact field names.
- **Close-reason enum** in `positions.VALID_CLOSE_REASONS` already includes all the reasons this module needs: `'profit_target', 'stop', 'time_stop', 'signal_flip', 'credit_profit_take', 'assigned', 'called_away', 'exercised', 'expired_worthless', 'safety_stop'`.

---

## File Structure

| Path | Responsibility | Status |
|---|---|---|
| `bullbot/v2/exits.py` | `ExitAction` dataclass + action enums + `evaluate` dispatcher + per-intent helpers + `compute_post_assignment_exit_plan` + safety-stop + credit-detection helpers. | **Create** |
| `tests/unit/test_v2_exits.py` | Unit tests per task — table-driven per (intent × trigger) combination, plus wheel-state-machine scenarios. | **Create** |
| `bullbot/v2/positions.py` | Unchanged. | — |
| `bullbot/v2/signals.py` | Unchanged. | — |
| `bullbot/db/migrations.py` | Unchanged. (No schema additions — all needed fields landed in C.0.) | — |

Module size target: < 350 LOC (larger than other v2 modules because of branch-heavy logic; if it pushes past 350, split into `exits.py` + `exits_wheel.py` in a follow-up).

---

## Task 1: `ExitAction` dataclass + action constants + module skeleton

**Files:**
- Create: `bullbot/v2/exits.py`
- Create: `tests/unit/test_v2_exits.py`

The dispatcher returns an `ExitAction(kind, reason, linked_position_id)`. Valid kinds enumerate every possible outcome of a daily evaluation:
- `hold` — no change; position stays open.
- `closed_profit_target`, `closed_stop`, `closed_signal_flip`, `closed_time_stop`, `closed_credit_profit_take`, `closed_safety_stop` — six trade-intent exit reasons.
- `assigned_to_shares`, `called_away`, `exercised_to_shares`, `expired_worthless` — four accumulate-intent at-expiry outcomes.

`linked_position_id` is non-None only for outcomes that create a new linked position (assignment, exercise).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_v2_exits.py`:

```python
"""Unit tests for bullbot.v2.exits — deterministic exit-rule evaluator."""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.v2 import exits, positions
from bullbot.v2.signals import DirectionalSignal


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def test_exitaction_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be one of"):
        exits.ExitAction(kind="explode", reason="boom")


def test_exitaction_defaults_reason_to_empty_string():
    action = exits.ExitAction(kind="hold")
    assert action.reason == ""
    assert action.linked_position_id is None


def test_exitaction_carries_linked_position_id_for_assignment():
    action = exits.ExitAction(
        kind="assigned_to_shares", reason="CSP ITM at expiry",
        linked_position_id=42,
    )
    assert action.linked_position_id == 42


def test_action_kinds_constant_includes_all_trade_and_accumulate_outcomes():
    expected = {
        "hold",
        "closed_profit_target", "closed_stop", "closed_signal_flip",
        "closed_time_stop", "closed_credit_profit_take", "closed_safety_stop",
        "assigned_to_shares", "called_away", "exercised_to_shares",
        "expired_worthless",
    }
    assert set(exits.ACTION_KINDS) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bullbot.v2.exits'`.

- [ ] **Step 3: Implement the skeleton**

Create `bullbot/v2/exits.py`:

```python
"""Deterministic exit-rule evaluator for v2 Phase C.

Public entry: evaluate(conn, position, signal, spot, atr_14, today, asof_ts).
Routes by Position.intent ('trade' vs 'accumulate') and returns an ExitAction
describing what (if anything) happened. For accumulate-intent positions whose
nearest leg expires today, may also invoke positions.assign_csp_to_shares or
positions.record_event to advance the wheel state machine.

All P&L and stop math uses OptionLeg.effective_basis() (Grok review Tier 1
Finding 1) so positions born from assignment compare against net_basis, not
the raw strike.
"""
from __future__ import annotations

from dataclasses import dataclass

ACTION_KINDS = (
    "hold",
    # trade-intent exits
    "closed_profit_target",
    "closed_stop",
    "closed_signal_flip",
    "closed_time_stop",
    "closed_credit_profit_take",
    "closed_safety_stop",
    # accumulate-intent at-expiry transitions
    "assigned_to_shares",
    "called_away",
    "exercised_to_shares",
    "expired_worthless",
)


@dataclass(frozen=True)
class ExitAction:
    kind: str
    reason: str = ""
    linked_position_id: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise ValueError(f"kind must be one of {ACTION_KINDS}; got {self.kind!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/exits.py tests/unit/test_v2_exits.py
git commit -m "feat(v2/c3b): ExitAction dataclass + ACTION_KINDS constants"
```

---

## Task 2: `_position_pnl_pct` — net-basis-aware unrealized P&L

**Files:**
- Modify: `bullbot/v2/exits.py` (append `_position_pnl_pct`)
- Modify: `tests/unit/test_v2_exits.py` (append P&L helper tests)

For share legs born from assignment (net_basis non-None), unrealized-loss percent must use `effective_basis()`, not the raw strike or `entry_price`. For a long shares position with `entry_price=100` and `net_basis=98`: at spot=$92, the unrealized loss vs basis is `(92-98)/98 = -6.1%`, not `(92-100)/100 = -8%`. This 2-percentage-point difference matters at the 15% safety stop boundary.

For multi-leg structures the helper is only used as a sanity-check input to the safety-stop on share-only positions (long_shares / short_shares). The trade-intent profit_target/stop are evaluated against underlying spot vs stored prices (no P&L percent needed); credit_profit_take has its own dedicated path. So `_position_pnl_pct` is share-leg-only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_exits.py`:

```python
def _share_position(conn, qty=100, entry_price=100.0, net_basis=None,
                    intent="trade", structure_kind="long_shares",
                    profit_target_price=None, stop_price=None,
                    time_stop_dte=None, nearest_leg_expiry_dte=None,
                    rationale="", ticker="AAPL"):
    leg = positions.OptionLeg(
        action="buy", kind="share", strike=None, expiry=None,
        qty=qty, entry_price=entry_price, net_basis=net_basis,
    )
    return positions.open_position(
        conn,
        ticker=ticker, intent=intent, structure_kind=structure_kind,
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=profit_target_price, stop_price=stop_price,
        time_stop_dte=time_stop_dte,
        assignment_acceptable=(intent == "accumulate"),
        nearest_leg_expiry_dte=nearest_leg_expiry_dte,
        rationale=rationale,
    )


def test_position_pnl_pct_uses_entry_price_when_net_basis_is_none(conn):
    pos = _share_position(conn, qty=100, entry_price=100.0, net_basis=None)
    # spot = 95, no net_basis -> (95-100)/100 = -5%
    pct = exits._position_pnl_pct(position=pos, spot=95.0)
    assert pct == pytest.approx(-0.05)


def test_position_pnl_pct_uses_net_basis_when_set(conn):
    """Grok Tier 1 Finding 1: assigned shares carry net_basis (lower than
    strike). P&L must compute against net_basis, not entry_price."""
    pos = _share_position(conn, qty=100, entry_price=100.0, net_basis=98.0)
    # spot = 92, net_basis=98 -> (92-98)/98 = -6.12%
    # (vs (92-100)/100 = -8% if we erroneously used entry_price)
    pct = exits._position_pnl_pct(position=pos, spot=92.0)
    assert pct == pytest.approx((92.0 - 98.0) / 98.0)


def test_position_pnl_pct_for_short_shares_inverts_sign(conn):
    pos = _share_position(
        conn, qty=100, entry_price=100.0, structure_kind="short_shares",
    )
    # Override: short shares means we WANT spot to go DOWN. Spot=110 is a loss.
    # Manually create with action=sell:
    leg = positions.OptionLeg(
        action="sell", kind="share", strike=None, expiry=None,
        qty=100, entry_price=100.0,
    )
    short_pos = positions.open_position(
        conn,
        ticker="MSFT", intent="trade", structure_kind="short_shares",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=False,
        nearest_leg_expiry_dte=None, rationale="",
    )
    pct = exits._position_pnl_pct(position=short_pos, spot=110.0)
    # Short at 100, spot at 110 -> 10% adverse loss -> -0.10
    assert pct == pytest.approx(-0.10)


def test_position_pnl_pct_returns_zero_for_non_share_position(conn):
    """For option-only positions (no share leg), P&L percent is not defined
    in basis-percent terms — return 0.0 (the safety-stop won't trigger;
    intent-specific exits handle option positions separately)."""
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=100.0, expiry="2026-06-19",
        qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="long_call",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=False,
        nearest_leg_expiry_dte=30, rationale="",
    )
    pct = exits._position_pnl_pct(position=pos, spot=95.0)
    assert pct == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: FAIL on the 4 new tests with `AttributeError: module 'bullbot.v2.exits' has no attribute '_position_pnl_pct'`.

- [ ] **Step 3: Implement `_position_pnl_pct`**

Append to `bullbot/v2/exits.py`:

```python
from bullbot.v2.positions import Position


def _position_pnl_pct(*, position: Position, spot: float) -> float:
    """Net-basis-aware unrealized P&L percent for a share-only position.

    Returns 0.0 for option-only positions — those are handled by the
    intent-specific exit paths, not by the safety-stop.

    For long shares: (spot - basis) / basis.
    For short shares: (basis - spot) / basis.

    `basis` is `OptionLeg.effective_basis()` — net_basis when non-None
    (assigned shares carry net_basis = strike - csp_credit/100), else
    entry_price (Grok review Tier 1 Finding 1).
    """
    share_legs = [leg for leg in position.legs if leg.kind == "share"]
    if not share_legs or len(position.legs) != 1:
        # Multi-leg or option-only — no basis-percent semantics here.
        return 0.0
    leg = share_legs[0]
    basis = leg.effective_basis()
    if basis <= 0:
        return 0.0
    if leg.action == "buy":
        return (spot - basis) / basis
    # leg.action == "sell" (short shares)
    return (basis - spot) / basis
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: PASS (8 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/exits.py tests/unit/test_v2_exits.py
git commit -m "feat(v2/c3b): _position_pnl_pct uses effective_basis (Grok T1 F1)"
```

---

## Task 3: Safety-stop (15% adverse from effective basis, intent-independent)

**Files:**
- Modify: `bullbot/v2/exits.py` (append `SAFETY_STOP_PCT` + `_check_safety_stop`)
- Modify: `tests/unit/test_v2_exits.py` (append safety-stop tests)

The safety-stop fires when a share-only position's unrealized loss exceeds 15% of effective basis. This is the "last-resort" check that runs regardless of intent — even an `accumulate`-intent shares position holding through normal turbulence will be force-closed if the underlying gaps 15%+ adverse. Option positions are not subject to this rule (the per-trade max-loss cap from `risk.py` already bounded their downside at entry).

When triggered, the helper calls `positions.close_position` with reason `'safety_stop'` and returns `ExitAction(kind='closed_safety_stop', ...)`. Returns `None` when not triggered so the caller can fall through to intent-specific logic.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_exits.py`:

```python
def test_check_safety_stop_returns_none_when_loss_under_threshold(conn):
    pos = _share_position(conn, qty=100, entry_price=100.0)
    # spot = 90 -> -10% loss, under the 15% threshold
    action = exits._check_safety_stop(
        conn, position=pos, spot=90.0, now_ts=1_700_001_000,
    )
    assert action is None


def test_check_safety_stop_triggers_at_15pct_adverse_inclusive(conn):
    """Loss exactly 15% (boundary) triggers the stop — uses <= -0.15."""
    pos = _share_position(conn, qty=100, entry_price=100.0)
    action = exits._check_safety_stop(
        conn, position=pos, spot=85.0, now_ts=1_700_001_000,
    )
    assert action is not None
    assert action.kind == "closed_safety_stop"
    assert "15" in action.reason


def test_check_safety_stop_uses_net_basis_when_set(conn):
    """Position with net_basis=98 — safety stop at -15% triggers at 83.30,
    not at 85.00 (which would be -15% of entry_price=100)."""
    pos = _share_position(conn, qty=100, entry_price=100.0, net_basis=98.0)
    # spot = 85 -> (85-98)/98 = -13.27% -> NOT triggered yet
    action_85 = exits._check_safety_stop(
        conn, position=pos, spot=85.0, now_ts=1_700_001_000,
    )
    assert action_85 is None
    # spot = 83 -> (83-98)/98 = -15.31% -> triggered
    action_83 = exits._check_safety_stop(
        conn, position=pos, spot=83.0, now_ts=1_700_001_000,
    )
    assert action_83 is not None
    assert action_83.kind == "closed_safety_stop"


def test_check_safety_stop_closes_position_in_db_with_correct_reason(conn):
    pos = _share_position(conn, qty=100, entry_price=100.0)
    exits._check_safety_stop(
        conn, position=pos, spot=80.0, now_ts=1_700_001_000,
    )
    reloaded = positions.load_position(conn, pos.id)
    assert reloaded.closed_ts == 1_700_001_000
    assert reloaded.close_reason == "safety_stop"
    assert reloaded.legs[0].exit_price == 80.0


def test_check_safety_stop_does_not_trigger_on_option_only_position(conn):
    """Long call position is not subject to safety-stop — risk.py cap
    handles option downside at entry time."""
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=100.0, expiry="2026-06-19",
        qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="long_call",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=False,
        nearest_leg_expiry_dte=30, rationale="",
    )
    action = exits._check_safety_stop(
        conn, position=pos, spot=50.0, now_ts=1_700_001_000,
    )
    assert action is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: FAIL on the 5 new tests with `AttributeError: module 'bullbot.v2.exits' has no attribute '_check_safety_stop'`.

- [ ] **Step 3: Implement safety-stop**

Append to `bullbot/v2/exits.py`:

```python
import sqlite3

SAFETY_STOP_PCT = 0.15  # 15% adverse from effective basis


def _check_safety_stop(
    conn: sqlite3.Connection, *, position: Position, spot: float, now_ts: int,
) -> ExitAction | None:
    """Force-close a share-only position whose loss exceeds SAFETY_STOP_PCT
    of effective basis. Returns None when not triggered.

    Independent of intent — even an accumulate position will be liquidated
    on a 15%+ adverse gap. Option-only positions are not subject to this
    rule (risk.py's per-trade cap already bounded their downside at entry).
    """
    pnl_pct = _position_pnl_pct(position=position, spot=spot)
    if pnl_pct == 0.0:
        # Either option-only or basis<=0 — safety stop doesn't apply.
        return None
    if pnl_pct > -SAFETY_STOP_PCT:
        return None  # within tolerance
    # Trigger: close at spot.
    leg = position.legs[0]
    positions.close_position(
        conn,
        position_id=position.id,
        closed_ts=now_ts,
        close_reason="safety_stop",
        leg_exit_prices={leg.id: spot},
    )
    return ExitAction(
        kind="closed_safety_stop",
        reason=f"pnl {pnl_pct:.1%} exceeds {SAFETY_STOP_PCT:.0%} safety stop",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: PASS (13 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/exits.py tests/unit/test_v2_exits.py
git commit -m "feat(v2/c3b): _check_safety_stop — 15% adverse force-close using effective basis"
```

---

## Task 4: Trade-intent — profit_target + stop_price triggers

**Files:**
- Modify: `bullbot/v2/exits.py` (append `_check_trade_price_triggers`)
- Modify: `tests/unit/test_v2_exits.py` (append target/stop tests)

For `intent='trade'` positions, the runner closes when underlying spot crosses the stored `profit_target_price` (in the favorable direction) or `stop_price` (in the adverse direction). Direction is inferred from the position structure: bullish structures (long_call, bull_call_spread, long_shares, etc.) win when spot goes UP; bearish structures (long_put, bear_put_spread, short_shares, etc.) win when spot goes DOWN.

The simplest signal: compare against the position's `entry_price` of its primary leg. If profit_target_price is above entry, position is bullish; below entry, bearish. Same for stop_price (below entry = bullish stop; above entry = bearish stop).

When triggered, the helper calls `positions.close_position` with the matching reason and returns `ExitAction`. Returns `None` when neither trigger fires.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_exits.py`:

```python
def _trade_long_position(conn, profit_target_price=200.0, stop_price=180.0,
                          structure_kind="long_call"):
    """Helper: bullish trade-intent option position with target/stop above/below spot=190."""
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=190.0, expiry="2026-06-19",
        qty=1, entry_price=2.50,
    )
    return positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind=structure_kind,
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=profit_target_price, stop_price=stop_price,
        time_stop_dte=21, assignment_acceptable=False,
        nearest_leg_expiry_dte=30, rationale="",
    )


def test_check_trade_price_triggers_fires_on_profit_target_for_bullish(conn):
    pos = _trade_long_position(conn, profit_target_price=200.0, stop_price=180.0)
    action = exits._check_trade_price_triggers(
        conn, position=pos, spot=200.5, now_ts=1_700_001_000,
    )
    assert action is not None
    assert action.kind == "closed_profit_target"


def test_check_trade_price_triggers_fires_on_stop_for_bullish(conn):
    pos = _trade_long_position(conn, profit_target_price=200.0, stop_price=180.0)
    action = exits._check_trade_price_triggers(
        conn, position=pos, spot=179.0, now_ts=1_700_001_000,
    )
    assert action is not None
    assert action.kind == "closed_stop"


def test_check_trade_price_triggers_returns_none_between_target_and_stop(conn):
    pos = _trade_long_position(conn, profit_target_price=200.0, stop_price=180.0)
    action = exits._check_trade_price_triggers(
        conn, position=pos, spot=190.0, now_ts=1_700_001_000,
    )
    assert action is None


def test_check_trade_price_triggers_handles_bearish_structure(conn):
    """Bearish: profit_target_price BELOW entry (we want underlying to fall),
    stop_price ABOVE entry. Trigger logic must invert."""
    leg = positions.OptionLeg(
        action="buy", kind="put", strike=180.0, expiry="2026-06-19",
        qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="long_put",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=170.0, stop_price=185.0,
        time_stop_dte=21, assignment_acceptable=False,
        nearest_leg_expiry_dte=30, rationale="",
    )
    # spot drops to 168 -> profit target hit (we wanted underlying to fall)
    action_profit = exits._check_trade_price_triggers(
        conn, position=pos, spot=168.0, now_ts=1_700_001_000,
    )
    assert action_profit is not None
    assert action_profit.kind == "closed_profit_target"


def test_check_trade_price_triggers_handles_bearish_stop(conn):
    leg = positions.OptionLeg(
        action="buy", kind="put", strike=180.0, expiry="2026-06-19",
        qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="long_put",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=170.0, stop_price=185.0,
        time_stop_dte=21, assignment_acceptable=False,
        nearest_leg_expiry_dte=30, rationale="",
    )
    # spot rallies to 186 -> stop hit (we wanted underlying to fall but it rose)
    action_stop = exits._check_trade_price_triggers(
        conn, position=pos, spot=186.0, now_ts=1_700_001_000,
    )
    assert action_stop is not None
    assert action_stop.kind == "closed_stop"


def test_check_trade_price_triggers_returns_none_when_no_target_or_stop_set(conn):
    """If both profit_target_price and stop_price are None, can't evaluate
    -> return None and let other triggers handle the position."""
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=190.0, expiry="2026-06-19",
        qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="long_call",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=21, assignment_acceptable=False,
        nearest_leg_expiry_dte=30, rationale="",
    )
    action = exits._check_trade_price_triggers(
        conn, position=pos, spot=200.0, now_ts=1_700_001_000,
    )
    assert action is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: FAIL on the 6 new tests with `AttributeError: module 'bullbot.v2.exits' has no attribute '_check_trade_price_triggers'`.

- [ ] **Step 3: Implement `_check_trade_price_triggers`**

Append to `bullbot/v2/exits.py`:

```python
def _is_bullish_target(*, profit_target_price: float, stop_price: float | None) -> bool:
    """A target ABOVE the stop is a bullish position (we want underlying up)."""
    if stop_price is None:
        # Can't infer direction reliably; default to bullish if target > 0.
        return profit_target_price > 0
    return profit_target_price > stop_price


def _check_trade_price_triggers(
    conn: sqlite3.Connection, *, position: Position, spot: float, now_ts: int,
) -> ExitAction | None:
    """Close when underlying tags the stored profit_target_price or stop_price.

    Direction (bullish vs bearish) is inferred from profit_target_price vs
    stop_price (bullish: target > stop; bearish: target < stop). Returns
    None when neither trigger fires or when both prices are unset.
    """
    pt = position.profit_target_price
    sp = position.stop_price
    if pt is None and sp is None:
        return None

    bullish = _is_bullish_target(
        profit_target_price=pt if pt is not None else float("inf"),
        stop_price=sp,
    ) if pt is not None else (sp is not None and spot > sp)

    triggered_kind: str | None = None
    triggered_reason: str = ""

    if pt is not None and bullish and spot >= pt:
        triggered_kind = "closed_profit_target"
        triggered_reason = f"spot {spot:.2f} >= profit_target {pt:.2f}"
    elif pt is not None and (not bullish) and spot <= pt:
        triggered_kind = "closed_profit_target"
        triggered_reason = f"spot {spot:.2f} <= profit_target {pt:.2f}"
    elif sp is not None and bullish and spot <= sp:
        triggered_kind = "closed_stop"
        triggered_reason = f"spot {spot:.2f} <= stop {sp:.2f}"
    elif sp is not None and (not bullish) and spot >= sp:
        triggered_kind = "closed_stop"
        triggered_reason = f"spot {spot:.2f} >= stop {sp:.2f}"

    if triggered_kind is None:
        return None

    # Map kind to close_reason (drop the "closed_" prefix).
    close_reason = triggered_kind.removeprefix("closed_")
    leg_exit_prices = {leg.id: spot for leg in position.legs if leg.kind == "share"}
    # For option legs, exit price isn't computable here (no chain access);
    # mark as 0.0 — runner_c will refine via chains.price_leg before final P&L.
    for leg in position.legs:
        if leg.kind != "share":
            leg_exit_prices[leg.id] = 0.0
    positions.close_position(
        conn,
        position_id=position.id,
        closed_ts=now_ts,
        close_reason=close_reason,
        leg_exit_prices=leg_exit_prices,
    )
    return ExitAction(kind=triggered_kind, reason=triggered_reason)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: PASS (19 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/exits.py tests/unit/test_v2_exits.py
git commit -m "feat(v2/c3b): _check_trade_price_triggers — profit_target + stop, direction-aware"
```

---

## Task 5: Trade-intent — signal_flip trigger

**Files:**
- Modify: `bullbot/v2/exits.py` (append `_check_signal_flip`)
- Modify: `tests/unit/test_v2_exits.py` (append signal-flip tests)

A trade-intent position closes when the current Phase A signal flips to the opposite direction with confidence >= 0.5. Mapping:
- Position direction is bullish (target > stop) → close on `signal.direction == 'bearish'` with `confidence >= 0.5`.
- Position direction is bearish (target < stop) → close on `signal.direction == 'bullish'` with `confidence >= 0.5`.
- `chop` and `no_edge` signals do NOT trigger close (low-confidence guard).
- Same-direction signals do NOT trigger (we're aligned).

Returns `None` when no flip.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_exits.py`:

```python
SIGNAL_FLIP_CONFIDENCE = 0.5


def _signal(direction: str, confidence: float = 0.7, asof_ts: int = 1_700_000_000):
    return DirectionalSignal(
        ticker="AAPL", asof_ts=asof_ts, direction=direction,
        confidence=confidence, horizon_days=30, rationale="t",
        rules_version="v1.0",
    )


def test_check_signal_flip_fires_when_bullish_position_meets_bearish_signal(conn):
    pos = _trade_long_position(conn)  # bullish: target=200, stop=180
    signal = _signal("bearish", confidence=0.7)
    action = exits._check_signal_flip(
        conn, position=pos, signal=signal, now_ts=1_700_001_000,
    )
    assert action is not None
    assert action.kind == "closed_signal_flip"


def test_check_signal_flip_ignores_low_confidence_opposite_signal(conn):
    """confidence < 0.5 doesn't trigger flip even when direction is opposite."""
    pos = _trade_long_position(conn)
    signal = _signal("bearish", confidence=0.4)
    action = exits._check_signal_flip(
        conn, position=pos, signal=signal, now_ts=1_700_001_000,
    )
    assert action is None


def test_check_signal_flip_ignores_same_direction_signal(conn):
    pos = _trade_long_position(conn)
    signal = _signal("bullish", confidence=0.9)
    action = exits._check_signal_flip(
        conn, position=pos, signal=signal, now_ts=1_700_001_000,
    )
    assert action is None


def test_check_signal_flip_ignores_chop_signal(conn):
    """chop / no_edge are weakening signals but not flips — don't close on these."""
    pos = _trade_long_position(conn)
    chop_signal = _signal("chop", confidence=0.9)
    assert exits._check_signal_flip(
        conn, position=pos, signal=chop_signal, now_ts=1_700_001_000,
    ) is None
    no_edge_signal = _signal("no_edge", confidence=0.9)
    assert exits._check_signal_flip(
        conn, position=pos, signal=no_edge_signal, now_ts=1_700_001_000,
    ) is None


def test_check_signal_flip_fires_at_confidence_exactly_05(conn):
    """Trigger is >=, inclusive at 0.5."""
    pos = _trade_long_position(conn)
    signal = _signal("bearish", confidence=0.5)
    action = exits._check_signal_flip(
        conn, position=pos, signal=signal, now_ts=1_700_001_000,
    )
    assert action is not None
    assert action.kind == "closed_signal_flip"


def test_check_signal_flip_fires_for_bearish_position_on_bullish_signal(conn):
    leg = positions.OptionLeg(
        action="buy", kind="put", strike=180.0, expiry="2026-06-19",
        qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="long_put",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=170.0, stop_price=185.0,
        time_stop_dte=21, assignment_acceptable=False,
        nearest_leg_expiry_dte=30, rationale="",
    )
    signal = _signal("bullish", confidence=0.8)
    action = exits._check_signal_flip(
        conn, position=pos, signal=signal, now_ts=1_700_001_000,
    )
    assert action is not None
    assert action.kind == "closed_signal_flip"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: FAIL on the 6 new tests with `AttributeError: module 'bullbot.v2.exits' has no attribute '_check_signal_flip'`.

- [ ] **Step 3: Implement `_check_signal_flip`**

Append to `bullbot/v2/exits.py`:

```python
from bullbot.v2.signals import DirectionalSignal

SIGNAL_FLIP_CONFIDENCE = 0.5
_OPPOSITE_DIRECTION = {"bullish": "bearish", "bearish": "bullish"}


def _check_signal_flip(
    conn: sqlite3.Connection, *, position: Position, signal: DirectionalSignal,
    now_ts: int,
) -> ExitAction | None:
    """Close when the current signal flips to the opposite direction with
    confidence >= SIGNAL_FLIP_CONFIDENCE. chop / no_edge are NOT flips —
    those are weakening signals; we don't churn on them."""
    pt = position.profit_target_price
    sp = position.stop_price
    if pt is None and sp is None:
        return None

    position_direction = "bullish" if _is_bullish_target(
        profit_target_price=pt if pt is not None else float("inf"),
        stop_price=sp,
    ) else "bearish"
    expected_flip = _OPPOSITE_DIRECTION.get(position_direction)
    if expected_flip is None:
        return None
    if signal.direction != expected_flip:
        return None
    if signal.confidence < SIGNAL_FLIP_CONFIDENCE:
        return None

    leg_exit_prices = {leg.id: 0.0 for leg in position.legs}
    positions.close_position(
        conn,
        position_id=position.id,
        closed_ts=now_ts,
        close_reason="signal_flip",
        leg_exit_prices=leg_exit_prices,
    )
    return ExitAction(
        kind="closed_signal_flip",
        reason=f"signal flipped to {signal.direction} @ confidence {signal.confidence:.2f}",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: PASS (25 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/exits.py tests/unit/test_v2_exits.py
git commit -m "feat(v2/c3b): _check_signal_flip — opposite-direction confidence>=0.5 close"
```

---

## Task 6: Trade-intent — time_stop trigger

**Files:**
- Modify: `bullbot/v2/exits.py` (append `_check_time_stop`)
- Modify: `tests/unit/test_v2_exits.py` (append time-stop tests)

For trade-intent positions with `time_stop_dte` set, close when the nearest leg's days-to-expiry drops below or equals the stored `time_stop_dte`. Gamma risk + theta accelerates in the last 3 weeks; this rule force-exits long-premium structures before they bleed.

Days-to-expiry per leg = `(leg.expiry_date - today).days`. Nearest leg = min across all option legs (share legs have no expiry, ignore). If all legs are shares, `time_stop` doesn't apply — return `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_exits.py`:

```python
def test_check_time_stop_fires_when_nearest_leg_dte_reaches_stored_threshold(conn):
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=190.0, expiry="2026-06-08",  # 22 days out from 2026-05-17
        qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="long_call",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=200.0, stop_price=180.0,
        time_stop_dte=21, assignment_acceptable=False,
        nearest_leg_expiry_dte=22, rationale="",
    )
    # today = 2026-05-18 -> DTE = 21 -> triggers (<=21)
    action = exits._check_time_stop(
        conn, position=pos, today=date(2026, 5, 18), now_ts=1_700_001_000,
    )
    assert action is not None
    assert action.kind == "closed_time_stop"


def test_check_time_stop_does_not_fire_when_dte_above_threshold(conn):
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=190.0, expiry="2026-06-30",  # 44 days out
        qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="long_call",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=200.0, stop_price=180.0,
        time_stop_dte=21, assignment_acceptable=False,
        nearest_leg_expiry_dte=44, rationale="",
    )
    action = exits._check_time_stop(
        conn, position=pos, today=date(2026, 5, 17), now_ts=1_700_001_000,
    )
    assert action is None


def test_check_time_stop_uses_nearest_leg_for_multi_leg_structures(conn):
    """Calendar-shaped structure (different expiries): nearest leg drives the
    time stop. Long Jun call + short Jul call -> Jun is nearest."""
    leg_near = positions.OptionLeg(
        action="buy", kind="call", strike=190.0, expiry="2026-06-08",  # 22 days
        qty=1, entry_price=2.50,
    )
    leg_far = positions.OptionLeg(
        action="sell", kind="call", strike=200.0, expiry="2026-09-19",  # ~4 months
        qty=1, entry_price=1.00,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="diagonal",
        legs=[leg_near, leg_far], opened_ts=1_700_000_000,
        profit_target_price=195.0, stop_price=180.0,
        time_stop_dte=21, assignment_acceptable=False,
        nearest_leg_expiry_dte=22, rationale="",
    )
    # today = 2026-05-18 -> nearest DTE = 21 -> triggers
    action = exits._check_time_stop(
        conn, position=pos, today=date(2026, 5, 18), now_ts=1_700_001_000,
    )
    assert action is not None


def test_check_time_stop_returns_none_when_time_stop_dte_unset(conn):
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=190.0, expiry="2026-06-08",
        qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="long_call",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=200.0, stop_price=180.0,
        time_stop_dte=None, assignment_acceptable=False,
        nearest_leg_expiry_dte=22, rationale="",
    )
    action = exits._check_time_stop(
        conn, position=pos, today=date(2026, 5, 18), now_ts=1_700_001_000,
    )
    assert action is None


def test_check_time_stop_returns_none_for_shares_only_position(conn):
    """Share legs have no expiry — time_stop doesn't apply."""
    pos = _share_position(conn, time_stop_dte=21)
    action = exits._check_time_stop(
        conn, position=pos, today=date(2026, 5, 18), now_ts=1_700_001_000,
    )
    assert action is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: FAIL on the 5 new tests with `AttributeError: module 'bullbot.v2.exits' has no attribute '_check_time_stop'`.

- [ ] **Step 3: Implement `_check_time_stop`**

Append to `bullbot/v2/exits.py`:

```python
from datetime import date as _date


def _check_time_stop(
    conn: sqlite3.Connection, *, position: Position, today: _date, now_ts: int,
) -> ExitAction | None:
    """Close when the nearest option leg's days-to-expiry <= time_stop_dte.
    No-op for share-only positions or when time_stop_dte is unset."""
    if position.time_stop_dte is None:
        return None
    option_legs = [leg for leg in position.legs if leg.kind in ("call", "put")]
    if not option_legs:
        return None
    nearest_dte = min(
        (_date.fromisoformat(leg.expiry) - today).days
        for leg in option_legs
    )
    if nearest_dte > position.time_stop_dte:
        return None
    leg_exit_prices = {leg.id: 0.0 for leg in position.legs}
    positions.close_position(
        conn,
        position_id=position.id,
        closed_ts=now_ts,
        close_reason="time_stop",
        leg_exit_prices=leg_exit_prices,
    )
    return ExitAction(
        kind="closed_time_stop",
        reason=f"nearest leg DTE {nearest_dte} <= time_stop_dte {position.time_stop_dte}",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: PASS (30 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/exits.py tests/unit/test_v2_exits.py
git commit -m "feat(v2/c3b): _check_time_stop — nearest-leg DTE <= time_stop_dte"
```

---

## Task 7: Trade-intent — credit_profit_take (Grok Tier 2 Finding 6)

**Files:**
- Modify: `bullbot/v2/exits.py` (append `_max_credit_received`, `_is_credit_structure`, `_check_credit_profit_take`)
- Modify: `tests/unit/test_v2_exits.py` (append credit profit-take tests)

For net-credit structures held with `intent='trade'` (CSP, bear-call credit spread, bull-put credit spread, iron condor), close when remaining premium ≤ 50% of max credit received. Theta decay is front-loaded; holding credit to expiry is greedy and gamma-risky. The threshold is computed from leg entry prices.

`_max_credit_received(legs)` returns the per-contract net credit in dollars (sum of `sell_leg.entry_price - buy_leg.entry_price` × 100). Returns 0.0 for net-debit structures (those aren't credit). Used both to detect "is this a credit structure" and to set the close threshold.

The CALLER (runner) is responsible for passing the current per-leg mid prices via the `current_leg_prices` parameter — exits.py doesn't fetch chains. This keeps the module pure-ish (no chain dependency).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_exits.py`:

```python
def test_max_credit_received_for_csp_is_short_put_premium():
    """CSP: short put, no other legs. Max credit = entry_price × 100 × qty."""
    leg = positions.OptionLeg(
        action="sell", kind="put", strike=100.0, expiry="2026-06-19",
        qty=1, entry_price=2.00,
    )
    assert exits._max_credit_received([leg]) == pytest.approx(200.0)


def test_max_credit_received_for_bull_put_credit_spread():
    """Sell 100p @ $2, buy 95p @ $0.50 -> net credit $1.50 * 100 = $150."""
    legs = [
        positions.OptionLeg(
            action="sell", kind="put", strike=100.0, expiry="2026-06-19",
            qty=1, entry_price=2.00,
        ),
        positions.OptionLeg(
            action="buy", kind="put", strike=95.0, expiry="2026-06-19",
            qty=1, entry_price=0.50,
        ),
    ]
    assert exits._max_credit_received(legs) == pytest.approx(150.0)


def test_max_credit_received_for_long_call_is_zero():
    """Long call is net debit, not credit."""
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=100.0, expiry="2026-06-19",
        qty=1, entry_price=2.50,
    )
    assert exits._max_credit_received([leg]) == 0.0


def test_is_credit_structure_true_for_csp_and_credit_spread():
    csp_leg = positions.OptionLeg(
        action="sell", kind="put", strike=100.0, expiry="2026-06-19",
        qty=1, entry_price=2.00,
    )
    assert exits._is_credit_structure([csp_leg]) is True


def test_is_credit_structure_false_for_long_premium():
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=100.0, expiry="2026-06-19",
        qty=1, entry_price=2.50,
    )
    assert exits._is_credit_structure([leg]) is False


def test_check_credit_profit_take_fires_when_remaining_premium_under_half(conn):
    """CSP entered for $2.00 credit. If current mid is $0.80, remaining is
    40% of max credit -> trigger (<= 50%)."""
    csp_leg = positions.OptionLeg(
        action="sell", kind="put", strike=100.0, expiry="2026-06-19",
        qty=1, entry_price=2.00,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="csp",
        legs=[csp_leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=21, assignment_acceptable=False,
        nearest_leg_expiry_dte=30, rationale="",
    )
    current_leg_prices = {pos.legs[0].id: 0.80}
    action = exits._check_credit_profit_take(
        conn, position=pos, current_leg_prices=current_leg_prices,
        now_ts=1_700_001_000,
    )
    assert action is not None
    assert action.kind == "closed_credit_profit_take"


def test_check_credit_profit_take_does_not_fire_when_above_threshold(conn):
    csp_leg = positions.OptionLeg(
        action="sell", kind="put", strike=100.0, expiry="2026-06-19",
        qty=1, entry_price=2.00,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="csp",
        legs=[csp_leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=21, assignment_acceptable=False,
        nearest_leg_expiry_dte=30, rationale="",
    )
    current_leg_prices = {pos.legs[0].id: 1.50}  # still 75% of $2 entry
    action = exits._check_credit_profit_take(
        conn, position=pos, current_leg_prices=current_leg_prices,
        now_ts=1_700_001_000,
    )
    assert action is None


def test_check_credit_profit_take_does_not_fire_for_long_premium(conn):
    leg = positions.OptionLeg(
        action="buy", kind="call", strike=100.0, expiry="2026-06-19",
        qty=1, entry_price=2.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="trade", structure_kind="long_call",
        legs=[leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=21, assignment_acceptable=False,
        nearest_leg_expiry_dte=30, rationale="",
    )
    current_leg_prices = {pos.legs[0].id: 0.50}
    action = exits._check_credit_profit_take(
        conn, position=pos, current_leg_prices=current_leg_prices,
        now_ts=1_700_001_000,
    )
    assert action is None  # not a credit structure


def test_check_credit_profit_take_only_applies_to_trade_intent(conn):
    """Accumulate-intent CSP holders WANT assignment — they don't take 50%
    profit early."""
    csp_leg = positions.OptionLeg(
        action="sell", kind="put", strike=100.0, expiry="2026-06-19",
        qty=1, entry_price=2.00,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="accumulate", structure_kind="csp",
        legs=[csp_leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=True,
        nearest_leg_expiry_dte=30, rationale="",
    )
    current_leg_prices = {pos.legs[0].id: 0.50}
    action = exits._check_credit_profit_take(
        conn, position=pos, current_leg_prices=current_leg_prices,
        now_ts=1_700_001_000,
    )
    assert action is None  # accumulate intent -> no early profit-take
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: FAIL on the 9 new tests with `AttributeError` for the three new helpers.

- [ ] **Step 3: Implement credit-detection + profit-take**

Append to `bullbot/v2/exits.py`:

```python
from bullbot.v2.positions import OptionLeg

CREDIT_PROFIT_TAKE_PCT = 0.50  # close when remaining premium <= 50% of max credit


def _max_credit_received(legs: list[OptionLeg]) -> float:
    """Per-position net credit in dollars (positive when net seller).
    Returns 0.0 when the structure is net-debit (e.g., long premium)."""
    total = 0.0
    for leg in legs:
        if leg.kind == "share":
            continue
        sign = 1.0 if leg.action == "sell" else -1.0
        total += sign * leg.entry_price * leg.qty * 100
    return max(0.0, total)


def _is_credit_structure(legs: list[OptionLeg]) -> bool:
    """True when the position was opened for net credit (CSP, IC,
    bull-put credit spread, bear-call credit spread)."""
    return _max_credit_received(legs) > 0


def _current_credit_outstanding(
    legs: list[OptionLeg], current_leg_prices: dict[int, float],
) -> float:
    """Dollar value of premium still outstanding (what we'd pay to close).
    Mirrors _max_credit_received but uses current prices instead of entry."""
    total = 0.0
    for leg in legs:
        if leg.kind == "share" or leg.id is None:
            continue
        cur = current_leg_prices.get(leg.id)
        if cur is None:
            continue
        sign = 1.0 if leg.action == "sell" else -1.0
        total += sign * cur * leg.qty * 100
    return max(0.0, total)


def _check_credit_profit_take(
    conn: sqlite3.Connection, *, position: Position,
    current_leg_prices: dict[int, float], now_ts: int,
) -> ExitAction | None:
    """Close credit trade-intent positions when remaining premium <= 50% of
    max credit received. Grok review Tier 2 Finding 6: theta is front-loaded,
    holding credit to zero is greedy + gamma-risky."""
    if position.intent != "trade":
        return None
    if not _is_credit_structure(position.legs):
        return None
    max_credit = _max_credit_received(position.legs)
    remaining = _current_credit_outstanding(position.legs, current_leg_prices)
    if remaining > max_credit * CREDIT_PROFIT_TAKE_PCT:
        return None
    leg_exit_prices = {
        leg.id: current_leg_prices.get(leg.id, 0.0) for leg in position.legs
    }
    positions.close_position(
        conn,
        position_id=position.id,
        closed_ts=now_ts,
        close_reason="credit_profit_take",
        leg_exit_prices=leg_exit_prices,
    )
    return ExitAction(
        kind="closed_credit_profit_take",
        reason=(
            f"remaining premium ${remaining:.2f} <= "
            f"{CREDIT_PROFIT_TAKE_PCT:.0%} of max credit ${max_credit:.2f}"
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: PASS (39 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/exits.py tests/unit/test_v2_exits.py
git commit -m "feat(v2/c3b): _check_credit_profit_take — 50% credit-close rule (Grok T2 F6)"
```

---

## Task 8: `compute_post_assignment_exit_plan` (Grok Tier 2 Finding 8)

**Files:**
- Modify: `bullbot/v2/exits.py` (append `PostAssignmentPlan` dataclass + `compute_post_assignment_exit_plan`)
- Modify: `tests/unit/test_v2_exits.py` (append plan-derivation tests)

When a CSP is assigned, the newly-opened shares position does NOT inherit a generic "hold until called away" plan. Instead, derive the plan from the current Phase A signal at assignment time:

- `signal.bullish` with confidence ≥ 0.5 → `intent='accumulate'`, soft stop at `net_basis − 2 × ATR`.
- `signal.bearish` with confidence ≥ 0.5 → `intent='trade'`, hard stop at `net_basis − 1 × ATR`, profit_target=None (forced liquidation path).
- `signal.chop` / `no_edge` → `intent='accumulate'`, defensive stop at `net_basis − 2 × ATR`.

Pure function — no DB, no I/O. The C.3c assignment path will call this and pass the result to `positions.assign_csp_to_shares`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_exits.py`:

```python
def test_post_assignment_plan_bullish_signal_keeps_accumulate_intent():
    """signal=bullish, confidence>=0.5 -> stay accumulate with soft stop."""
    signal = _signal("bullish", confidence=0.7)
    plan = exits.compute_post_assignment_exit_plan(
        signal=signal, net_basis=98.0, atr_14=3.0,
    )
    assert plan.intent == "accumulate"
    assert plan.profit_target_price is None
    assert plan.stop_price == pytest.approx(98.0 - 2 * 3.0)  # 92.0
    assert plan.time_stop_dte is None
    assert plan.nearest_leg_expiry_dte is None


def test_post_assignment_plan_bearish_signal_flips_to_trade():
    """signal=bearish, confidence>=0.5 -> switch to trade intent with HARD
    stop (-1 ATR) and no profit_target (forced liquidation path)."""
    signal = _signal("bearish", confidence=0.8)
    plan = exits.compute_post_assignment_exit_plan(
        signal=signal, net_basis=98.0, atr_14=3.0,
    )
    assert plan.intent == "trade"
    assert plan.profit_target_price is None
    assert plan.stop_price == pytest.approx(98.0 - 1 * 3.0)  # 95.0


def test_post_assignment_plan_chop_signal_stays_accumulate_with_defensive_stop():
    """chop / no_edge -> stay accumulate, soft stop, CC eligible on next tick."""
    chop_signal = _signal("chop", confidence=0.9)
    plan = exits.compute_post_assignment_exit_plan(
        signal=chop_signal, net_basis=98.0, atr_14=3.0,
    )
    assert plan.intent == "accumulate"
    assert plan.stop_price == pytest.approx(98.0 - 2 * 3.0)


def test_post_assignment_plan_low_confidence_bearish_treated_as_chop():
    """confidence < 0.5 -> not a strong enough signal to flip to trade.
    Behaves like chop: accumulate with defensive stop."""
    weak_bearish = _signal("bearish", confidence=0.3)
    plan = exits.compute_post_assignment_exit_plan(
        signal=weak_bearish, net_basis=98.0, atr_14=3.0,
    )
    assert plan.intent == "accumulate"


def test_post_assignment_plan_low_confidence_bullish_treated_as_chop():
    weak_bullish = _signal("bullish", confidence=0.3)
    plan = exits.compute_post_assignment_exit_plan(
        signal=weak_bullish, net_basis=98.0, atr_14=3.0,
    )
    assert plan.intent == "accumulate"
    assert plan.stop_price == pytest.approx(98.0 - 2 * 3.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: FAIL on the 5 new tests with `AttributeError: module 'bullbot.v2.exits' has no attribute 'compute_post_assignment_exit_plan'`.

- [ ] **Step 3: Implement `compute_post_assignment_exit_plan`**

Append to `bullbot/v2/exits.py`:

```python
ASSIGNMENT_BULLISH_ATR_MULT = 2.0
ASSIGNMENT_BEARISH_ATR_MULT = 1.0
ASSIGNMENT_DEFENSIVE_ATR_MULT = 2.0


@dataclass(frozen=True)
class PostAssignmentPlan:
    """Exit-plan kwargs for a newly-opened linked-shares position born from
    CSP assignment. Field names match positions.assign_csp_to_shares kwargs."""
    intent: str
    profit_target_price: float | None
    stop_price: float | None
    time_stop_dte: int | None
    nearest_leg_expiry_dte: int | None


def compute_post_assignment_exit_plan(
    *,
    signal: DirectionalSignal,
    net_basis: float,
    atr_14: float,
) -> PostAssignmentPlan:
    """Derive exit plan for the newly-opened shares position from the current
    Phase A signal at assignment time. Grok review Tier 2 Finding 8.

    Logic:
      - bullish + confidence >= 0.5 -> accumulate, soft stop -2 ATR
      - bearish + confidence >= 0.5 -> trade, hard stop -1 ATR, no profit target
      - chop / no_edge / low-confidence -> accumulate, defensive stop -2 ATR
    """
    is_confident = signal.confidence >= 0.5
    if signal.direction == "bearish" and is_confident:
        stop = net_basis - ASSIGNMENT_BEARISH_ATR_MULT * atr_14
        return PostAssignmentPlan(
            intent="trade", profit_target_price=None, stop_price=stop,
            time_stop_dte=None, nearest_leg_expiry_dte=None,
        )
    if signal.direction == "bullish" and is_confident:
        stop = net_basis - ASSIGNMENT_BULLISH_ATR_MULT * atr_14
        return PostAssignmentPlan(
            intent="accumulate", profit_target_price=None, stop_price=stop,
            time_stop_dte=None, nearest_leg_expiry_dte=None,
        )
    # chop / no_edge / low-confidence anything -> defensive accumulate
    stop = net_basis - ASSIGNMENT_DEFENSIVE_ATR_MULT * atr_14
    return PostAssignmentPlan(
        intent="accumulate", profit_target_price=None, stop_price=stop,
        time_stop_dte=None, nearest_leg_expiry_dte=None,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: PASS (44 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/exits.py tests/unit/test_v2_exits.py
git commit -m "feat(v2/c3b): compute_post_assignment_exit_plan derives shares plan from signal (Grok T2 F8)"
```

---

## Task 9: Accumulate-intent at-expiry routing

**Files:**
- Modify: `bullbot/v2/exits.py` (append `_check_accumulate_at_expiry`)
- Modify: `tests/unit/test_v2_exits.py` (append wheel-scenario tests)

When an `accumulate`-intent position's nearest leg expires today, route by leg type + moneyness:
- **Short put (CSP) ITM at expiry** (spot < strike) → `positions.assign_csp_to_shares` with plan from `compute_post_assignment_exit_plan` → returns `ExitAction(kind='assigned_to_shares', linked_position_id=...)`.
- **Short call (CC) ITM at expiry** (spot > strike): the share leg in the position is sold at the strike. Close the position with reason `called_away`. Returns `ExitAction(kind='called_away')`. (No new linked position — the shares leave the portfolio.)
- **Long ITM call at expiry** (spot > strike): exercise → open linked long-shares position at `entry_price = strike`, `net_basis = strike + (premium_paid / 100)`. Close call with reason `exercised`. Returns `ExitAction(kind='exercised_to_shares', linked_position_id=...)`.
- **OTM at expiry** (or other no-action cases) → close with reason `expired_worthless`. Returns `ExitAction(kind='expired_worthless')`.

The function is called only when nearest_leg_expiry == today. The caller (`evaluate` in Task 10) handles the "is it today?" gate.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_exits.py`:

```python
def test_accumulate_at_expiry_csp_itm_assigns_to_shares(conn):
    """CSP at strike $100, spot at $96 on expiry -> assignment, linked shares
    position opened with net_basis = 100 - 2.00 = $98."""
    csp_leg = positions.OptionLeg(
        action="sell", kind="put", strike=100.0, expiry="2026-05-17",
        qty=1, entry_price=2.00,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="accumulate", structure_kind="csp",
        legs=[csp_leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=True,
        nearest_leg_expiry_dte=0, rationale="",
    )
    signal = _signal("bullish", confidence=0.7)
    action = exits._check_accumulate_at_expiry(
        conn, position=pos, signal=signal, spot=96.0, atr_14=3.0,
        today=date(2026, 5, 17), now_ts=1_700_500_000,
    )
    assert action.kind == "assigned_to_shares"
    assert action.linked_position_id is not None
    linked = positions.load_position(conn, action.linked_position_id)
    assert linked.structure_kind == "long_shares"
    assert linked.legs[0].net_basis == pytest.approx(98.0)


def test_accumulate_at_expiry_csp_otm_expires_worthless(conn):
    """CSP at strike $100, spot at $105 on expiry -> OTM, premium kept."""
    csp_leg = positions.OptionLeg(
        action="sell", kind="put", strike=100.0, expiry="2026-05-17",
        qty=1, entry_price=2.00,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="accumulate", structure_kind="csp",
        legs=[csp_leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=True,
        nearest_leg_expiry_dte=0, rationale="",
    )
    signal = _signal("bullish", confidence=0.7)
    action = exits._check_accumulate_at_expiry(
        conn, position=pos, signal=signal, spot=105.0, atr_14=3.0,
        today=date(2026, 5, 17), now_ts=1_700_500_000,
    )
    assert action.kind == "expired_worthless"
    reloaded = positions.load_position(conn, pos.id)
    assert reloaded.close_reason == "expired_worthless"


def test_accumulate_at_expiry_covered_call_itm_called_away(conn):
    """Long 100 shares (from prior assignment) + short 105 call.
    Spot at $108 on expiry -> shares called away at 105."""
    share_leg = positions.OptionLeg(
        action="buy", kind="share", strike=None, expiry=None,
        qty=100, entry_price=100.0, net_basis=98.0,
    )
    call_leg = positions.OptionLeg(
        action="sell", kind="call", strike=105.0, expiry="2026-05-17",
        qty=1, entry_price=1.50,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="accumulate", structure_kind="covered_call",
        legs=[share_leg, call_leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=True,
        nearest_leg_expiry_dte=0, rationale="",
    )
    signal = _signal("bullish", confidence=0.7)
    action = exits._check_accumulate_at_expiry(
        conn, position=pos, signal=signal, spot=108.0, atr_14=3.0,
        today=date(2026, 5, 17), now_ts=1_700_500_000,
    )
    assert action.kind == "called_away"
    reloaded = positions.load_position(conn, pos.id)
    assert reloaded.close_reason == "called_away"


def test_accumulate_at_expiry_long_call_itm_exercises(conn):
    """Deep-ITM long call held with accumulate intent -> exercise into shares.
    Strike 100, premium $5, spot $115 at expiry. net_basis = 100 + 5 = $105."""
    call_leg = positions.OptionLeg(
        action="buy", kind="call", strike=100.0, expiry="2026-05-17",
        qty=1, entry_price=5.00,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="accumulate", structure_kind="long_call",
        legs=[call_leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=True,
        nearest_leg_expiry_dte=0, rationale="",
    )
    signal = _signal("bullish", confidence=0.7)
    action = exits._check_accumulate_at_expiry(
        conn, position=pos, signal=signal, spot=115.0, atr_14=3.0,
        today=date(2026, 5, 17), now_ts=1_700_500_000,
    )
    assert action.kind == "exercised_to_shares"
    linked = positions.load_position(conn, action.linked_position_id)
    assert linked.structure_kind == "long_shares"
    assert linked.legs[0].entry_price == 100.0
    assert linked.legs[0].net_basis == pytest.approx(105.0)


def test_accumulate_at_expiry_long_call_otm_expires_worthless(conn):
    call_leg = positions.OptionLeg(
        action="buy", kind="call", strike=100.0, expiry="2026-05-17",
        qty=1, entry_price=5.00,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="accumulate", structure_kind="long_call",
        legs=[call_leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=True,
        nearest_leg_expiry_dte=0, rationale="",
    )
    signal = _signal("bullish", confidence=0.7)
    action = exits._check_accumulate_at_expiry(
        conn, position=pos, signal=signal, spot=95.0, atr_14=3.0,
        today=date(2026, 5, 17), now_ts=1_700_500_000,
    )
    assert action.kind == "expired_worthless"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: FAIL on the 5 new tests with `AttributeError: module 'bullbot.v2.exits' has no attribute '_check_accumulate_at_expiry'`.

- [ ] **Step 3: Implement `_check_accumulate_at_expiry`**

Append to `bullbot/v2/exits.py`:

```python
def _check_accumulate_at_expiry(
    conn: sqlite3.Connection, *, position: Position, signal: DirectionalSignal,
    spot: float, atr_14: float, today: _date, now_ts: int,
) -> ExitAction:
    """Route accumulate-intent positions at the nearest leg's expiry.

    Caller (evaluate) is responsible for confirming nearest_leg_expiry == today.
    Returns one of: assigned_to_shares, called_away, exercised_to_shares,
    expired_worthless. Always advances state — either via positions.assign_csp_to_shares,
    record_event + close_position, or close_position alone.
    """
    # CSP ITM at expiry -> assignment
    short_puts = [
        leg for leg in position.legs
        if leg.kind == "put" and leg.action == "sell"
        and _date.fromisoformat(leg.expiry) == today
        and spot < leg.strike
    ]
    if short_puts:
        csp_leg = short_puts[0]
        original_credit = csp_leg.entry_price * 100.0  # per-contract dollars
        net_basis = csp_leg.strike - (original_credit / 100.0)
        plan = compute_post_assignment_exit_plan(
            signal=signal, net_basis=net_basis, atr_14=atr_14,
        )
        shares_pos = positions.assign_csp_to_shares(
            conn,
            csp_position=position,
            csp_leg_id=csp_leg.id,
            original_credit_per_contract=original_credit,
            occurred_ts=now_ts,
            intent=plan.intent,
            profit_target_price=plan.profit_target_price,
            stop_price=plan.stop_price,
            time_stop_dte=plan.time_stop_dte,
            nearest_leg_expiry_dte=plan.nearest_leg_expiry_dte,
            rationale=f"post-assignment, signal={signal.direction} confidence={signal.confidence:.2f}",
        )
        return ExitAction(
            kind="assigned_to_shares",
            reason=f"CSP @ {csp_leg.strike} assigned, net_basis ${net_basis:.2f}",
            linked_position_id=shares_pos.id,
        )

    # Short call ITM (covered call called away)
    short_calls = [
        leg for leg in position.legs
        if leg.kind == "call" and leg.action == "sell"
        and _date.fromisoformat(leg.expiry) == today
        and spot > leg.strike
    ]
    if short_calls:
        cc_leg = short_calls[0]
        positions.record_event(
            conn,
            position_id=position.id,
            event_kind="called_away",
            occurred_ts=now_ts,
            source_leg_id=cc_leg.id,
            linked_position_id=None,
            original_credit_per_contract=None,
            notes=f"spot {spot} > strike {cc_leg.strike}",
        )
        positions.close_position(
            conn,
            position_id=position.id,
            closed_ts=now_ts,
            close_reason="called_away",
            leg_exit_prices={
                cc_leg.id: 0.0,
                **{leg.id: cc_leg.strike for leg in position.legs if leg.kind == "share"},
            },
        )
        return ExitAction(
            kind="called_away",
            reason=f"CC @ {cc_leg.strike} assigned, shares sold at strike",
        )

    # Long call ITM (accumulate intent — exercise into shares)
    long_calls_itm = [
        leg for leg in position.legs
        if leg.kind == "call" and leg.action == "buy"
        and _date.fromisoformat(leg.expiry) == today
        and spot > leg.strike
    ]
    if long_calls_itm:
        call_leg = long_calls_itm[0]
        share_qty = call_leg.qty * 100
        net_basis = call_leg.strike + call_leg.entry_price  # paid premium per share
        share_leg = positions.OptionLeg(
            action="buy", kind="share", strike=None, expiry=None,
            qty=share_qty, entry_price=call_leg.strike, net_basis=net_basis,
        )
        linked = positions.open_position(
            conn,
            ticker=position.ticker, intent="accumulate", structure_kind="long_shares",
            legs=[share_leg], opened_ts=now_ts,
            profit_target_price=None, stop_price=None, time_stop_dte=None,
            assignment_acceptable=False, nearest_leg_expiry_dte=None,
            rationale=f"exercised from long_call @ {call_leg.strike}",
            linked_position_id=position.id,
        )
        positions.record_event(
            conn,
            position_id=position.id,
            event_kind="exercised",
            occurred_ts=now_ts,
            source_leg_id=call_leg.id,
            linked_position_id=linked.id,
            original_credit_per_contract=None,
            notes=f"exercised at strike {call_leg.strike}",
        )
        positions.close_position(
            conn,
            position_id=position.id,
            closed_ts=now_ts,
            close_reason="exercised",
            leg_exit_prices={call_leg.id: 0.0},
        )
        return ExitAction(
            kind="exercised_to_shares",
            reason=f"long call @ {call_leg.strike} exercised, net_basis ${net_basis:.2f}",
            linked_position_id=linked.id,
        )

    # Anything else expiring today (OTM, etc.) -> worthless
    positions.close_position(
        conn,
        position_id=position.id,
        closed_ts=now_ts,
        close_reason="expired_worthless",
        leg_exit_prices={leg.id: 0.0 for leg in position.legs},
    )
    return ExitAction(kind="expired_worthless", reason="OTM at expiry")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: PASS (49 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/exits.py tests/unit/test_v2_exits.py
git commit -m "feat(v2/c3b): _check_accumulate_at_expiry — CSP assign / CC called-away / exercise / worthless"
```

---

## Task 10: Top-level `evaluate()` dispatcher

**Files:**
- Modify: `bullbot/v2/exits.py` (append public `evaluate`)
- Modify: `tests/unit/test_v2_exits.py` (append dispatcher tests)

The single public entry point. Order:
1. Safety-stop first (intent-independent; force-close on 15%+ adverse).
2. If `intent == 'trade'`: check (price triggers → signal flip → time stop → credit profit-take) in order. First one to fire wins.
3. If `intent == 'accumulate'`: if nearest leg's expiry is today, route via `_check_accumulate_at_expiry`. Otherwise return `ExitAction(kind='hold')`.

Returns `ExitAction(kind='hold')` when no trigger fires.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_v2_exits.py`:

```python
def test_evaluate_returns_hold_when_no_trigger_fires(conn):
    pos = _trade_long_position(conn)  # target=200, stop=180
    signal = _signal("bullish", confidence=0.8)
    action = exits.evaluate(
        conn, position=pos, signal=signal, spot=190.0, atr_14=3.0,
        today=date(2026, 5, 17), asof_ts=1_700_001_000,
        current_leg_prices={},
    )
    assert action.kind == "hold"


def test_evaluate_safety_stop_takes_precedence_over_intent_triggers(conn):
    """Even an accumulate position with a stored bullish signal gets force-closed
    on a 15%+ adverse spot move."""
    pos = _share_position(
        conn, qty=100, entry_price=100.0, intent="accumulate",
    )
    signal = _signal("bullish", confidence=0.9)
    action = exits.evaluate(
        conn, position=pos, signal=signal, spot=80.0, atr_14=3.0,
        today=date(2026, 5, 17), asof_ts=1_700_001_000,
        current_leg_prices={},
    )
    assert action.kind == "closed_safety_stop"


def test_evaluate_trade_intent_routes_through_price_triggers(conn):
    pos = _trade_long_position(conn, profit_target_price=200.0, stop_price=180.0)
    signal = _signal("bullish", confidence=0.8)
    action = exits.evaluate(
        conn, position=pos, signal=signal, spot=201.0, atr_14=3.0,
        today=date(2026, 5, 17), asof_ts=1_700_001_000,
        current_leg_prices={},
    )
    assert action.kind == "closed_profit_target"


def test_evaluate_accumulate_intent_holds_when_not_at_expiry(conn):
    csp_leg = positions.OptionLeg(
        action="sell", kind="put", strike=100.0, expiry="2026-06-19",
        qty=1, entry_price=2.00,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="accumulate", structure_kind="csp",
        legs=[csp_leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=True,
        nearest_leg_expiry_dte=30, rationale="",
    )
    signal = _signal("bullish", confidence=0.8)
    action = exits.evaluate(
        conn, position=pos, signal=signal, spot=98.0, atr_14=3.0,
        today=date(2026, 5, 17), asof_ts=1_700_001_000,
        current_leg_prices={pos.legs[0].id: 1.80},
    )
    assert action.kind == "hold"


def test_evaluate_accumulate_intent_routes_at_expiry(conn):
    csp_leg = positions.OptionLeg(
        action="sell", kind="put", strike=100.0, expiry="2026-05-17",
        qty=1, entry_price=2.00,
    )
    pos = positions.open_position(
        conn,
        ticker="AAPL", intent="accumulate", structure_kind="csp",
        legs=[csp_leg], opened_ts=1_700_000_000,
        profit_target_price=None, stop_price=None,
        time_stop_dte=None, assignment_acceptable=True,
        nearest_leg_expiry_dte=0, rationale="",
    )
    signal = _signal("bullish", confidence=0.7)
    action = exits.evaluate(
        conn, position=pos, signal=signal, spot=96.0, atr_14=3.0,
        today=date(2026, 5, 17), asof_ts=1_700_001_000,
        current_leg_prices={},
    )
    assert action.kind == "assigned_to_shares"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: FAIL on the 5 new tests with `AttributeError: module 'bullbot.v2.exits' has no attribute 'evaluate'`.

- [ ] **Step 3: Implement `evaluate`**

Append to `bullbot/v2/exits.py`:

```python
def evaluate(
    conn: sqlite3.Connection, *,
    position: Position,
    signal: DirectionalSignal,
    spot: float,
    atr_14: float,
    today: _date,
    asof_ts: int,
    current_leg_prices: dict[int, float],
) -> ExitAction:
    """Run the full exit-rule pipeline for one open Position.

    Order:
      1. Safety-stop (intent-independent, 15%+ adverse).
      2. intent='trade': price triggers -> signal flip -> time stop -> credit profit-take.
      3. intent='accumulate': route at expiry, else hold.

    Returns ExitAction(kind='hold') when nothing fires.
    """
    safety = _check_safety_stop(conn, position=position, spot=spot, now_ts=asof_ts)
    if safety is not None:
        return safety

    if position.intent == "trade":
        for check in (
            lambda: _check_trade_price_triggers(
                conn, position=position, spot=spot, now_ts=asof_ts,
            ),
            lambda: _check_signal_flip(
                conn, position=position, signal=signal, now_ts=asof_ts,
            ),
            lambda: _check_time_stop(
                conn, position=position, today=today, now_ts=asof_ts,
            ),
            lambda: _check_credit_profit_take(
                conn, position=position,
                current_leg_prices=current_leg_prices, now_ts=asof_ts,
            ),
        ):
            result = check()
            if result is not None:
                return result
        return ExitAction(kind="hold")

    # intent == "accumulate"
    option_legs = [leg for leg in position.legs if leg.kind in ("call", "put")]
    if not option_legs:
        return ExitAction(kind="hold")
    nearest_expiry = min(_date.fromisoformat(leg.expiry) for leg in option_legs)
    if nearest_expiry > today:
        return ExitAction(kind="hold")
    return _check_accumulate_at_expiry(
        conn, position=position, signal=signal, spot=spot, atr_14=atr_14,
        today=today, now_ts=asof_ts,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit/test_v2_exits.py -v`
Expected: PASS (54 tests total).

- [ ] **Step 5: Commit**

```bash
git add bullbot/v2/exits.py tests/unit/test_v2_exits.py
git commit -m "feat(v2/c3b): evaluate() dispatcher orchestrating safety-stop + intent-specific paths"
```

---

## Task 11: Full regression check

**Files:** none (test-only verification step)

- [ ] **Step 1: Run the full unit suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/unit -q`
Expected: All previously-passing tests still pass; the new `test_v2_exits.py` adds 54 tests, bringing unit total from 639 → 693.

- [ ] **Step 2: Run the integration suite**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -m pytest tests/integration -q`
Expected: All 80 integration tests still pass.

- [ ] **Step 3: Static-import sanity check**

Run: `/Users/danield.runion/Projects/bull-bot/.venv/bin/python -c "from bullbot.v2 import exits; print(exits.ExitAction, exits.evaluate, exits.compute_post_assignment_exit_plan, exits.ACTION_KINDS, exits.SAFETY_STOP_PCT, exits.CREDIT_PROFIT_TAKE_PCT, exits.SIGNAL_FLIP_CONFIDENCE)"`
Expected: prints all public exports without ImportError.

- [ ] **Step 4: Optional marker commit**

```bash
git commit --allow-empty -m "chore(v2/c3b): Phase C.3b complete — exits.py landed"
```

---

## Acceptance criteria

C.3b is complete when ALL of the following hold:

1. `bullbot/v2/exits.py` exists and exports: `ExitAction`, `ACTION_KINDS`, `evaluate`, `compute_post_assignment_exit_plan`, `PostAssignmentPlan`, plus public constants `SAFETY_STOP_PCT`, `CREDIT_PROFIT_TAKE_PCT`, `SIGNAL_FLIP_CONFIDENCE`.
2. `tests/unit/test_v2_exits.py` contains the 54 tests listed in Tasks 1–10 and they all pass.
3. Full unit + integration suite is green (no regressions vs the C.3a baseline of 639 unit + 80 integration).
4. `exits.py` is under 350 LOC.
5. No new third-party dependencies introduced.
6. No DB schema changes.
7. All P&L and stop math uses `OptionLeg.effective_basis()` for share positions (Grok Tier 1 Finding 1 satisfied).
8. Credit profit-take rule only applies to `intent='trade'` (accumulate-intent credit holders ride to assignment per design).

## What this unblocks

- **C.3c (`vehicle.py`):** the vehicle agent never needs to think about exits — it picks the entry plan and stores it on the position; `exits.evaluate` enforces the plan thereafter.
- **C.5 (`runner_c.py`):** the forward daily-run loop calls `exits.evaluate` once per held position before checking flat tickers for new entries.

## Notes for the implementer

- **`compute_post_assignment_exit_plan` is pure** — no DB, no I/O. It's the only piece of C.3b that could later be reused outside the assignment path (e.g., a "what would the plan be?" preview in the dashboard).
- **Option-leg exit prices** in `close_position` calls are written as `0.0` placeholder by exits.py. The runner (C.5) refines these via `chains.price_leg` before computing final realized P&L. Don't worry about getting them right here.
- **`_check_credit_profit_take` requires `current_leg_prices`** — the runner pre-fetches per-leg prices via `chains.price_leg` and passes them in. exits.py does NOT call chains directly (keeps exits.py decoupled from the network).
- **Wheel sequencing edge case:** after a CSP gets assigned in Task 9, the next daily-run will see the new linked-shares position. That position is `intent='accumulate'` (or `'trade'` if signal was bearish at assignment). Subsequent days route via the accumulate path until either: (a) signal flips and the trade-intent stop fires, (b) safety-stop fires, or (c) a covered call is added (C.3c logic) and later gets called away.
- **No `nearest_leg_expiry_dte` recompute** at every evaluate call — stored at entry, doesn't change. (When position is opened, vehicle.py computes it; when shares are born from assignment, it's None because shares have no expiry.)
- **Worktree `.venv` path** is `/Users/danield.runion/Projects/bull-bot/.venv/bin/python`. Same note as prior phases.
