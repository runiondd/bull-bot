# Context Handoff
**Updated:** 2026-04-25

## Current State
`main` clean and pushed (HEAD `8752ef6` — no new commits today; this was a validation day). Two scheduled runs since the 4/23 ship (07:30 on 4/24 and 07:30 on 4/25). Next: tomorrow 07:30 EDT.

## What 4/24 + 4/25 confirmed in production
Every piece of the 4/23 work is operating as designed:
- Research-health brief auto-writes after every `scheduler.tick()`.
- `pf_oos anomalies — OK` consistently — `PF_CEILING=10.0` cap holding, no new inf values.
- `Dead paper trials — FLAG (1)` for SATS as of 4/24 ("promoted 3 days ago, paper_trial dispatch has never fired") — the `COALESCE(verdict_at, updated_at)` fallback fired exactly as predicted.
- `unrealized_pnl` populated and drifting daily. SPY ~flat at -$8 both days. TSLA went -$915 → **-$1,651** overnight 4/23→4/24 (8% leg down). User decision: **ride it** — paper trial is exactly the moment to observe. No kill_switch tripped.
- Dashboard renders split "Realized P&L" / "Unrealized P&L" cards.

## In Progress
None.

## Key Context
- **Anthropic shipped "Custom Visuals" in March 2026** but it's locked to Claude.ai + Cowork chat — not embeddable in third-party static HTML. Wrong tool for our cron-regenerated `dashboard.html`. If we want charts, the right path is **Chart.js** (or Plotly) embedded at build time. Not started; user asked, recommendation given.
- **User mentioned an `IMPLEMENTATION_PROMPT.md`** late in the session that should drive next steps — `find` couldn't locate it under `~/Projects/bull-bot`, `~/Downloads`, `~/Desktop`, `~/Documents`. Ask user where it is before proceeding with anything else.
- The `_dispatch_paper_trial` bug for SATS/GOOGL (promoted tickers whose dispatch never fires) is still open — surfaces in the health brief but no one's investigated the root cause yet.
- **`mark_to_mkt` column is vestigial** (per Option Z): still written on open/close, but `unrealized_pnl` is the authoritative unrealized value. Don't "fix" `mark_to_mkt`.

## Pending Work
1. **Locate `IMPLEMENTATION_PROMPT.md`** — ask user where it is or what it should contain.
2. **Dashboard charts** (if user wants to pursue): Chart.js or Plotly embedded at build time. Candidates: equity curve over time, per-ticker realized+unrealized P&L bars, strategy pool growth, evolver-verdict mix over time. ~1-2h scoped.
3. **`_dispatch_paper_trial` investigation** — root-cause why promoted tickers (SATS, GOOGL) don't dispatch.
4. **`PLATEAU_COUNTER_MAX=3` is aggressive** — half the universe retired to no_edge after only 3 iterations. Consider raising to 5-7.
5. v2 health-brief checks deferred in original spec: research-spend efficiency per ticker, unused-data detection, strategy class diversity, regime drift, cross-day trend comparison, weekly LLM-interpreted review agent.
