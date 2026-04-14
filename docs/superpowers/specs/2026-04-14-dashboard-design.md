# Bull-Bot Web Dashboard — Design Spec

**Date:** 2026-04-14
**Status:** Approved

## Overview

A static HTML dashboard generated from the Bull-Bot SQLite database. A Python script queries the DB and writes a single self-contained HTML file with embedded CSS and JS (Chart.js via CDN for any charts). No web server required — open the file in a browser. The generator runs after each `scheduler.tick()` so the dashboard stays current.

## Architecture

```
bullbot.db ──> generate_dashboard.py ──> reports/dashboard.html ──> browser
```

- **Input:** `cache/bullbot.db` (read-only)
- **Output:** `reports/dashboard.html` (overwritten each run)
- **Trigger:** Called at the end of `scheduler.tick()`, or manually via CLI
- **Dependencies:** Python stdlib only (sqlite3, json, datetime, html). Chart.js loaded from CDN in the HTML. No Flask, no Jinja, no web framework.

## Layout

Single HTML file with tabbed navigation. Dark theme (navy/charcoal background, light text). All interactivity is client-side JS (tab switching, ticker filtering, filter toggles).

### Header Bar
- "Bull-Bot Dashboard" title
- "Updated {timestamp}" right-aligned
- Refreshes when the file is regenerated

### Summary Cards (always visible above tabs)
Four metric cards in a row:
1. **Total Equity** — $50k + $215k = $265k (from config, not computed)
2. **Open Positions** — count from `positions WHERE closed_at IS NULL AND run_id='paper'`
3. **Paper P&L** — sum of `pnl_realized` from closed paper positions + sum of `mark_to_mkt` from open paper positions
4. **LLM Spend** — sum of `cumulative_llm_usd` from `ticker_state`

### Tab 1: Overview
**Ticker status grid** showing every ticker in the universe:
- Columns: Ticker, Account ($50k/$215k), Phase (color-coded badge), Strategy class name, Paper trade count
- Phase badges: `paper_trial` green, `discovering` amber, `no_edge` red, `live` blue
- Rows are clickable — clicking a ticker filters the Evolver, Positions, and Transactions tabs to that ticker. A "clear filter" option resets to all tickers.

**Recent activity feed** below the grid:
- Last 20 events, reverse chronological
- Built by merging three queries into a single list sorted by timestamp:
  1. `evolver_proposals`: "{ticker} evolver iter {n}: {class_name} {PASS/FAIL}" — keyed on `created_at`
  2. `orders WHERE run_id NOT LIKE 'bt:%'`: "{ticker} paper {intent}: {abbreviated legs}" — keyed on `placed_at`
  3. `ticker_state WHERE phase='paper_trial'`: "{ticker} promoted to paper trial" — keyed on `paper_started_at`
- Each line: timestamp, ticker, description

### Tab 2: Evolver
**Per-ticker proposal history** using expanded cards:
- Grouped by ticker (or filtered to one ticker if selected from Overview)
- Each ticker section shows: ticker name, phase badge, category, account, iteration count, LLM spend

**Per-iteration card:**
- Strategy class name, PASS/FAIL badge
- Metrics row: trade count, PF OOS, PF IS, max drawdown, and for growth: CAGR, Sortino
- Params displayed as `key=value` pairs
- LLM rationale in a quote block
- PASS cards: green left border, full opacity
- FAIL cards: red left border, dimmed (opacity 0.6)

**Data source:** `evolver_proposals` joined with `strategies` on `strategy_id`

### Tab 3: Positions
**Position cards** with filter bar (All / Open / Closed / Paper / Backtest):
- Default filter: Paper only (hide backtest noise)

**Open position card:**
- Ticker, strategy class, "OPEN" badge, run type (paper/live)
- Legs in human-readable format (e.g., "Long 1x TSLA270119C00260000")
- Metrics: cost basis, current mark, unrealized P&L, DTE to nearest expiry
- Exit rules: profit target $, stop loss $, min DTE close

**Closed position card (dimmed):**
- Same as open but with: realized P&L, hold duration in days, exit reason
- Green left border for profit, red for loss

**Data source:** `positions` joined with `strategies` on `strategy_id`. Legs parsed from JSON column.

### Tab 4: Transactions
**Chronological order log** with filter bar (All / Opens / Closes / Paper / Backtest):
- Default filter: Paper only

**Table columns:** Date, Ticker, Intent (open/close), Status, Legs (abbreviated), P&L (for closes), Commission

**Summary footer:** Total transactions shown, net realized P&L, total commissions

**Data source:** `orders` table. Legs parsed from JSON column and abbreviated for display.

### Tab 5: Costs
**LLM cost breakdown:**
- Per-ticker spend from `ticker_state.cumulative_llm_usd`
- Per-category spend from `cost_ledger`
- Total spend

**Commission summary:**
- Total commissions from paper trades
- Total commissions from backtest (for reference)

## Data Queries

All queries are read-only SELECT against the existing schema. No new tables needed. Key joins:
- `evolver_proposals.strategy_id → strategies.id` for class name and params
- `positions.strategy_id → strategies.id` for strategy context
- `orders.strategy_id → strategies.id`
- `ticker_state` for phase, iteration count, paper trade count

## Ticker Filtering

When a user clicks a ticker in the Overview grid, the Evolver/Positions/Transactions tabs filter to that ticker. Implemented as client-side JS: all data is present in the HTML, filtering just toggles `display:none` on elements via a `data-ticker` attribute. A "Show All" button clears the filter.

## Styling

- Dark theme: background `#1a1a2e`, cards `#0f3460`, text `#e0e0e0`
- Accent blue `#4cc9f0`, green `#53d769`, red `#ff6b6b`, amber `#ffa500`
- Monospace font throughout (data-heavy dashboard)
- Responsive: works on a laptop screen, doesn't need to be mobile-optimized

## File Structure

```
bullbot/dashboard/
    generator.py      # Main script: query DB, build HTML, write file
    templates.py      # HTML template fragments as Python strings
```

The generator is a single module with no external dependencies. Template fragments are Python f-strings in `templates.py`, not a template engine.

## Integration

Add a call at the end of `scheduler.tick()`:
```python
from bullbot.dashboard import generator
generator.generate(conn)
```

Also callable standalone:
```bash
python -m bullbot.dashboard.generator
```

## Out of Scope

- No live refresh / WebSocket / polling
- No authentication
- No server process
- No charts in v1 (tables and cards only — charts can be added later if wanted)
- No mobile layout optimization
- No editing or control actions from the dashboard
