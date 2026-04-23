# Research Health Brief — design

**Status:** accepted
**Author:** brainstormed with the user, 2026-04-22
**Problem statement:** bull-bot produces a daily dashboard but does not surface meta-observations about its own research state. The user has to probe the DB by hand to notice issues like stagnant tickers, broken gates, or dead paper-trial promotions. The goal is to generate those observations automatically after every daily run so the bot surfaces what needs attention instead of requiring the operator to look for it.

## Goals

1. After every `scheduler.tick()`, produce a short markdown brief summarizing research health.
2. Write the brief to `reports/research_health_<unix_ts>.md` (one file per run, archival).
3. Embed a rendered HTML version in `reports/dashboard.html` so it is visible where the user already looks.
4. MVP covers four checks plus a header block — enough to catch the issues surfaced during the 2026-04-22 session review.

## Non-goals (deferred to v2)

- Research spend efficiency per ticker ($ LLM vs. strategies/verdicts produced).
- Unused data (tickers with bars but not in `UNIVERSE` or `REGIME_DATA_TICKERS`).
- Strategy class diversity scoring.
- Regime drift / kill-switch proximity.
- Cross-day trend comparison ("3 new plateaus this week").
- Severity tiers (`info`/`warn`/`crit`).
- A dashboard-visible history viewer (browse past briefs). MD files exist but are only readable via SSH for now.
- Weekly LLM-interpreted review agent (separate project).

## Architecture

### File layout

```
bullbot/research/__init__.py              # empty
bullbot/research/health.py                # ~200 LOC
tests/unit/test_research_health.py        # ~250 LOC
```

### Integration point

`scheduler.tick()` currently ends by calling `generator.generate(conn)` (scheduler.py:180-183). Insert health-brief generation *before* the dashboard so the dashboard can consume its output:

```python
# scheduler.tick(), last steps
try:
    from bullbot.research import health
    health.write_latest_brief(conn)
except Exception:
    log.exception("health brief generation failed")
try:
    from bullbot.dashboard import generator
    generator.generate(conn)
except Exception:
    log.exception("dashboard generation failed")
```

Same error-tolerant pattern the dashboard already uses. A crash in the health module must never fail the tick.

### Data flow

1. `scheduler.tick()` runs all ticker dispatches as today.
2. `health.write_latest_brief(conn)` builds a `HealthBrief`, renders it to markdown, writes to `reports/research_health_<unix_ts>.md`.
3. `dashboard.generator.generate(conn)` calls `health.generate_health_brief(conn).to_html()` inline and embeds the HTML in `reports/dashboard.html`. It does **not** read the markdown file — the HTML is regenerated from the DB every run. MD files are archival only.

## Public surface of `bullbot/research/health.py`

```python
from dataclasses import dataclass
from pathlib import Path
import sqlite3

@dataclass(frozen=True)
class CheckResult:
    title: str
    passed: bool                 # True = nothing to flag
    findings: list[str]          # one-line human-readable items

@dataclass(frozen=True)
class HealthBrief:
    generated_at: int            # unix ts
    header: dict[str, str]       # ordered label -> rendered line
    results: list[CheckResult]
    def to_markdown(self) -> str: ...
    def to_html(self) -> str: ...

def generate_health_brief(conn: sqlite3.Connection) -> HealthBrief: ...

def write_latest_brief(conn: sqlite3.Connection, reports_dir: Path | None = None) -> Path: ...
```

Only these four names are public. Check functions and the internal `_safe_check` helper are module-private.

## The four MVP checks

Each check is a pure function `(conn) -> CheckResult`. Each is wrapped internally in `_safe_check` so a SQL or logic error becomes a findings entry, never an uncaught exception.

### Check 1 — `check_data_shortfalls(conn)`

For each ticker in `config.UNIVERSE`:

```sql
SELECT COUNT(*) FROM bars WHERE ticker=? AND timeframe='1d'
```

Required bars ≈ `config.HEALTH_MIN_BARS_FOR_WF` (default `WF_WINDOW_MONTHS * 21` = 504 for 24 months). Tickers with fewer bars get a finding:

```
XLK: 257 bars (need ~504 for 24mo walkforward)
```

Passed iff all UNIVERSE tickers meet the minimum.

### Check 2 — `check_pf_inf(conn)`

```sql
SELECT ticker, best_pf_oos, best_strategy_id
FROM ticker_state
WHERE best_pf_oos IS NOT NULL AND best_pf_oos > ?
```

Threshold is `config.HEALTH_PF_OOS_ABSURD_THRESHOLD` (default `1e10` — catches IEEE `inf` and absurdly-large values). Per row:

```
TSLA: best_pf_oos=138.7 (strategy 114) — likely sample-size artifact or /0
```

For true `inf`, render as the literal string `inf` rather than `1.7976931348623157e308`. Passed iff 0 rows.

### Check 3 — `check_dead_paper_trials(conn)`

Two sub-conditions, both surface "promoted but not trading":

```sql
-- A: promotion recorded but dispatch never fired
SELECT ticker, verdict_at
FROM ticker_state
WHERE phase='paper_trial'
  AND paper_started_at IS NULL
  AND verdict_at IS NOT NULL
  AND verdict_at < ?  -- now - N*86400

-- B: started paper trading but 0 live trades after N days
SELECT ticker, paper_started_at
FROM ticker_state
WHERE phase='paper_trial'
  AND paper_started_at IS NOT NULL
  AND paper_trade_count = 0
  AND paper_started_at < ?  -- now - N*86400
```

`N = config.HEALTH_DEAD_PAPER_DAYS` (default `3`).

Per finding:

```
SATS: promoted 2 days ago, paper_trial dispatch has never fired
GOOGL: started paper trading 4 days ago, 0 live trades
```

Passed iff 0 rows across both sub-conditions.

### Check 4 — `check_iteration_failures(conn)`

```sql
SELECT ticker, exc_type, COUNT(*) AS n
FROM iteration_failures
WHERE ts > ?   -- now - 86400
GROUP BY ticker, exc_type
ORDER BY n DESC, ticker
```

Per row:

```
AAPL: 2 × DailyRefreshError (last 24h)
```

Passed iff 0 rows.

## Header block (always included, not a check)

Ordered dict, rendered as one `**Label:** value` line each in markdown and as a `<dl>` in HTML.

| Label | Derivation |
|---|---|
| `Universe` | `"16 tickers (6 discovering, 4 paper_trial, 2 no_edge)"` from `SELECT phase, COUNT(*) FROM ticker_state GROUP BY phase`, cross-checked against `len(config.UNIVERSE)` |
| `Strategy pool` | `"146 (+8 today)"` from `SELECT COUNT(*) FROM strategies` and `SELECT COUNT(*) FROM strategies WHERE created_at > :today_utc` |
| `LLM spend today` | `"$0.38"` from `SELECT COALESCE(SUM(amount_usd),0) FROM cost_ledger WHERE category='llm' AND ts > :today_utc` |
| `Live positions` | `"0 open, 0 closed today ($0.00 realized)"` from `positions` with `run_id='live'` — counts: open = rows where `closed_at IS NULL`; closed today = rows where `closed_at > :today_utc`; realized = `SUM(pnl_realized)` over closed-today |

`:today_utc` is defined as `int(datetime.combine(date.today(), time.min, tzinfo=timezone.utc).timestamp())` — unix seconds at 00:00 UTC of the current calendar date. Not localized; logs and DB are already UTC. The same value is threaded through all header queries for consistency within a single brief.

## Config additions

In `bullbot/config.py`:

```python
# --- Research health brief ---
HEALTH_DEAD_PAPER_DAYS = 3
HEALTH_MIN_BARS_FOR_WF = WF_WINDOW_MONTHS * 21   # ~504 for 24mo
HEALTH_PF_OOS_ABSURD_THRESHOLD = 1e10
```

All three are thresholds the operator may want to tune. Match the existing convention of every tunable constant living in `config.py`.

Extend `tests/unit/test_config.py` with assertions for the three new constants.

## Rendering

### `HealthBrief.to_markdown()`

```
# Research Health — 2026-04-22T07:39Z

**Universe:** 16 tickers (6 discovering, 4 paper_trial, 2 no_edge)
**Strategy pool:** 146 (+8 today)
**LLM spend today:** $0.38
**Live positions:** 0 open, 0 closed today ($0.00 realized)

## Data shortfalls — FLAG (6)
- XLK: 257 bars (need ~504 for 24mo walkforward)
- XLF: 257 bars (need ~504)
- ...

## pf_oos anomalies — FLAG (5)
- AAPL: best_pf_oos=inf (strategy 123) — sample-size artifact or /0
- ...

## Dead paper trials — FLAG (1)
- SATS: promoted 2 days ago, paper_trial dispatch has never fired

## Iteration failures (24h) — OK
```

- Timestamp is ISO 8601 UTC with `Z` suffix.
- A check with `passed=True` renders as `## Title — OK` on a single line, no bullets.
- A check with `passed=False` renders as `## Title — FLAG (N)` followed by a bulleted list.
- Findings are rendered verbatim (trusted, bot-generated).

### `HealthBrief.to_html()`

Deterministic, hand-built HTML — no markdown parser dependency. Structure:

```html
<section class="research-health">
  <h2>Research Health — 2026-04-22T07:39Z</h2>
  <dl class="health-header">
    <dt>Universe</dt><dd>16 tickers (...)</dd>
    ...
  </dl>
  <section class="check check-flag">
    <h3>Data shortfalls — FLAG (6)</h3>
    <ul>
      <li>XLK: 257 bars (...)</li>
      ...
    </ul>
  </section>
  <section class="check check-ok">
    <h3>Iteration failures (24h) — OK</h3>
  </section>
</section>
```

All user-supplied strings must be HTML-escaped via `html.escape()`. Dashboard CSS (in `bullbot/dashboard/templates.py`) extended to style `.research-health`, `.check-flag`, `.check-ok`.

## Error handling

### Per-check isolation

```python
def _safe_check(fn, conn) -> CheckResult:
    try:
        return fn(conn)
    except Exception as exc:
        log.exception("health check %s crashed", fn.__name__)
        return CheckResult(
            title=fn.__name__,
            passed=False,
            findings=[f"check crashed: {type(exc).__name__}: {exc}"],
        )
```

Every check in the registry is invoked through `_safe_check`. One failing check must not prevent the other three from rendering.

### Scheduler-level isolation

The `try/except` wrapper added to `scheduler.tick()` (shown in the integration section) is the outer safety net. Combined with per-check isolation, the brief always produces *something* — either real findings or crash markers — and the tick never fails because of health logic.

### Idempotency

`write_latest_brief` uses `int(time.time())` in the filename. If called twice in the same second (only possible in tests), the second write overwrites the first. Fine.

## Testing strategy

Tests live in `tests/unit/test_research_health.py`. All tests use an in-memory `sqlite3.connect(":memory:")` fixture that creates the relevant tables and seeds them per-test.

### Per-check tests

For each of the four checks, two tests:
1. **Clean DB test**: seed DB with data that should NOT trigger the check → assert `result.passed is True` and `result.findings == []`.
2. **Flag test**: seed DB with data that SHOULD trigger → assert `result.passed is False` and findings contain expected ticker names / key substrings.

Assert on substrings, not exact strings, so renderer tweaks don't require test updates.

### Renderer tests

- `HealthBrief.to_markdown()`: construct a `HealthBrief` with 2 passing + 2 flagging checks manually → assert the output contains expected headings, the `— OK` tokens for passing checks, the `— FLAG (N)` tokens with correct counts, and each finding on its own line.
- `HealthBrief.to_html()`: same input → assert required HTML structure (`<section class="check check-ok">`, etc.) and that special characters in findings are escaped (seed one finding with `<script>` and verify it renders as `&lt;script&gt;`).

### `_safe_check` test

Pass a function that raises `ValueError("boom")` → assert the returned `CheckResult` has `passed=False`, `title` equal to the function name, and findings contains `"boom"`.

### `write_latest_brief` test

Use `tmp_path` pytest fixture. Call `write_latest_brief(conn, reports_dir=tmp_path)` → assert a single file matching `research_health_*.md` exists in `tmp_path`, its content starts with `# Research Health`, and `conn` is not closed.

### Integration test (optional, keep small)

One integration test that calls `scheduler.tick()` with a fully-seeded fixture DB and asserts a `research_health_*.md` file appears in the reports directory. Confirms the scheduler wiring, no deeper.

## Open questions

None at spec-write time. The only judgment call is `HEALTH_DEAD_PAPER_DAYS = 3`, which the user accepted explicitly.

## Sequencing hint for the implementation plan

1. Write the spec (this file).
2. Write `tests/unit/test_research_health.py` red, including renderer and per-check cases — drives the public API.
3. Implement `bullbot/research/health.py` until tests pass.
4. Add the three config constants and matching `test_config.py` assertions.
5. Wire into `scheduler.tick()` behind the `try/except` shown.
6. Extend `bullbot/dashboard/generator.py` and `templates.py` to render the HTML section and style it.
7. Deploy: push, pull on pasture, let tomorrow's 07:30 run produce the first real brief.
