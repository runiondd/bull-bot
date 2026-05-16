# Phase C Vehicle Agent — Review Prompt for Grok (or other external reviewer)

## How to use this bundle

Three files in this directory are intended to be reviewed together:

1. `2026-05-16-phase-c-vehicle-agent-design.md` — the design being reviewed.
2. `2026-05-16-phase-c-vehicle-agent-context.md` — project background: what Bull-Bot is, history, tech stack, constraints, what's already shipped, Dan's stated preferences.
3. `2026-05-16-phase-c-vehicle-agent-review-prompt.md` — this file: explicit ask for what to review.

Read the context document first, then the design, then return the review structured as below.

## What you are reviewing

A design for Phase C of a personal paper-trading research project. The project (Bull-Bot) emits a daily directional signal per ticker from a rules-based system (Phase A, shipped). It currently translates those signals into long/short shares (Phase B, shipped). Phase C replaces the share-only dispatcher with an LLM-picked options-and-shares agent, plus a backtest harness.

The author is one person (Dan), with help from Claude. The bot runs on a single Mac mini against Yahoo Finance data. There is no real money at risk in this phase.

## What you should review for

Please prioritize feedback in this order:

### Tier 1 — Things that would invalidate the design

1. **Trader correctness.** Does the proposed agent logic match how options actually behave? Are there failure modes that experienced options traders would consider obvious that the design ignores? (Examples worth scrutinizing: theta bleed assumptions, IV crush handling, gamma risk near expiry, dividend ex-dates, early assignment, pin risk.)
2. **Risk-cap calibration.** Three caps: per-trade max-loss 2% NAV, per-ticker concentration 15% NAV, total open positions 12. For a paper-trading agent that will graduate to real money, are these too tight, too loose, or wrong-shaped (i.e., the wrong dimensions to cap)?
3. **Backtest methodology validity.** The backtest uses Black-Scholes pricing on Yahoo bars with realized-vol-30d as the IV proxy and VIX-regime adjustment, restricted to ATM ±10% and 21-365 DTE. Is this rigorous enough to produce signal that survives forward-deployment, or will it systematically over/underestimate expected P&L? If the latter, what's the cheapest fix that doesn't require a paid historical chain subscription?
4. **Schema design.** The position model is `Position(intent, exit_plan_json, ...) + list[OptionLeg(action, kind, strike, expiry, qty)]` with linked-position chaining for wheel sequencing. Will this representation tear under any structure the design says it supports (calendars, diagonals, butterflies, ICs)? Any joins or queries it makes hard?

### Tier 2 — Things that would improve the design

5. **LLM prompt design.** The vehicle agent uses Haiku, given the JSON input shown in design §4.5. Are there inputs missing that an experienced trader would consider essential? Are any of the included inputs noisy or counterproductive?
6. **Exit-rule completeness.** The exit logic in §4.7 has trade-intent and accumulate-intent branches. Is there a third intent that should exist? Are there exit triggers missing for any of the leg structures in scope?
7. **Earnings handling.** Within 7 days of earnings, vehicle whitelist is restricted to defined-risk + short-premium structures. Is 7 days the right window? Is the whitelist the right set?
8. **Wheel sequencing.** Linked-position chaining for CSP → assigned → covered call → called away. Are there race conditions or state-machine gaps in this lifecycle?

### Tier 3 — Things to flag but not necessarily fix

9. Anything in the "out of scope" or "open questions" sections that you think should NOT be deferred — i.e., that Phase C should not ship without.
10. Anything in scope that should be cut as YAGNI.
11. Naming, file structure, or organization that would not survive the bot growing 3× in size.

## Format your response as

```
## Tier 1 findings

### Finding 1
- What: <one-sentence description>
- Why it matters: <2-3 sentences>
- Suggested change: <concrete>

### Finding 2
...

## Tier 2 findings

(same format)

## Tier 3 findings

(same format)

## Things you got right (brief)

(short bulleted list of design decisions you'd specifically endorse — not flattery, but useful for confirming the author's intuitions)

## Overall recommendation

(approve / approve with changes / reject and rethink — pick one and justify in 3-5 sentences)
```

## Constraints on your review

- Do not propose using a paid data source (Polygon, ORATS, Tradier) as a Tier 1 requirement. The project will not pay for one in Phase C. If your concern about backtest methodology can ONLY be solved with a paid feed, mark it Tier 2 and note the cost-blocker.
- Do not propose adopting a different broker or moving off Yahoo Finance as a Tier 1 requirement.
- Do not propose rewriting Phase A or Phase B. Those are shipped and out of scope for this review.
- Do not write code. Conceptual changes only. Implementation is downstream of this review.
- Assume the reader (Dan) is a PM, not a backend engineer. Frame Tier 1 findings in terms of what would happen to a trade, not in terms of refactoring opinions.
