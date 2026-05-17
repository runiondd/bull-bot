# Phase C.2 Levels Plan — Review Prompt for Grok (or other external reviewer)

## How to use this bundle

Three files together form the review bundle:

1. `2026-05-17-phase-c2-levels-support-resistance.md` — the implementation plan (7 TDD tasks, ~33 unit tests).
2. `2026-05-17-phase-c2-levels-support-resistance-context.md` — project background.
3. `2026-05-17-phase-c2-levels-support-resistance-review-prompt.md` — this file.

Read the context document first, then the plan, then return the review structured as below.

## What you are reviewing

An implementation plan for Phase C.2 of the Bull-Bot v2 vehicle-agent rollout. C.0 (schema + positions + risk) and C.1 (Yahoo + Black-Scholes chains) are already merged into `main`. C.2 ships `bullbot/v2/levels.py` — a pure, stdlib-only support/resistance calculator that the C.3 vehicle agent will consume as LLM context and that `exits.py` will use to set entry-time profit-target and stop-price values.

The plan was written using the Superpowers `writing-plans` skill (same skill used for C.0 and C.1). It is 7 TDD tasks: Level dataclass → 4 private helpers (swing extrema, SMA, round-number, dedup) → public `compute_sr` orchestrator → regression check.

## What you should review for

Please prioritize feedback in this order:

### Tier 1 — Things that would invalidate the plan

1. **Swing-detection correctness.** The plan uses strict-less-than for swing high confirmation: `bars[j].high < cand_high for all j in window, j != i`. Plateau bars (consecutive bars at the SAME high) will produce NO swing high because no bar is strictly greater. Is this the right behavior or should ties be handled differently (e.g., use `<=` for some neighbors)? Real-world stocks frequently form double-tops at the same level.
2. **Strength scaling math.** Swing strength = `min(1.0, touch_count / 5.0)`. SMA strength = `min(1.0, window / 200.0)`. Round-number strength = fixed `0.3`. Are these comparable on a 0–1 scale? When the C.3 vehicle agent sees `nearest_resistance.strength = 0.8` and `nearest_support.strength = 0.3`, will those numbers communicate what an experienced trader would expect?
3. **Round-number step-size table.** `$1 / $5 / $10 / $50` at `< $50 / < $200 / < $1000 / >= $1000`. The 200/1000 thresholds are arbitrary — should they be quartiles of the universe, log-scaled, or something else? For MSTR (currently ~$400), the step is $10 which feels right; for a $1500 BRK.B, the step is $50 which also feels right. Are there pathological cases?
4. **Dedup semantics.** Within-0.5% clusters collapse to the highest-strength level (tiebreak by `swing > sma_200 > sma_50 > sma_20 > round_number`). Is 0.5% the right band? For a $400 stock that's $2 — about a tick or two of normal noise. For a $50 stock that's $0.25 — finer than typical resolution. Is this OK or should the band scale?

### Tier 2 — Things that would improve the plan

5. **Lookback vs full-history split for SMAs.** The plan uses `lookback=60` for swing detection but feeds SMAs the full bar history. Is this right? Or should everything use the same lookback? An older 200-day SMA computed over the most recent 200 bars vs a 200-day SMA over a longer history with the most recent 200 sampled — semantically the same, but worth confirming.
6. **Bar shape duck-typing vs Pydantic.** The plan uses `SimpleNamespace` bars (duck-typed) matching v2 convention. The repo also has a strict Pydantic `Bar` schema in `bullbot/data/schemas.py` used by the v1 path. Is the duck-typing OK, or should `levels.py` accept the Pydantic Bar too (or convert)?
7. **Missing test scenarios.** Any meaningful S/R scenarios the test list misses? Examples: gap days where high << prev_low, single-day spikes that are then immediately retraced, bars with `low > close` (shouldn't happen but if it does), very long flat periods, plateau swing detection (related to Finding 1).

### Tier 3 — Things to flag but not necessarily fix

8. Is the 7-task granularity right, or should any tasks be split / merged for subagent execution?
9. Anything in "Notes for the implementer" that should be promoted into a task body?
10. Is the strength heuristic over- or under-engineered for what the C.3 LLM agent will actually use?

## Format your response as

```
## Tier 1 findings

### Finding 1
- What: <one-sentence description>
- Why it matters: <2-3 sentences>
- Suggested change: <concrete edit to plan>

### Finding 2
...

## Tier 2 findings

(same format)

## Tier 3 findings

(same format)

## Things you got right (brief)

(short bulleted list of plan decisions you'd specifically endorse)

## Overall recommendation

(approve as-is / approve with the Tier 1 changes / reject and rewrite — pick one and justify in 3-5 sentences)
```

## Constraints on your review

- Do not propose using a paid data source or moving off Yahoo Finance — out of scope.
- Do not propose changing the TDD plan structure (failing-test-first, one commit per task) — that's locked by the `writing-plans` skill.
- Do not propose adding NumPy or pandas — stdlib-only is a deliberate constraint to keep the v2 codepath dependency-light.
- Do not rewrite C.0 or C.1 (shipped) or expand into C.3+ scope (vehicle agent, backtest, runner) — those have their own plans.
- The reader (Dan) is a PM, not a backend engineer. Frame Tier 1 findings in terms of trading consequences (wrong levels surfaced to the LLM, missed entries, false signals), not refactor opinions.
