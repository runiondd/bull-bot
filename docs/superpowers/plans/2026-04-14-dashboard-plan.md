# Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a self-contained HTML dashboard from the Bull-Bot SQLite database showing ticker status, evolver history, positions, transactions, and costs.

**Architecture:** A Python module (`bullbot/dashboard/`) queries the DB and writes `reports/dashboard.html`. Template fragments live in `templates.py` as f-string functions. The generator is called at the end of `scheduler.tick()` and can also run standalone. All interactivity (tabs, filtering) is client-side JS embedded in the HTML.

**Tech Stack:** Python stdlib (sqlite3, json, datetime, html, pathlib). No external dependencies.

---

### Task 1: Data layer — query functions

**Files:**
- Create: `bullbot/dashboard/__init__.py`
- Create: `bullbot/dashboard/queries.py`
- Create: `tests/unit/test_dashboard_queries.py`

This task builds all the DB queries the dashboard needs. Each function takes a `sqlite3.Connection` and returns plain dicts/lists.

- [ ] **Step 1: Write failing tests for query functions**

```python
# tests/unit/test_dashboard_queries.py
import json
import sqlite3
import time

import pytest

from bullbot.dashboard import queries


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE ticker_state (
            id INTEGER PRIMARY KEY, ticker TEXT, phase TEXT,
            iteration_count INTEGER, plateau_counter INTEGER,
            best_strategy_id INTEGER, best_pf_is REAL, best_pf_oos REAL,
            cumulative_llm_usd REAL, paper_started_at INTEGER,
            paper_trade_count INTEGER, live_started_at INTEGER,
            verdict_at INTEGER, retired INTEGER, updated_at INTEGER
        );
        CREATE TABLE strategies (
            id INTEGER PRIMARY KEY, class_name TEXT, class_version INTEGER,
            params TEXT, params_hash TEXT, parent_id INTEGER, created_at INTEGER
        );
        CREATE TABLE evolver_proposals (
            id INTEGER PRIMARY KEY, ticker TEXT, iteration INTEGER,
            strategy_id INTEGER, rationale TEXT, llm_cost_usd REAL,
            pf_is REAL, pf_oos REAL, sharpe_is REAL, max_dd_pct REAL,
            trade_count INTEGER, regime_breakdown TEXT, passed_gate INTEGER,
            created_at INTEGER
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY, run_id TEXT, ticker TEXT, strategy_id INTEGER,
            legs TEXT, contracts INTEGER, open_price REAL, close_price REAL,
            mark_to_mkt REAL, opened_at INTEGER, closed_at INTEGER,
            pnl_realized REAL, exit_rules TEXT
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY, run_id TEXT, ticker TEXT, strategy_id INTEGER,
            intent TEXT, legs TEXT, status TEXT, commission REAL,
            pnl_realized REAL, placed_at INTEGER
        );
        CREATE TABLE cost_ledger (
            id INTEGER PRIMARY KEY, ts INTEGER, category TEXT, ticker TEXT,
            amount_usd REAL, details TEXT
        );
    """)
    return c


def test_summary_metrics(conn):
    conn.execute(
        "INSERT INTO positions (run_id,ticker,strategy_id,legs,contracts,open_price,mark_to_mkt,opened_at,closed_at,pnl_realized) "
        "VALUES ('paper','SPY',1,'[]',1,-200,0,1,2,95.0)"
    )
    conn.execute(
        "INSERT INTO positions (run_id,ticker,strategy_id,legs,contracts,open_price,mark_to_mkt,opened_at,closed_at,pnl_realized) "
        "VALUES ('paper','TSLA',1,'[]',1,-9000,-8500,1,NULL,NULL)"
    )
    conn.execute("INSERT INTO ticker_state (ticker,phase,cumulative_llm_usd) VALUES ('SPY','paper_trial',2.50)")
    conn.execute("INSERT INTO ticker_state (ticker,phase,cumulative_llm_usd) VALUES ('TSLA','paper_trial',1.50)")
    m = queries.summary_metrics(conn)
    assert m["open_positions"] == 1
    assert m["paper_pnl"] == 95.0 + (-8500)
    assert m["llm_spend"] == 4.0


def test_ticker_grid(conn):
    conn.execute("INSERT INTO ticker_state (ticker,phase,iteration_count,paper_trade_count,best_strategy_id) VALUES ('SPY','paper_trial',3,1,1)")
    conn.execute("INSERT INTO strategies (id,class_name,class_version,params,params_hash) VALUES (1,'PutCreditSpread',1,'{}','abc')")
    rows = queries.ticker_grid(conn)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "SPY"
    assert rows[0]["strategy"] == "PutCreditSpread"


def test_recent_activity(conn):
    conn.execute("INSERT INTO evolver_proposals (ticker,iteration,strategy_id,rationale,llm_cost_usd,pf_oos,trade_count,passed_gate,created_at) VALUES ('SPY',1,1,'test',0.01,1.5,10,1,1000)")
    conn.execute("INSERT INTO strategies (id,class_name,class_version,params,params_hash) VALUES (1,'PCS',1,'{}','x')")
    conn.execute("INSERT INTO orders (run_id,ticker,strategy_id,intent,legs,status,placed_at) VALUES ('paper','SPY',1,'open','[]','filled',2000)")
    events = queries.recent_activity(conn, limit=20)
    assert len(events) == 2
    assert events[0]["ts"] == 2000  # most recent first


def test_evolver_proposals(conn):
    conn.execute("INSERT INTO strategies (id,class_name,class_version,params,params_hash) VALUES (1,'BearPutSpread',1,'{\"delta\":0.3}','x')")
    conn.execute("INSERT INTO evolver_proposals (ticker,iteration,strategy_id,rationale,llm_cost_usd,pf_is,pf_oos,max_dd_pct,trade_count,passed_gate,created_at) VALUES ('TSLA',1,1,'test reason',0.02,1.1,7.4,0.35,11,0,1000)")
    rows = queries.evolver_proposals(conn)
    assert rows[0]["class_name"] == "BearPutSpread"
    assert rows[0]["rationale"] == "test reason"
    assert rows[0]["params"] == {"delta": 0.3}


def test_positions_list(conn):
    legs = json.dumps([{"option_symbol": "SPY260515P00570000", "side": "short", "quantity": 1, "strike": 570.0, "expiry": "2026-05-15", "kind": "P"}])
    conn.execute(
        "INSERT INTO positions (run_id,ticker,strategy_id,legs,contracts,open_price,mark_to_mkt,opened_at,closed_at,pnl_realized,exit_rules) "
        "VALUES ('paper','SPY',1,?,1,-200,-150,1000,NULL,NULL,?)",
        (legs, '{"profit_target_pct":0.5}')
    )
    conn.execute("INSERT INTO strategies (id,class_name,class_version,params,params_hash) VALUES (1,'PCS',1,'{}','x')")
    rows = queries.positions_list(conn)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "SPY"
    assert rows[0]["is_open"] is True
    assert len(rows[0]["legs"]) == 1


def test_orders_list(conn):
    legs = json.dumps([{"option_symbol": "SPY260515P00570000", "side": "short", "quantity": 1, "strike": 570.0, "expiry": "2026-05-15", "kind": "P"}])
    conn.execute(
        "INSERT INTO orders (run_id,ticker,strategy_id,intent,legs,status,commission,pnl_realized,placed_at) "
        "VALUES ('paper','SPY',1,'close',?,'filled',1.30,95.0,2000)",
        (legs,)
    )
    conn.execute("INSERT INTO strategies (id,class_name,class_version,params,params_hash) VALUES (1,'PCS',1,'{}','x')")
    rows = queries.orders_list(conn)
    assert len(rows) == 1
    assert rows[0]["pnl"] == 95.0


def test_cost_breakdown(conn):
    conn.execute("INSERT INTO ticker_state (ticker,phase,cumulative_llm_usd) VALUES ('SPY','paper_trial',2.50)")
    conn.execute("INSERT INTO cost_ledger (ts,category,ticker,amount_usd,details) VALUES (1000,'llm',NULL,0.50,'{}')")
    conn.execute("INSERT INTO orders (run_id,ticker,strategy_id,intent,legs,status,commission,placed_at) VALUES ('paper','SPY',1,'open','[]','filled',1.30,1000)")
    costs = queries.cost_breakdown(conn)
    assert costs["llm_per_ticker"]["SPY"] == 2.50
    assert costs["paper_commissions"] == 1.30
```

- [ ] **Step 2: Create empty package and run tests to verify they fail**

Create `bullbot/dashboard/__init__.py` as an empty file.

Run: `pytest tests/unit/test_dashboard_queries.py -v`
Expected: FAIL — `queries` module not found

- [ ] **Step 3: Implement query functions**

```python
# bullbot/dashboard/queries.py
"""Dashboard data queries — read-only SELECTs against bullbot.db."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def summary_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    open_pos = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE closed_at IS NULL AND run_id NOT LIKE 'bt:%'"
    ).fetchone()[0]
    realized = conn.execute(
        "SELECT COALESCE(SUM(pnl_realized), 0) FROM positions "
        "WHERE closed_at IS NOT NULL AND run_id NOT LIKE 'bt:%'"
    ).fetchone()[0]
    mark = conn.execute(
        "SELECT COALESCE(SUM(mark_to_mkt), 0) FROM positions "
        "WHERE closed_at IS NULL AND run_id NOT LIKE 'bt:%'"
    ).fetchone()[0]
    llm = conn.execute(
        "SELECT COALESCE(SUM(cumulative_llm_usd), 0) FROM ticker_state"
    ).fetchone()[0]
    return {
        "open_positions": open_pos,
        "paper_pnl": float(realized) + float(mark),
        "llm_spend": float(llm),
    }


def ticker_grid(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT ts.ticker, ts.phase, ts.iteration_count, ts.paper_trade_count,
               ts.best_strategy_id, s.class_name
        FROM ticker_state ts
        LEFT JOIN strategies s ON s.id = ts.best_strategy_id
        ORDER BY ts.ticker
    """).fetchall()
    return [
        {
            "ticker": r["ticker"],
            "phase": r["phase"],
            "iteration_count": r["iteration_count"],
            "paper_trade_count": r["paper_trade_count"] or 0,
            "strategy": r["class_name"] or "-",
        }
        for r in rows
    ]


def recent_activity(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    for r in conn.execute("""
        SELECT ep.ticker, ep.iteration, ep.passed_gate, ep.created_at, s.class_name
        FROM evolver_proposals ep
        JOIN strategies s ON s.id = ep.strategy_id
        ORDER BY ep.created_at DESC LIMIT ?
    """, (limit,)).fetchall():
        gate = "PASS" if r["passed_gate"] else "FAIL"
        events.append({
            "ts": r["created_at"],
            "ticker": r["ticker"],
            "description": f"evolver iter {r['iteration']}: {r['class_name']} {gate}",
        })

    for r in conn.execute("""
        SELECT ticker, intent, legs, placed_at
        FROM orders WHERE run_id NOT LIKE 'bt:%'
        ORDER BY placed_at DESC LIMIT ?
    """, (limit,)).fetchall():
        legs_json = json.loads(r["legs"]) if r["legs"] else []
        abbr = _abbreviate_legs(legs_json)
        events.append({
            "ts": r["placed_at"],
            "ticker": r["ticker"],
            "description": f"paper {r['intent']}: {abbr}",
        })

    for r in conn.execute(
        "SELECT ticker, paper_started_at FROM ticker_state WHERE paper_started_at IS NOT NULL"
    ).fetchall():
        events.append({
            "ts": r["paper_started_at"],
            "ticker": r["ticker"],
            "description": "promoted to paper trial",
        })

    events.sort(key=lambda e: e["ts"], reverse=True)
    return events[:limit]


def evolver_proposals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT ep.ticker, ep.iteration, ep.strategy_id, ep.rationale,
               ep.pf_is, ep.pf_oos, ep.max_dd_pct, ep.trade_count,
               ep.passed_gate, ep.created_at, ep.llm_cost_usd,
               s.class_name, s.params
        FROM evolver_proposals ep
        JOIN strategies s ON s.id = ep.strategy_id
        ORDER BY ep.ticker, ep.iteration
    """).fetchall()
    return [
        {
            "ticker": r["ticker"],
            "iteration": r["iteration"],
            "class_name": r["class_name"],
            "params": json.loads(r["params"]) if r["params"] else {},
            "rationale": r["rationale"],
            "pf_is": r["pf_is"],
            "pf_oos": r["pf_oos"],
            "max_dd_pct": r["max_dd_pct"],
            "trade_count": r["trade_count"],
            "passed_gate": bool(r["passed_gate"]),
            "created_at": r["created_at"],
            "llm_cost_usd": r["llm_cost_usd"],
        }
        for r in rows
    ]


def positions_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT p.id, p.run_id, p.ticker, p.contracts, p.open_price,
               p.close_price, p.mark_to_mkt, p.opened_at, p.closed_at,
               p.pnl_realized, p.exit_rules, p.legs, s.class_name
        FROM positions p
        LEFT JOIN strategies s ON s.id = p.strategy_id
        ORDER BY p.opened_at DESC
    """).fetchall()
    return [
        {
            "id": r["id"],
            "run_id": r["run_id"],
            "ticker": r["ticker"],
            "class_name": r["class_name"] or "?",
            "contracts": r["contracts"],
            "open_price": r["open_price"],
            "close_price": r["close_price"],
            "mark_to_mkt": r["mark_to_mkt"],
            "opened_at": r["opened_at"],
            "closed_at": r["closed_at"],
            "pnl_realized": r["pnl_realized"],
            "exit_rules": json.loads(r["exit_rules"]) if r["exit_rules"] else {},
            "legs": json.loads(r["legs"]) if r["legs"] else [],
            "is_open": r["closed_at"] is None,
            "is_backtest": r["run_id"].startswith("bt:") if r["run_id"] else False,
        }
        for r in rows
    ]


def orders_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT o.id, o.run_id, o.ticker, o.intent, o.legs, o.status,
               o.commission, o.pnl_realized, o.placed_at, s.class_name
        FROM orders o
        LEFT JOIN strategies s ON s.id = o.strategy_id
        ORDER BY o.placed_at DESC
    """).fetchall()
    return [
        {
            "id": r["id"],
            "run_id": r["run_id"],
            "ticker": r["ticker"],
            "intent": r["intent"],
            "legs": json.loads(r["legs"]) if r["legs"] else [],
            "status": r["status"],
            "commission": r["commission"],
            "pnl": r["pnl_realized"],
            "placed_at": r["placed_at"],
            "class_name": r["class_name"] or "?",
            "is_backtest": r["run_id"].startswith("bt:") if r["run_id"] else False,
        }
        for r in rows
    ]


def cost_breakdown(conn: sqlite3.Connection) -> dict[str, Any]:
    llm_per_ticker = {}
    for r in conn.execute(
        "SELECT ticker, cumulative_llm_usd FROM ticker_state WHERE cumulative_llm_usd > 0"
    ).fetchall():
        llm_per_ticker[r["ticker"]] = float(r["cumulative_llm_usd"])

    ledger_total = conn.execute(
        "SELECT COALESCE(SUM(amount_usd), 0) FROM cost_ledger WHERE category='llm'"
    ).fetchone()[0]

    paper_comm = conn.execute(
        "SELECT COALESCE(SUM(commission), 0) FROM orders WHERE run_id NOT LIKE 'bt:%'"
    ).fetchone()[0]

    bt_comm = conn.execute(
        "SELECT COALESCE(SUM(commission), 0) FROM orders WHERE run_id LIKE 'bt:%'"
    ).fetchone()[0]

    return {
        "llm_per_ticker": llm_per_ticker,
        "llm_ledger_total": float(ledger_total),
        "paper_commissions": float(paper_comm),
        "backtest_commissions": float(bt_comm),
    }


def _abbreviate_legs(legs: list[dict]) -> str:
    parts = []
    for leg in legs:
        side = "L" if leg.get("side") == "long" else "S"
        qty = leg.get("quantity", 1)
        sym = leg.get("option_symbol", "?")
        parts.append(f"{side} {qty}x {sym}")
    return " / ".join(parts) if parts else "(no legs)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_dashboard_queries.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/__init__.py bullbot/dashboard/queries.py tests/unit/test_dashboard_queries.py
git commit -m "feat(dashboard): add data query layer for dashboard generation"
```

---

### Task 2: HTML templates

**Files:**
- Create: `bullbot/dashboard/templates.py`
- Create: `tests/unit/test_dashboard_templates.py`

Each template function takes pre-queried data and returns an HTML string fragment. The page shell includes embedded CSS, tab JS, and ticker-filter JS.

- [ ] **Step 1: Write failing tests for template functions**

```python
# tests/unit/test_dashboard_templates.py
from bullbot.dashboard import templates


def test_page_shell_wraps_content():
    html = templates.page_shell("2026-04-14 12:00", "<p>body</p>")
    assert "Bull-Bot Dashboard" in html
    assert "2026-04-14 12:00" in html
    assert "<p>body</p>" in html
    assert "<html" in html
    assert "switchTab" in html  # JS function exists


def test_summary_cards():
    html = templates.summary_cards({
        "open_positions": 3,
        "paper_pnl": 1247.50,
        "llm_spend": 4.98,
    })
    assert "$265,000" in html  # total equity from config
    assert "3" in html
    assert "+$1,247.50" in html
    assert "$4.98" in html


def test_summary_cards_negative_pnl():
    html = templates.summary_cards({
        "open_positions": 0,
        "paper_pnl": -500.0,
        "llm_spend": 1.0,
    })
    assert "-$500.00" in html


def test_ticker_grid_row():
    html = templates.ticker_grid([{
        "ticker": "SPY",
        "phase": "paper_trial",
        "iteration_count": 3,
        "paper_trade_count": 1,
        "strategy": "PutCreditSpread",
    }])
    assert "SPY" in html
    assert "paper_trial" in html
    assert "PutCreditSpread" in html
    assert 'data-ticker="SPY"' in html  # for JS filtering


def test_evolver_card_pass():
    html = templates.evolver_section([{
        "ticker": "TSLA",
        "iteration": 1,
        "class_name": "GrowthLEAPS",
        "params": {"target_delta": 0.6},
        "rationale": "Long-dated calls for growth",
        "pf_is": 1.3,
        "pf_oos": float("inf"),
        "max_dd_pct": 0.0,
        "trade_count": 6,
        "passed_gate": True,
        "created_at": 1000,
        "llm_cost_usd": 0.03,
    }])
    assert "GrowthLEAPS" in html
    assert "PASS" in html
    assert "target_delta=0.6" in html
    assert "Long-dated calls for growth" in html


def test_evolver_card_fail_is_dimmed():
    html = templates.evolver_section([{
        "ticker": "TSLA",
        "iteration": 2,
        "class_name": "BearPutSpread",
        "params": {},
        "rationale": "test",
        "pf_is": 0.5,
        "pf_oos": 0.0,
        "max_dd_pct": 0.5,
        "trade_count": 0,
        "passed_gate": False,
        "created_at": 2000,
        "llm_cost_usd": 0.02,
    }])
    assert "FAIL" in html
    assert "opacity" in html


def test_position_card_open():
    html = templates.positions_section([{
        "id": 1, "run_id": "paper", "ticker": "TSLA", "class_name": "GrowthLEAPS",
        "contracts": 1, "open_price": -9528.0, "close_price": None,
        "mark_to_mkt": -8800.0, "opened_at": 1000, "closed_at": None,
        "pnl_realized": None, "exit_rules": {"profit_target_pct": 0.9},
        "legs": [{"option_symbol": "TSLA270119C00260000", "side": "long", "quantity": 1, "strike": 260.0, "expiry": "2027-01-19", "kind": "C"}],
        "is_open": True, "is_backtest": False,
    }])
    assert "TSLA" in html
    assert "OPEN" in html
    assert "TSLA270119C00260000" in html


def test_transactions_table():
    html = templates.transactions_section([{
        "id": 1, "run_id": "paper", "ticker": "SPY", "intent": "close",
        "legs": [{"option_symbol": "SPY260515P00570000", "side": "short", "quantity": 1, "strike": 570.0, "expiry": "2026-05-15", "kind": "P"}],
        "status": "filled", "commission": 1.30, "pnl": 95.0,
        "placed_at": 2000, "class_name": "PCS", "is_backtest": False,
    }])
    assert "SPY" in html
    assert "close" in html
    assert "+$95.00" in html


def test_costs_section():
    html = templates.costs_section({
        "llm_per_ticker": {"SPY": 2.50, "TSLA": 0.03},
        "llm_ledger_total": 3.00,
        "paper_commissions": 6.50,
        "backtest_commissions": 502.30,
    })
    assert "SPY" in html
    assert "$2.50" in html
    assert "$6.50" in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_dashboard_templates.py -v`
Expected: FAIL — `templates` module not found

- [ ] **Step 3: Implement template functions**

This file is large but mechanical — each function returns an HTML string built with f-strings. The full implementation should follow the mockups from the design phase: dark theme, color-coded badges, card layout for evolver/positions, table for transactions.

Create `bullbot/dashboard/templates.py` with these functions:

- `page_shell(updated_at: str, body: str) -> str` — full HTML document with embedded CSS, tab-switching JS, and ticker-filter JS
- `summary_cards(metrics: dict) -> str` — four metric cards row
- `ticker_grid(rows: list[dict]) -> str` — clickable ticker status table
- `activity_feed(events: list[dict]) -> str` — recent activity list
- `evolver_section(proposals: list[dict]) -> str` — grouped-by-ticker evolver cards
- `positions_section(positions: list[dict]) -> str` — position cards with filter bar
- `transactions_section(orders: list[dict]) -> str` — order log table with filter bar
- `costs_section(costs: dict) -> str` — LLM and commission breakdown

Each function uses `html.escape()` on any user-provided strings (rationale, etc). Timestamps are formatted via `datetime.fromtimestamp()`. PnL values are color-coded green/red. Phase badges use the spec colors. All elements that represent a specific ticker get `data-ticker="{ticker}"` for client-side filtering.

The CSS in `page_shell` should define:
- Dark theme variables matching the spec (`#1a1a2e`, `#0f3460`, `#4cc9f0`, etc.)
- `.tab-btn`, `.tab-content` for tab switching
- `.phase-badge` variants for each phase
- `.card` with `.pass-border` / `.fail-border` classes
- `.filter-btn` for position/transaction filters
- `.dimmed` class (opacity 0.6)

The JS in `page_shell` should define:
- `switchTab(tabName)` — shows matching `.tab-content`, highlights matching `.tab-btn`
- `filterTicker(ticker)` — hides elements where `data-ticker` doesn't match, shows "Showing: {ticker}" indicator with clear button
- `clearFilter()` — shows all elements
- `toggleFilter(type)` — for position/transaction filter buttons (open/closed/paper/backtest)

**Note:** This file will be the largest in the dashboard module (~300-400 lines). The code is entirely string formatting — no logic beyond conditional CSS classes and number formatting. Write it to pass all the tests above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_dashboard_templates.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add bullbot/dashboard/templates.py tests/unit/test_dashboard_templates.py
git commit -m "feat(dashboard): add HTML template functions"
```

---

### Task 3: Generator — assemble and write the HTML file

**Files:**
- Create: `bullbot/dashboard/generator.py`
- Create: `tests/unit/test_dashboard_generator.py`

The generator wires queries to templates and writes the output file.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_dashboard_generator.py
import sqlite3
import json
import time
from pathlib import Path

import pytest

from bullbot.dashboard import generator


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE ticker_state (
            id INTEGER PRIMARY KEY, ticker TEXT, phase TEXT,
            iteration_count INTEGER, plateau_counter INTEGER,
            best_strategy_id INTEGER, best_pf_is REAL, best_pf_oos REAL,
            cumulative_llm_usd REAL, paper_started_at INTEGER,
            paper_trade_count INTEGER, live_started_at INTEGER,
            verdict_at INTEGER, retired INTEGER, updated_at INTEGER
        );
        CREATE TABLE strategies (
            id INTEGER PRIMARY KEY, class_name TEXT, class_version INTEGER,
            params TEXT, params_hash TEXT, parent_id INTEGER, created_at INTEGER
        );
        CREATE TABLE evolver_proposals (
            id INTEGER PRIMARY KEY, ticker TEXT, iteration INTEGER,
            strategy_id INTEGER, rationale TEXT, llm_cost_usd REAL,
            pf_is REAL, pf_oos REAL, sharpe_is REAL, max_dd_pct REAL,
            trade_count INTEGER, regime_breakdown TEXT, passed_gate INTEGER,
            created_at INTEGER
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY, run_id TEXT, ticker TEXT, strategy_id INTEGER,
            legs TEXT, contracts INTEGER, open_price REAL, close_price REAL,
            mark_to_mkt REAL, opened_at INTEGER, closed_at INTEGER,
            pnl_realized REAL, exit_rules TEXT
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY, run_id TEXT, ticker TEXT, strategy_id INTEGER,
            intent TEXT, legs TEXT, status TEXT, commission REAL,
            pnl_realized REAL, placed_at INTEGER
        );
        CREATE TABLE cost_ledger (
            id INTEGER PRIMARY KEY, ts INTEGER, category TEXT, ticker TEXT,
            amount_usd REAL, details TEXT
        );
    """)
    # Seed minimal data
    c.execute("INSERT INTO ticker_state (ticker,phase,iteration_count,paper_trade_count,cumulative_llm_usd) VALUES ('SPY','paper_trial',3,1,2.50)")
    c.execute("INSERT INTO strategies (id,class_name,class_version,params,params_hash) VALUES (1,'PutCreditSpread',1,'{}','abc')")
    return c


def test_generate_writes_html_file(conn, tmp_path):
    out = tmp_path / "dashboard.html"
    generator.generate(conn, output_path=out)
    assert out.exists()
    html = out.read_text()
    assert "Bull-Bot Dashboard" in html
    assert "SPY" in html
    assert "PutCreditSpread" in html


def test_generate_uses_default_path(conn, monkeypatch):
    import bullbot.config as cfg
    monkeypatch.setattr(cfg, "REPORTS_DIR", Path("/tmp/bullbot_test_reports"))
    Path("/tmp/bullbot_test_reports").mkdir(exist_ok=True)
    generator.generate(conn)
    assert (Path("/tmp/bullbot_test_reports") / "dashboard.html").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_dashboard_generator.py -v`
Expected: FAIL — `generator` module not found

- [ ] **Step 3: Implement the generator**

```python
# bullbot/dashboard/generator.py
"""Generate the Bull-Bot HTML dashboard from the database."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bullbot import config
from bullbot.dashboard import queries, templates


def generate(conn: sqlite3.Connection, output_path: Path | None = None) -> Path:
    if output_path is None:
        output_path = config.REPORTS_DIR / "dashboard.html"

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    metrics = queries.summary_metrics(conn)
    grid = queries.ticker_grid(conn)
    activity = queries.recent_activity(conn)
    proposals = queries.evolver_proposals(conn)
    positions = queries.positions_list(conn)
    orders = queries.orders_list(conn)
    costs = queries.cost_breakdown(conn)

    overview_html = templates.ticker_grid(grid) + templates.activity_feed(activity)
    evolver_html = templates.evolver_section(proposals)
    positions_html = templates.positions_section(positions)
    transactions_html = templates.transactions_section(orders)
    costs_html = templates.costs_section(costs)

    tabs = {
        "Overview": overview_html,
        "Evolver": evolver_html,
        "Positions": positions_html,
        "Transactions": transactions_html,
        "Costs": costs_html,
    }

    body_parts = [templates.summary_cards(metrics)]
    body_parts.append('<div class="tab-bar">')
    for i, name in enumerate(tabs):
        active = " active" if i == 0 else ""
        body_parts.append(
            f'<button class="tab-btn{active}" onclick="switchTab(\'{name}\')">{name}</button>'
        )
    body_parts.append('</div>')
    body_parts.append('<div id="filter-indicator" style="display:none"></div>')

    for i, (name, content) in enumerate(tabs.items()):
        display = "block" if i == 0 else "none"
        body_parts.append(
            f'<div class="tab-content" id="tab-{name}" style="display:{display}">{content}</div>'
        )

    body = "\n".join(body_parts)
    html = templates.page_shell(now, body)
    output_path.write_text(html, encoding="utf-8")
    return output_path


if __name__ == "__main__":
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    path = generate(conn)
    print(f"Dashboard written to {path}")
    conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_dashboard_generator.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass (previous + new)

- [ ] **Step 6: Commit**

```bash
git add bullbot/dashboard/generator.py tests/unit/test_dashboard_generator.py
git commit -m "feat(dashboard): add generator that assembles and writes HTML"
```

---

### Task 4: Scheduler integration and CLI entry point

**Files:**
- Modify: `bullbot/scheduler.py` (add 3 lines at end of `tick()`)
- Modify: `bullbot/dashboard/generator.py` (already has `__main__` block)

- [ ] **Step 1: Add dashboard generation to scheduler.tick()**

In `bullbot/scheduler.py`, add at the end of the `tick()` function (after the for-loop over tickers, around line 178):

```python
    try:
        from bullbot.dashboard import generator
        generator.generate(conn)
    except Exception:
        log.exception("dashboard generation failed")
```

- [ ] **Step 2: Test the CLI entry point manually**

Run: `python -m bullbot.dashboard.generator`
Expected: prints "Dashboard written to /Users/danield.runion/Bull-Bot/reports/dashboard.html"

- [ ] **Step 3: Open the dashboard in a browser and verify it looks correct**

Run: `open reports/dashboard.html`

Verify:
- Header shows "Bull-Bot Dashboard" with timestamp
- Summary cards show equity, open positions, PnL, LLM spend
- Overview tab shows ticker grid with correct phases and strategies
- Clicking a ticker filters other tabs
- Evolver tab shows proposal cards with rationale
- Positions tab shows open/closed positions
- Transactions tab shows order log
- Costs tab shows LLM and commission breakdown

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add bullbot/scheduler.py
git commit -m "feat(dashboard): wire dashboard generation into scheduler.tick()"
```

---

### Task 5: Visual polish and end-to-end verification

**Files:**
- Modify: `bullbot/dashboard/templates.py` (CSS/styling adjustments)

- [ ] **Step 1: Generate dashboard from production DB**

Run: `python -m bullbot.dashboard.generator`

- [ ] **Step 2: Open and review each tab**

Open `reports/dashboard.html` in a browser. Check:
- Color consistency across all tabs
- Phase badges are color-coded correctly (green/amber/red/blue)
- Pass/fail cards in evolver tab are visually distinct
- Ticker filter works across all tabs
- Position/transaction filter buttons work
- Numbers are formatted correctly (dollar signs, commas, percentages)
- No broken HTML or missing data

- [ ] **Step 3: Fix any visual issues found**

Adjust CSS or template functions in `templates.py` as needed. This is a polish pass — the functionality should already work from prior tasks.

- [ ] **Step 4: Final test run**

Run: `pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 5: Final commit**

```bash
git add bullbot/dashboard/templates.py
git commit -m "style(dashboard): visual polish and layout refinements"
```
