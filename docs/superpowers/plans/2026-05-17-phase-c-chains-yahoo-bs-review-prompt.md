# Phase C.1 Chains Plan — Review Prompt for Grok (or other external reviewer)

## How to use this bundle

Three files together form the review bundle:

1. `2026-05-17-phase-c-chains-yahoo-bs.md` — the implementation plan being reviewed (8 TDD tasks, ~34 unit tests, 1 integration test).
2. `2026-05-17-phase-c-chains-yahoo-bs-context.md` — project background: what Bull-Bot is, where Phase C stands, what this plan delivers and does NOT touch, conventions.
3. `2026-05-17-phase-c-chains-yahoo-bs-review-prompt.md` — this file: explicit ask for what to review.

Read the context document first, then the plan, then return the review structured as below.

For broader Phase C context (full design, prior Grok review response, list of all 7 sub-phases), see the spec bundle in `docs/superpowers/specs/2026-05-16-phase-c-vehicle-agent-*.md`.

## What you are reviewing

An implementation plan, NOT a design. The design was reviewed and approved (with Tier 1/2 changes) by you on 2026-05-16 — see `2026-05-16-phase-c-vehicle-agent-grok-review-response.md`. C.0 (schema + positions + risk) shipped on 2026-05-16 in PR [bull-bot#1](https://github.com/runiondd/bull-bot/pull/1).

This plan, C.1, builds the live Yahoo + Black-Scholes pricing layer (`bullbot/v2/chains.py`). It is the second of 7 sub-phases inside Phase C. The plan was written using the Superpowers `writing-plans` skill, which enforces test-first TDD, exact file paths, complete code blocks (no placeholders), and one commit per task.

## What you should review for

Please prioritize feedback in this order:

### Tier 1 — Things that would invalidate the plan

1. **Test correctness.** Are the BS textbook prices in Task 3 (ATM call ≈ 13.84, ATM put ≈ 9.45 with S=K=100, T=1y, IV=0.30, r=0.045) actually correct? If any value is off, the test passes when the implementation is wrong (or fails when it's right) — silent damage. Verify the numbers and flag any that need adjustment.
2. **Trader correctness of pricing fallbacks.** The plan's resolution order in Task 6 is: cached Yahoo mid → snapshot IV with BS → IV proxy with BS. Is this the right priority? Specifically: when Yahoo has cached a stale snapshot from a market-closed period (e.g., re-running the same `asof_ts` Saturday morning), `price_leg` will return the stale mid as `'yahoo'`. Is that a correctness bug worth fixing in C.1, or acceptable since the runner only calls with current-day `asof_ts`?
3. **IV proxy quality.** Formula is `realized_vol_30(underlying) × (vix_today / median(vix_60))` clamped to `[0.05, 3.0]`. For a name like MSTR where realized vol is decoupled from VIX (idiosyncratic crypto-driven moves), this proxy will mis-price options significantly. Is this acceptable for C.1 (forward mode only, Yahoo IV used when present), or does it need a per-ticker correction now?
4. **Atomic-persistence guarantee in Task 5.** The plan claims partial failure persists nothing by accumulating quotes in memory first and only committing at the end. But the per-call `_persist_quote` does an `INSERT OR REPLACE` against the live connection without an explicit `BEGIN`. SQLite autocommits per statement unless wrapped in a transaction. Will `conn.rollback()` actually undo prior `INSERT OR REPLACE` calls in this sequence? Flag if the transaction handling is wrong.

### Tier 2 — Things that would improve the plan

5. **Bid/ask = 0/0 edge case.** Yahoo returns `bid=0, ask=0` for illiquid strikes during a market-closed period. `ChainQuote.mid_price()` will return `0.0`, surfacing a "free option." The plan doesn't filter this. Should there be an explicit check?
6. **`net_basis` propagation through `price_leg`.** Share legs with non-None `net_basis` (assigned shares) currently return `(spot, 'bs')`. That's correct for MtM (current spot = mark) but the source tag is misleading. Should there be a separate tag like `'spot'` for share legs?
7. **`_load_bars` duplication.** The plan acknowledges duplicating the bar-loading helper from `bullbot/v2/runner.py` to keep the module self-contained. Is the duplication justified, or should it be promoted to a shared `bullbot/v2/_bar_loader.py` now to avoid drift later?
8. **`asof_ts` semantics for snapshot lookup.** The plan uses exact `asof_ts` match for snapshot lookups. If Yahoo was fetched at 16:05 and `price_leg` is called at 16:30 with a slightly different `asof_ts`, the snapshot lookup misses and falls back to BS. Is there a need for a "snapshot within N hours" tolerance window?

### Tier 3 — Things to flag but not necessarily fix

9. Is 8 tasks the right granularity, or should any be split / merged? Flag any task you think a subagent would struggle to complete end-to-end.
10. Anything in the "Notes for the implementer" section that you think should be promoted into the task body itself (because skipping it would silently break the implementation).
11. Anything in the test list that is missing a meaningful scenario (e.g., short-DTE expiry behavior, dividend-adjacent options, low-OI / wide-spread strikes).

## Format your response as

```
## Tier 1 findings

### Finding 1
- What: <one-sentence description>
- Why it matters: <2-3 sentences>
- Suggested change: <concrete edit to plan, e.g., "change Task 3 expected price from X to Y because...">

### Finding 2
...

## Tier 2 findings

(same format)

## Tier 3 findings

(same format)

## Things you got right (brief)

(short bulleted list of plan decisions you'd specifically endorse — not flattery, but useful for confirming the author's intuitions)

## Overall recommendation

(approve as-is / approve with the Tier 1 changes / reject and rewrite — pick one and justify in 3-5 sentences)
```

## Constraints on your review

- Do not propose using a paid data source (Polygon, ORATS, Tradier) as a Tier 1 requirement. The project will not pay for one in Phase C.
- Do not propose adopting a different broker or moving off Yahoo Finance as a Tier 1 requirement.
- Do not propose rewriting C.0 (already shipped) or expanding scope into C.4 (backtest harness) or C.5 (forward runner / dashboard).
- Do not write code. Suggest changes to the plan's task instructions or expected test values, not new code.
- The plan format (TDD steps with failing-test-first verification, one commit per task) is fixed by the Superpowers `writing-plans` skill — do not propose changing the plan structure itself, only the contents of individual tasks.
- Assume the reader (Dan) is a PM, not a backend engineer. Frame Tier 1 findings in terms of what would happen at runtime (wrong prices, lost data, silent failures), not in terms of refactoring opinions.
