# CoveredCallOverlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CoveredCallOverlay strategy that sells short-dated calls against existing LEAPS positions, with timing filters (RSI, day return) and evolver-tuned parameters.

**Architecture:** A `long_inventory` DB table holds the user's existing LEAPS/shares. The `CoveredCallOverlay` strategy reads this inventory, checks timing filters, and generates signals to sell short calls. It follows existing strategy patterns (evaluate() → Signal → engine.step fill). The evolver optimizes strike delta, DTE, coverage ratio, and timing thresholds.

**Tech Stack:** Python stdlib. Existing Bull-Bot strategy framework, `bs_delta` for delta computation, standard walk-forward backtesting.

---

### Task 1: long_inventory table and seed data

**Files:**
- Modify: `bullbot/db/schema.sql`
- Create: `bullbot/data/long_inventory.py`
- Create: `tests/unit/test_long_inventory.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_long_inventory.py
import sqlite3
import time

import pytest

from bullbot.data import long_inventory


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE long_inventory (
            id INTEGER PRIMARY KEY,
            account TEXT NOT NULL,
            ticker TEXT NOT NULL,
            kind TEXT NOT NULL,
            strike REAL,
            expiry TEXT,
            quantity REAL NOT NULL,
            cost_basis_per REAL,
            added_at INTEGER NOT NULL,
            removed_at INTEGER
        );
    """)
    return c


def test_active_inventory_returns_only_active(conn):
    now = int(time.time())
    conn.execute(
        "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at) "
        "VALUES ('ira','TSLA','call',300,'2027-06-17',1,127.66,?)", (now,)
    )
    conn.execute(
        "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at,removed_at) "
        "VALUES ('ira','TSLA','call',200,'2026-01-01',1,50.0,?,?)", (now, now)
    )
    rows = long_inventory.active_inventory(conn, "TSLA")
    assert len(rows) == 1
    assert rows[0]["strike"] == 300


def test_active_inventory_filters_by_ticker(conn):
    now = int(time.time())
    conn.execute(
        "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at) "
        "VALUES ('ira','TSLA','call',300,'2027-06-17',1,127.66,?)", (now,)
    )
    conn.execute(
        "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at) "
        "VALUES ('ira','NVDA','call',100,'2027-06-17',1,50.0,?)", (now,)
    )
    rows = long_inventory.active_inventory(conn, "TSLA")
    assert len(rows) == 1


def test_active_inventory_filters_by_account(conn):
    now = int(time.time())
    conn.execute(
        "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at) "
        "VALUES ('ira','TSLA','call',300,'2027-06-17',1,127.66,?)", (now,)
    )
    conn.execute(
        "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at) "
        "VALUES ('taxable','TSLA','call',350,'2028-01-21',1,102.51,?)", (now,)
    )
    rows = long_inventory.active_inventory(conn, "TSLA", account="taxable")
    assert len(rows) == 1
    assert rows[0]["strike"] == 350


def test_total_coverable_contracts_calls_only(conn):
    now = int(time.time())
    conn.execute(
        "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at) "
        "VALUES ('ira','TSLA','call',300,'2027-06-17',1,127.66,?)", (now,)
    )
    conn.execute(
        "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at) "
        "VALUES ('ira','TSLA','call',350,'2027-06-17',3,110.09,?)", (now,)
    )
    conn.execute(
        "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at) "
        "VALUES ('ira','TSLA','shares',NULL,NULL,160,329.74,?)", (now,)
    )
    total = long_inventory.total_coverable_contracts(conn, "TSLA")
    # 1 + 3 calls + floor(160/100) shares = 5
    assert total == 5


def test_seed_from_fidelity_csv(conn, tmp_path):
    csv_content = (
        "Account Number,Account Name,Symbol,Description,Quantity,Last Price,Last Price Change,"
        "Current Value,Today's Gain/Loss Dollar,Today's Gain/Loss Percent,"
        "Total Gain/Loss Dollar,Total Gain/Loss Percent,Percent Of Account,"
        "Cost Basis Total,Average Cost Basis,Type\n"
        "233084385,Rollover IRA, -TSLA270617C300,TSLA JUN 17 2027 $300 CALL,1,$114.00,+$6.55,"
        "$11400.00,+$655.00,+6.09%,-$1365.67,-10.70%,5.02%,$12765.67,$127.66,Cash,\n"
        "X59844055,Dan's Brokerage,TSLA,TESLA INC COM,22.545,$362.15,+$9.73,"
        "$8164.67,+$219.36,+2.76%,-$1492.01,-15.46%,9.27%,$9656.68,$428.33,Cash,\n"
    )
    csv_path = tmp_path / "portfolio.csv"
    csv_path.write_text(csv_content)
    count = long_inventory.seed_from_fidelity_csv(conn, str(csv_path))
    assert count == 2
    rows = conn.execute("SELECT * FROM long_inventory").fetchall()
    assert len(rows) == 2
    call_row = [r for r in rows if r["kind"] == "call"][0]
    assert call_row["ticker"] == "TSLA"
    assert call_row["strike"] == 300
    assert call_row["expiry"] == "2027-06-17"
    assert call_row["account"] == "ira"
    share_row = [r for r in rows if r["kind"] == "shares"][0]
    assert share_row["ticker"] == "TSLA"
    assert share_row["quantity"] == 22.545
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_long_inventory.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Add long_inventory table to schema.sql**

Append to `bullbot/db/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS long_inventory (
    id              INTEGER PRIMARY KEY,
    account         TEXT    NOT NULL,
    ticker          TEXT    NOT NULL,
    kind            TEXT    NOT NULL,
    strike          REAL,
    expiry          TEXT,
    quantity        REAL    NOT NULL,
    cost_basis_per  REAL,
    added_at        INTEGER NOT NULL,
    removed_at      INTEGER
);
```

- [ ] **Step 4: Implement long_inventory.py**

```python
# bullbot/data/long_inventory.py
"""Long position inventory — LEAPS and shares the user holds externally."""
from __future__ import annotations

import csv
import math
import re
import sqlite3
import time
from typing import Any


_ACCOUNT_MAP = {
    "233084385": "ira",
    "X59844055": "taxable",
}

_CALL_PATTERN = re.compile(
    r"-([A-Z]+)(\d{2})(\d{2})(\d{2})C(\d+(?:\.\d+)?)"
)

_MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}


def active_inventory(
    conn: sqlite3.Connection, ticker: str, account: str | None = None,
) -> list[dict[str, Any]]:
    """Return active (not removed) long inventory for a ticker."""
    if account:
        rows = conn.execute(
            "SELECT * FROM long_inventory WHERE ticker=? AND account=? AND removed_at IS NULL",
            (ticker, account),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM long_inventory WHERE ticker=? AND removed_at IS NULL",
            (ticker,),
        ).fetchall()
    return [dict(r) for r in rows]


def total_coverable_contracts(
    conn: sqlite3.Connection, ticker: str, account: str | None = None,
) -> int:
    """Return total contracts that can be covered: call qty + floor(shares/100)."""
    inv = active_inventory(conn, ticker, account)
    total = 0
    for row in inv:
        if row["kind"] == "call":
            total += int(row["quantity"])
        elif row["kind"] == "shares":
            total += int(row["quantity"] // 100)
    return total


def seed_from_fidelity_csv(conn: sqlite3.Connection, csv_path: str) -> int:
    """Parse a Fidelity portfolio CSV and insert long call/share positions."""
    now = int(time.time())
    count = 0
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            acct_num = row.get("Account Number", "").strip()
            account = _ACCOUNT_MAP.get(acct_num)
            if account is None:
                continue

            symbol = row.get("Symbol", "").strip()
            desc = row.get("Description", "").strip()
            qty_str = row.get("Quantity", "").strip()
            cost_str = row.get("Average Cost Basis", "").strip()

            if not qty_str or not symbol:
                continue

            try:
                qty = float(qty_str.replace(",", ""))
            except ValueError:
                continue

            cost = None
            if cost_str:
                try:
                    cost = float(cost_str.replace("$", "").replace(",", ""))
                except ValueError:
                    pass

            if symbol.startswith(" -") and "CALL" in desc:
                parsed = _parse_call_description(desc)
                if parsed is None:
                    continue
                ticker, strike, expiry = parsed
                conn.execute(
                    "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at) "
                    "VALUES (?,?,'call',?,?,?,?,?)",
                    (account, ticker, strike, expiry, qty, cost, now),
                )
                count += 1
            elif not symbol.startswith(" -") and not symbol.endswith("**") and not symbol.endswith("***"):
                ticker = symbol.strip()
                if not ticker.isalpha():
                    continue
                conn.execute(
                    "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at) "
                    "VALUES (?,?,'shares',NULL,NULL,?,?,?)",
                    (account, ticker, qty, cost, now),
                )
                count += 1

    return count


def _parse_call_description(desc: str) -> tuple[str, float, str] | None:
    """Parse 'TSLA JUN 17 2027 $300 CALL' into (ticker, strike, expiry)."""
    parts = desc.split()
    if len(parts) < 6 or parts[-1] != "CALL":
        return None
    ticker = parts[0]
    try:
        month = _MONTH_MAP.get(parts[1].upper())
        if month is None:
            return None
        day = int(parts[2])
        year = int(parts[3])
        strike = float(parts[4].replace("$", ""))
        expiry = f"{year}-{month}-{day:02d}"
        return ticker, strike, expiry
    except (ValueError, IndexError):
        return None
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_long_inventory.py -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Seed the production database with Dan's positions**

```python
# Run manually after tests pass:
import sqlite3
from bullbot import config
from bullbot.data import long_inventory

conn = sqlite3.connect(str(config.DB_PATH))
conn.row_factory = sqlite3.Row

# Create table if not exists
conn.execute("""
    CREATE TABLE IF NOT EXISTS long_inventory (
        id INTEGER PRIMARY KEY, account TEXT NOT NULL, ticker TEXT NOT NULL,
        kind TEXT NOT NULL, strike REAL, expiry TEXT, quantity REAL NOT NULL,
        cost_basis_per REAL, added_at INTEGER NOT NULL, removed_at INTEGER
    )
""")

# Seed from CSV
count = long_inventory.seed_from_fidelity_csv(
    conn, "/Users/danield.runion/Downloads/Portfolio_Positions_Apr-14-2026.csv"
)
conn.commit()
print(f"Seeded {count} positions")

# Verify
for r in conn.execute("SELECT * FROM long_inventory ORDER BY account, ticker, kind").fetchall():
    print(f"  {r['account']:8s} {r['ticker']:6s} {r['kind']:6s} strike={r['strike']} exp={r['expiry']} qty={r['quantity']} cost={r['cost_basis_per']}")
conn.close()
```

- [ ] **Step 7: Commit**

```bash
git add bullbot/db/schema.sql bullbot/data/long_inventory.py tests/unit/test_long_inventory.py
git commit -m "feat: add long_inventory table and Fidelity CSV importer"
```

---

### Task 2: CoveredCallOverlay strategy class

**Files:**
- Create: `bullbot/strategies/covered_call_overlay.py`
- Create: `tests/unit/test_covered_call_overlay.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_covered_call_overlay.py
import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from bullbot.strategies.covered_call_overlay import CoveredCallOverlay
from bullbot.strategies.base import StrategySnapshot
from bullbot.data.schemas import Bar, OptionContract


def _make_bars(spot, n=60):
    """Create n daily bars ending at spot, with a 3% up day on the last bar."""
    bars = []
    base = spot / 1.03  # second-to-last bar
    for i in range(n - 1):
        price = base * (0.95 + 0.10 * i / n)
        bars.append(Bar(
            ticker="TSLA", timeframe="1d", ts=1000000 + i * 86400,
            open=price, high=price * 1.01, low=price * 0.99,
            close=price, volume=1000000, source="test",
        ))
    # Last bar: 3% up day
    bars.append(Bar(
        ticker="TSLA", timeframe="1d", ts=1000000 + (n - 1) * 86400,
        open=base, high=spot * 1.005, low=base * 0.998,
        close=spot, volume=2000000, source="test",
    ))
    return bars


def _make_chain(spot, cursor):
    """Create a minimal synthetic call chain."""
    contracts = []
    for dte in [30, 45, 60]:
        exp_ts = cursor + dte * 86400
        exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
        expiry = exp_dt.strftime("%Y-%m-%d")
        for strike_offset in range(-50, 60, 10):
            strike = round(spot + strike_offset, 2)
            if strike <= 0:
                continue
            contracts.append(OptionContract(
                ticker="TSLA", expiry=expiry, strike=strike, kind="C",
                ts=cursor, nbbo_bid=max(0.5, 20 - abs(strike_offset) * 0.3),
                nbbo_ask=max(0.6, 21 - abs(strike_offset) * 0.3),
                volume=100, open_interest=1000, iv=0.50,
            ))
    return contracts


def _make_snapshot(spot=350.0):
    cursor = 1776000000
    bars = _make_bars(spot)
    chain = _make_chain(spot, cursor)
    return StrategySnapshot(
        ticker="TSLA", asof_ts=cursor, spot=spot,
        bars_1d=bars, indicators={"rsi_14": 55.0, "sma_20": 340.0, "ema_20": 342.0, "atr_14": 8.0},
        atm_greeks={}, iv_rank=50.0, regime="chop", chain=chain,
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE long_inventory (
            id INTEGER PRIMARY KEY, account TEXT NOT NULL, ticker TEXT NOT NULL,
            kind TEXT NOT NULL, strike REAL, expiry TEXT, quantity REAL NOT NULL,
            cost_basis_per REAL, added_at INTEGER NOT NULL, removed_at INTEGER
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY, run_id TEXT, ticker TEXT, strategy_id INTEGER,
            legs TEXT, contracts INTEGER, open_price REAL, close_price REAL,
            mark_to_mkt REAL, opened_at INTEGER, closed_at INTEGER,
            pnl_realized REAL, exit_rules TEXT
        );
    """)
    now = int(time.time())
    c.execute(
        "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at) "
        "VALUES ('ira','TSLA','call',300,'2027-06-17',3,110.0,?)", (now,)
    )
    c.execute(
        "INSERT INTO long_inventory (account,ticker,kind,strike,expiry,quantity,cost_basis_per,added_at) "
        "VALUES ('ira','TSLA','shares',NULL,NULL,160,330.0,?)", (now,)
    )
    return c


def test_generates_short_call_signal(conn):
    strat = CoveredCallOverlay({
        "short_delta": 0.30, "dte_min": 25, "dte_max": 50,
        "coverage_ratio": 1.0, "min_rsi": 40, "min_day_return": 0.01,
        "iv_rank_min": 0, "roll_dte": 5, "profit_target_pct": 0.50,
        "roll_itm_delta": 0.70, "defend_time_value_min": 500,
    })
    snap = _make_snapshot(spot=350.0)
    signal = strat.evaluate(snap, [], conn=conn)
    assert signal is not None
    assert signal.intent == "open"
    assert len(signal.legs) == 1
    assert signal.legs[0].side == "short"
    assert signal.legs[0].kind == "C"
    assert signal.legs[0].strike > snap.spot  # OTM call


def test_no_signal_when_rsi_too_low(conn):
    strat = CoveredCallOverlay({
        "short_delta": 0.30, "dte_min": 25, "dte_max": 50,
        "coverage_ratio": 1.0, "min_rsi": 60, "min_day_return": 0.0,
        "iv_rank_min": 0, "roll_dte": 5, "profit_target_pct": 0.50,
        "roll_itm_delta": 0.70, "defend_time_value_min": 500,
    })
    snap = _make_snapshot(spot=350.0)  # RSI is 55, below min_rsi=60
    signal = strat.evaluate(snap, [], conn=conn)
    assert signal is None


def test_no_signal_when_fully_covered(conn):
    strat = CoveredCallOverlay({
        "short_delta": 0.30, "dte_min": 25, "dte_max": 50,
        "coverage_ratio": 1.0, "min_rsi": 40, "min_day_return": 0.01,
        "iv_rank_min": 0, "roll_dte": 5, "profit_target_pct": 0.50,
        "roll_itm_delta": 0.70, "defend_time_value_min": 500,
    })
    # Add existing short calls covering all 4 coverable contracts (3 calls + floor(160/100)=1 share lot)
    for i in range(4):
        conn.execute(
            "INSERT INTO positions (run_id,ticker,strategy_id,legs,contracts,open_price,mark_to_mkt,opened_at) "
            "VALUES ('paper','TSLA',1,?,1,-50,0,1000)",
            (json.dumps([{"option_symbol": f"TSLA260515C00{370+i*10}000", "side": "short", "quantity": 1, "strike": 370+i*10, "expiry": "2026-05-15", "kind": "C"}]),)
        )
    snap = _make_snapshot(spot=350.0)
    signal = strat.evaluate(snap, [{"id": i, "run_id": "paper", "ticker": "TSLA"} for i in range(4)], conn=conn)
    assert signal is None


def test_coverage_ratio_limits_short_calls(conn):
    strat = CoveredCallOverlay({
        "short_delta": 0.30, "dte_min": 25, "dte_max": 50,
        "coverage_ratio": 0.5, "min_rsi": 40, "min_day_return": 0.01,
        "iv_rank_min": 0, "roll_dte": 5, "profit_target_pct": 0.50,
        "roll_itm_delta": 0.70, "defend_time_value_min": 500,
    })
    # 4 coverable * 0.5 = 2 allowed. Add 2 existing short calls.
    for i in range(2):
        conn.execute(
            "INSERT INTO positions (run_id,ticker,strategy_id,legs,contracts,open_price,mark_to_mkt,opened_at) "
            "VALUES ('paper','TSLA',1,?,1,-50,0,1000)",
            (json.dumps([{"option_symbol": f"TSLA260515C00{370+i*10}000", "side": "short", "quantity": 1, "strike": 370+i*10, "expiry": "2026-05-15", "kind": "C"}]),)
        )
    snap = _make_snapshot(spot=350.0)
    signal = strat.evaluate(snap, [{"id": i, "run_id": "paper", "ticker": "TSLA"} for i in range(2)], conn=conn)
    assert signal is None  # already at coverage limit


def test_no_signal_on_red_day(conn):
    strat = CoveredCallOverlay({
        "short_delta": 0.30, "dte_min": 25, "dte_max": 50,
        "coverage_ratio": 1.0, "min_rsi": 40, "min_day_return": 0.02,
        "iv_rank_min": 0, "roll_dte": 5, "profit_target_pct": 0.50,
        "roll_itm_delta": 0.70, "defend_time_value_min": 500,
    })
    snap = _make_snapshot(spot=350.0)
    # Override the bars so the last day is flat (0% return)
    snap.bars_1d[-1] = Bar(
        ticker="TSLA", timeframe="1d", ts=snap.bars_1d[-1].ts,
        open=350.0, high=351.0, low=349.0, close=350.0,
        volume=1000000, source="test",
    )
    snap.bars_1d[-2] = Bar(
        ticker="TSLA", timeframe="1d", ts=snap.bars_1d[-2].ts,
        open=350.0, high=351.0, low=349.0, close=350.0,
        volume=1000000, source="test",
    )
    signal = strat.evaluate(snap, [], conn=conn)
    assert signal is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_covered_call_overlay.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement CoveredCallOverlay**

```python
# bullbot/strategies/covered_call_overlay.py
"""CoveredCallOverlay — sell short-dated calls against existing LEAPS/shares."""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from typing import Any

from bullbot import config
from bullbot.data.long_inventory import active_inventory, total_coverable_contracts
from bullbot.data.schemas import Leg, Signal
from bullbot.data.synthetic_chain import bs_delta, realized_vol
from bullbot.strategies.base import Strategy, StrategySnapshot


def _make_osi(ticker: str, expiry: str, strike: float, kind: str) -> str:
    d = datetime.strptime(expiry, "%Y-%m-%d").date()
    return f"{ticker}{d:%y%m%d}{kind}{int(round(strike * 1000)):08d}"


class CoveredCallOverlay(Strategy):
    CLASS_NAME = "CoveredCallOverlay"
    CLASS_VERSION = 1

    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
        conn: sqlite3.Connection | None = None,
    ) -> Signal | None:
        if conn is None:
            return None

        ticker = snapshot.ticker

        # --- Check inventory ---
        coverable = total_coverable_contracts(conn, ticker)
        if coverable <= 0:
            return None

        # --- Count existing short calls for this ticker ---
        existing_short_calls = len([
            p for p in open_positions
            if p.get("ticker") == ticker
        ])

        # --- Apply coverage ratio ---
        coverage_ratio = self.params.get("coverage_ratio", 1.0)
        max_short = int(math.floor(coverable * coverage_ratio))
        available = max_short - existing_short_calls
        if available <= 0:
            return None

        # --- Timing filters ---
        rsi = snapshot.indicators.get("rsi_14", 0)
        min_rsi = self.params.get("min_rsi", 40)
        if rsi < min_rsi:
            return None

        min_day_return = self.params.get("min_day_return", 0.01)
        if len(snapshot.bars_1d) >= 2:
            prev_close = snapshot.bars_1d[-2].close
            if prev_close > 0:
                day_return = (snapshot.spot - prev_close) / prev_close
            else:
                day_return = 0
        else:
            day_return = 0
        if day_return < min_day_return:
            return None

        iv_rank_min = self.params.get("iv_rank_min", 0)
        if snapshot.iv_rank < iv_rank_min:
            return None

        # --- Find short call candidate ---
        short_delta_target = self.params.get("short_delta", 0.30)
        dte_min = self.params.get("dte_min", 14)
        dte_max = self.params.get("dte_max", 60)
        now_dt = datetime.fromtimestamp(snapshot.asof_ts, tz=timezone.utc)

        best = None
        best_delta_diff = float("inf")

        for c in snapshot.chain:
            if c.kind != "C":
                continue
            if c.nbbo_bid <= 0 or c.nbbo_ask <= 0:
                continue
            exp_dt = datetime.strptime(c.expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            dte = (exp_dt - now_dt).days
            if dte < dte_min or dte > dte_max:
                continue
            if c.strike <= snapshot.spot:
                continue  # only OTM calls

            vol = c.iv if c.iv else realized_vol(snapshot.bars_1d)
            delta = bs_delta(snapshot.spot, c.strike, dte / 365.0, vol, config.RISK_FREE_RATE, "C")
            diff = abs(delta - short_delta_target)
            if diff < best_delta_diff:
                best_delta_diff = diff
                best = c

        if best is None:
            return None

        osi = _make_osi(ticker, best.expiry, best.strike, "C")
        mid = (best.nbbo_bid + best.nbbo_ask) / 2.0

        leg = Leg(
            option_symbol=osi, side="short", quantity=1,
            strike=best.strike, expiry=best.expiry, kind="C",
        )

        return Signal(
            intent="open",
            strategy_class=self.CLASS_NAME,
            legs=[leg],
            max_loss_per_contract=mid * 100,
            rationale=(
                f"Selling {best.strike}C exp {best.expiry} against LEAPS inventory. "
                f"Delta ~{short_delta_target:.2f}, RSI {rsi:.0f}, day return {day_return:.1%}."
            ),
            profit_target_pct=self.params.get("profit_target_pct", 0.50),
            stop_loss_mult=self.params.get("roll_itm_delta", 0.70),
            min_dte_close=self.params.get("roll_dte", 5),
        )

    def max_loss_per_contract(self) -> float:
        return 5000.0
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_covered_call_overlay.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add bullbot/strategies/covered_call_overlay.py tests/unit/test_covered_call_overlay.py
git commit -m "feat: add CoveredCallOverlay strategy class"
```

---

### Task 3: Register strategy and wire into engine

**Files:**
- Modify: `bullbot/strategies/registry.py`
- Modify: `bullbot/engine/step.py` (pass conn to evaluate)
- Modify: `bullbot/evolver/proposer.py` (add PMCC guidance)

- [ ] **Step 1: Register CoveredCallOverlay in registry.py**

Add import and registry entry:

```python
from bullbot.strategies.covered_call_overlay import CoveredCallOverlay

# In _REGISTRY dict:
"CoveredCallOverlay": CoveredCallOverlay,
```

- [ ] **Step 2: Pass conn to strategy.evaluate() in engine step**

In `bullbot/engine/step.py`, find the line:

```python
signal = strategy.evaluate(snap, open_positions)
```

Change it to:

```python
signal = strategy.evaluate(snap, open_positions, conn=conn)
```

The base class `evaluate()` signature needs updating too. In `bullbot/strategies/base.py`, change the abstract method:

```python
@abstractmethod
def evaluate(
    self,
    snapshot: StrategySnapshot,
    open_positions: list[dict[str, Any]],
    **kwargs: Any,
) -> Signal | None:
```

This is backwards-compatible — existing strategies ignore `**kwargs`, and CoveredCallOverlay extracts `conn` from kwargs.

- [ ] **Step 3: Update CoveredCallOverlay.evaluate to accept kwargs**

Change the evaluate signature in `covered_call_overlay.py` to match:

```python
def evaluate(
    self,
    snapshot: StrategySnapshot,
    open_positions: list[dict[str, Any]],
    **kwargs: Any,
) -> Signal | None:
    conn = kwargs.get("conn")
    if conn is None:
        return None
```

- [ ] **Step 4: Add PMCC guidance to proposer**

In `bullbot/evolver/proposer.py`, update `_GROWTH_GUIDANCE` to include:

```python
_GROWTH_GUIDANCE = """
This ticker is categorized as GROWTH. The growth gate requires:
  CAGR >= 20%, Sortino >= 1.0, max drawdown <= 35%, trade count >= 5.

IMPORTANT: Bearish strategies (BearPutSpread, LongPut) typically produce NEGATIVE
CAGR on growth stocks because the underlying trends upward over time. To pass the
growth gate you almost certainly need a BULLISH strategy.

If the ticker has entries in the long_inventory table (existing LEAPS/shares),
consider CoveredCallOverlay — it sells short-dated calls against those positions
to generate income. Key params: short_delta (0.15-0.40), dte_min (14-30),
dte_max (30-60), coverage_ratio (0.5-1.0), min_rsi (40-55), min_day_return
(0.01-0.03). This works well for generating premium income on beaten-down stocks
where you want to sell into strength.

Otherwise prefer GrowthLEAPS for pure directional exposure.
"""
```

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add bullbot/strategies/registry.py bullbot/engine/step.py bullbot/strategies/base.py bullbot/strategies/covered_call_overlay.py bullbot/evolver/proposer.py
git commit -m "feat: register CoveredCallOverlay, pass conn to evaluate, update proposer"
```

---

### Task 4: Dashboard integration

**Files:**
- Modify: `bullbot/dashboard/queries.py`
- Modify: `bullbot/dashboard/templates.py`

- [ ] **Step 1: Add long_inventory query to dashboard**

In `bullbot/dashboard/queries.py`, add:

```python
def long_inventory_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return active long inventory positions for dashboard display."""
    try:
        rows = conn.execute(
            "SELECT * FROM long_inventory WHERE removed_at IS NULL ORDER BY account, ticker, kind, expiry"
        ).fetchall()
    except sqlite3.OperationalError:
        return []  # table doesn't exist yet
    return [dict(r) for r in rows]
```

- [ ] **Step 2: Add inventory display to dashboard**

In `bullbot/dashboard/templates.py`, add a function:

```python
def inventory_section(inventory: list[dict]) -> str:
    """Render the long inventory table."""
    if not inventory:
        return '<div class="card">No long inventory positions.</div>'
    lines = ['<table>', '<thead><tr><th>Account</th><th>Ticker</th><th>Type</th><th>Strike</th><th>Expiry</th><th>Qty</th><th>Cost Basis</th></tr></thead>', '<tbody>']
    for row in inventory:
        account = html.escape(row.get("account", ""))
        ticker = html.escape(row.get("ticker", ""))
        kind = row.get("kind", "")
        strike = f"${row['strike']:,.0f}" if row.get("strike") else "—"
        expiry = row.get("expiry") or "—"
        qty = row.get("quantity", 0)
        qty_str = f"{qty:g}"
        cost = f"${row['cost_basis_per']:,.2f}" if row.get("cost_basis_per") else "—"
        lines.append(
            f'<tr data-ticker="{ticker}">'
            f'<td>{account}</td><td>{ticker}{_category_badge(ticker)}</td>'
            f'<td>{kind}</td><td>{strike}</td><td>{expiry}</td>'
            f'<td>{qty_str}</td><td>{cost}</td></tr>'
        )
    lines.append('</tbody></table>')
    return "\n".join(lines)
```

- [ ] **Step 3: Wire inventory into generator**

In `bullbot/dashboard/generator.py`, add to the `generate()` function:

```python
inventory = queries.long_inventory_summary(conn)
# ...
inventory_html = templates.inventory_section(inventory)
```

Add "Inventory" as a new tab after "Costs".

- [ ] **Step 4: Run tests and regenerate dashboard**

Run: `pytest tests/ -x -q`
Then: `python -m bullbot.dashboard.generator`

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/queries.py bullbot/dashboard/templates.py bullbot/dashboard/generator.py
git commit -m "feat(dashboard): add Inventory tab showing long LEAPS/shares positions"
```

---

### Task 5: End-to-end validation

**Files:**
- No new files

- [ ] **Step 1: Apply schema migration to production DB**

```bash
python3 -c "
import sqlite3
from bullbot import config
conn = sqlite3.connect(str(config.DB_PATH))
conn.execute('''
    CREATE TABLE IF NOT EXISTS long_inventory (
        id INTEGER PRIMARY KEY, account TEXT NOT NULL, ticker TEXT NOT NULL,
        kind TEXT NOT NULL, strike REAL, expiry TEXT, quantity REAL NOT NULL,
        cost_basis_per REAL, added_at INTEGER NOT NULL, removed_at INTEGER
    )
''')
conn.commit()
conn.close()
print('Table created')
"
```

- [ ] **Step 2: Seed inventory from Fidelity CSV**

```bash
python3 -c "
import sqlite3
from bullbot import config
from bullbot.data import long_inventory

conn = sqlite3.connect(str(config.DB_PATH))
conn.row_factory = sqlite3.Row
count = long_inventory.seed_from_fidelity_csv(
    conn, '/Users/danield.runion/Downloads/Portfolio_Positions_Apr-14-2026.csv'
)
conn.commit()
print(f'Seeded {count} positions')
for r in conn.execute('SELECT * FROM long_inventory ORDER BY account, ticker').fetchall():
    print(f'  {r[\"account\"]:8s} {r[\"ticker\"]:6s} {r[\"kind\"]:6s} strike={r[\"strike\"]} exp={r[\"expiry\"]} qty={r[\"quantity\"]}')
conn.close()
"
```

- [ ] **Step 3: Run a manual CoveredCallOverlay evaluation**

```bash
python3 -c "
import sqlite3
from bullbot import config
from bullbot.engine.step import _build_snapshot
from bullbot.strategies.covered_call_overlay import CoveredCallOverlay

conn = sqlite3.connect(str(config.DB_PATH))
conn.row_factory = sqlite3.Row

# Get latest TSLA cursor
cursor = conn.execute(\"SELECT MAX(ts) FROM bars WHERE ticker='TSLA' AND timeframe='1d'\").fetchone()[0]
snap = _build_snapshot(conn, 'TSLA', cursor)
if snap:
    print(f'TSLA spot={snap.spot:.2f}, RSI={snap.indicators.get(\"rsi_14\", 0):.1f}')
    strat = CoveredCallOverlay({
        'short_delta': 0.30, 'dte_min': 25, 'dte_max': 50,
        'coverage_ratio': 0.67, 'min_rsi': 40, 'min_day_return': 0.01,
        'iv_rank_min': 0, 'roll_dte': 5, 'profit_target_pct': 0.50,
        'roll_itm_delta': 0.70, 'defend_time_value_min': 500,
    })
    signal = strat.evaluate(snap, [], conn=conn)
    if signal:
        print(f'Signal: sell {signal.legs[0].strike}C exp {signal.legs[0].expiry}')
        print(f'Rationale: {signal.rationale}')
    else:
        print('No signal (timing filters not met)')
conn.close()
"
```

- [ ] **Step 4: Regenerate dashboard and verify Inventory tab**

```bash
python3 -m bullbot.dashboard.generator
open reports/dashboard.html
```

- [ ] **Step 5: Final commit**

```bash
git commit -m "feat: CoveredCallOverlay end-to-end validated with production data"
```
