# Bull-Bot v2 Phase C.1 Chains Plan — Grok Review Response

**Date:** 2026-05-17
**Status:** Grok review incorporated
**Plan reviewed:** `2026-05-17-phase-c-chains-yahoo-bs.md`
**Reviewer:** Grok (xAI), per `2026-05-17-phase-c-chains-yahoo-bs-review-prompt.md`
**Overall verdict from Grok:** approve with the Tier 1 changes

This document records, finding by finding, how the external review was incorporated into the C.1 implementation plan.

---

## Tier 1 — Findings that would invalidate the plan (both addressed)

### Finding A — Atomic-persistence transaction handling in Task 5

> The plan claims partial failure persists nothing by accumulating quotes in memory first and only committing at the end. But the per-call `_persist_quote` does an `INSERT OR REPLACE` against the live connection without an explicit `BEGIN`. SQLite autocommits per statement unless wrapped in a transaction. Will `conn.rollback()` actually undo prior `INSERT OR REPLACE` calls in this sequence?

**Addressed in:** Task 5 step 3, `fetch_chain` rewrite. Explicit transaction now wraps the persistence loop:
- `conn.execute("BEGIN")` before the first `_persist_quote` call.
- `conn.commit()` on success path.
- `conn.execute("ROLLBACK")` (NOT `conn.rollback()` alone, which is a no-op when no transaction is active) in the except branch.

Python's `sqlite3` module's default isolation level *can* auto-begin transactions, but the behavior changed across Python 3.6/3.12 and is fragile to rely on. Explicit `BEGIN` / `COMMIT` / `ROLLBACK` removes the ambiguity. The Task 5 partial-failure test (`test_fetch_chain_partial_failure_persists_nothing`) now becomes a meaningful guarantee instead of a coincidence of autocommit timing.

### Finding B — Documented freshness policy for cached Yahoo mids in Task 6

> When Yahoo has cached a stale snapshot from a market-closed period (e.g., re-running the same `asof_ts` Saturday morning), `price_leg` will return the stale mid as `'yahoo'`. Is that a correctness bug worth fixing in C.1, or acceptable since the runner only calls with current-day `asof_ts`?

**Addressed in:** Task 6 step 3, `price_leg` rewrite + a new `SNAPSHOT_FRESHNESS_SECONDS` module constant + 2 new test cases in Task 6 step 1.

New behavior:
- Module constant `SNAPSHOT_FRESHNESS_SECONDS = 86_400` (24 hours).
- The snapshot lookup compares the snapshot's `asof_ts` to the caller's `asof_ts`; if the snapshot is older than `SNAPSHOT_FRESHNESS_SECONDS`, the snapshot is treated as stale and the BS fallback runs instead. The snapshot's IV is still consulted as a hint for the BS pricer (better stale-IV than no IV).
- Source tag for a stale-snapshot fallback is `'bs'`, with a `_log.info` line noting the snapshot age — operator can audit in C.5 dashboard.
- The forward runner (C.5) only ever calls `price_leg` with current-day `asof_ts`, so this rule is a guardrail for the unusual cases: weekend re-runs, manual backfills, and the C.4 backtest harness when it re-uses cached snapshots from a prior backtest pass.

Two new tests in Task 6 step 1 cover the policy:
- `test_price_leg_falls_back_to_bs_when_snapshot_is_stale` — snapshot at `asof - 2 days`, caller asks for `asof` → returns `'bs'`, not `'yahoo'`.
- `test_price_leg_uses_snapshot_within_freshness_window` — snapshot at `asof - 12h`, caller asks for `asof` → returns `'yahoo'`.

---

## Tier 2 / Tier 3

Grok's overall response did not enumerate specific Tier 2 or Tier 3 findings beyond the endorsement list. The plan retains the original Tier 2 considerations from the review prompt as future hardening items (none gating C.1 ship):

- **Bid/ask = 0/0 filter** — left to forward observation. If real Yahoo responses surface this, add a filter in a follow-up commit; not a Tier 1 risk per Grok.
- **`net_basis` source tag on share legs** — left as `'bs'` for now since `v2_position_mtm.source` enum only allows `('yahoo', 'bs', 'mixed')`. Adding a new tag would require a schema migration; not justified at this stage.
- **`_load_bars` duplication vs shared module** — left duplicated. Will be promoted to a shared helper when C.4 backtest harness also needs it; YAGNI for C.1.
- **`asof_ts` tolerance window** — implicitly handled by Finding B's freshness policy (snapshots within 24h of requested asof are considered fresh).
- **Missing test scenarios** — Grok did not flag specific gaps.

---

## Things Grok endorsed (recorded for posterity)

- Reusing `bullbot.data.synthetic_chain.bs_price` and `realized_vol` instead of re-implementing BS math.
- Yahoo client as injectable callable default — testable without network or monkey-patch.
- Keeping the event-day IV bump out of C.1 and scoped to C.4 — preserves clean separation between forward pricing and backtest correction.
- TDD discipline + one-commit-per-task carried through from C.0.
- Explicit handling of share legs and expired options in `_price_leg_bs` (matters for MSTR/IBIT accumulate-intent flows).

---

## Summary of plan state

Both Tier 1 findings are now reflected in the plan document. Task 5 carries explicit `BEGIN`/`ROLLBACK` for transaction safety; Task 6 carries a `SNAPSHOT_FRESHNESS_SECONDS = 86_400` guardrail plus two new tests covering fresh vs stale snapshots. Total tests in C.1 now: 34 → 36 unit + 1 integration.

Plan is ready for implementation.
