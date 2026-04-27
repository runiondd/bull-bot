# Context Handoff
**Updated:** 2026-04-27

## Current State
`main` clean and pushed (HEAD `8f97cbd`). Two big things shipped since last handoff: dashboard reskin (4/26) and Phase 1 of the agentic-throughput rework (4/27). 423 unit + integration tests pass on pasture. Next scheduled run: 04-28 07:30 EDT.

## In Progress
None.

## Key Context
- **Agentic-throughput rework is a 5-phase project.** Spec at `docs/superpowers/specs/2026-04-27-agentic-throughput-design.md` (approved 4/27). Phase 1 (prompt caching + skip retired briefs) is shipped. Phases 2-5 are planned in the spec but not yet broken into implementation plans.
- **Phase 1 savings are intentionally small** (~$0.05/day). Real cost wins materialize in Phase 3 (batched proposals) and Phase 4 (iterations-per-tick > 1) because that's when many calls land within Anthropic's 5-minute cache window. Phase 1 is the foundation.
- **Dashboard URL:** `http://Daniels-MacBook-Pro-2.local:8080/` (or `192.168.1.220:8080`). `index.html → dashboard.html` symlink on pasture handles the directory-listing-vs-page issue.
- **TSLA paper position is down ~$1,500–1,600 unrealized.** User explicit decision (4/24): ride it. Paper trial is exactly the moment to observe. No kill_switch tripped.
- **`mark_to_mkt` is vestigial** (per Option Z); `unrealized_pnl` is authoritative.
- **`IMPLEMENTATION_PROMPT.md` from prior handoff was found** — was the dashboard-reskin spec at `~/Projects/bull-bot/dashboard/handoff/IMPLEMENTATION_PROMPT.md`. Reskin is shipped; that doc is now historical.

## Pending Work

### Agentic-throughput Phases 2-5 (priority: high)
2. **Phase 2 — Sonnet swap + A/B harness.** Switch proposer from Opus 4.6 → Sonnet 4.6, tag each proposal with `proposer_model`, run for 7 days, ship Sonnet if pass rate ≥ 80% of Opus. ~2-3 days. Plan not yet written.
3. **Phase 3 — Batched proposals (5 per LLM call).** Largest single throughput win. Parser change for `{"proposals": [...]}`; each batched proposal walk-forwarded independently. ~3-4 days.
4. **Phase 4 — Raise `PLATEAU_COUNTER_MAX` 3→10 + `ITERATIONS_PER_TICK` 1→5.** Pure config + small loop change. Resurrects the 8 retired tickers. ~1 day.
5. **Phase 5 — Universe expansion +10 tickers** (XLC, XLY, XLP, XLU, XLRE, XLB, TLT, UVXY, KRE, SMH). Yahoo bar backfill + config edit. ~1 day.

### Other open work (priority: medium)
- **`_dispatch_paper_trial` bug** still open. Promoted tickers (SATS, GOOGL) never fire dispatch — `paper_started_at` stays NULL or trades never open. Surfaces in the health brief. Real reason zero new paper positions in ~7 days. Worth a debugging session.

### Out of scope for now
- v2 health-brief checks deferred in spec (research-spend efficiency per ticker, regime drift, cross-day comparison, weekly LLM review agent).
- Dashboard charts (Chart.js / Plotly) — recommended but not started.
