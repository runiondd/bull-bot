# Agentic Throughput — design

**Status:** accepted (user approved option 1: stack all levers, phased rollout, 2026-04-27)
**Author:** brainstormed in session, 2026-04-27
**Problem statement:** the bot is throttled to ~5 strategy proposals per day with 5 active tickers, despite costing only $0.33/day. The user has a $10k/month profit target — research overhead is irrelevant against that. The current cadence is hill-climbing on a single proposal per ticker per tick. The agentic premise (cheap proposals, expensive validation) is inverted: we use expensive proposals (Opus, no caching) and free validation (walkforward backtests on cached bars). This spec re-orients the system to actually run at agentic pace.

## Goals

1. Increase strategy proposals per day from ~5 to **~650** (130×) without changing the edge gate.
2. Keep total LLM cost under **$2.00/day** (still well under 1% of profit target).
3. Resurrect the 8 retired (`no_edge`) tickers so they get re-explored, not abandoned after 3 plateau iterations.
4. Expand `UNIVERSE` from 16 to 26 to widen the candidate pool.

## Non-goals

- Changing the edge gate. `EDGE_PF_OOS_MIN = 1.3`, `EDGE_TRADE_COUNT_MIN = 5`, `EDGE_PF_IS_MIN = 1.5` stay put.
- Changing the strategy-class library (PCS, IC, CCS, CSP, CCO, BPS, GrowthLEAPS).
- Changing what "paper trial" means or the promotion gate (`PAPER_TRADE_COUNT_MIN = 5`, `PAPER_TRIAL_DAYS = 21`, `FAITHFULNESS_DELTA_MAX = 0.30`).
- Fixing the `_dispatch_paper_trial` bug for SATS / GOOGL — separate spec.
- Building a real-time / intraday tick. Daily tick with N iterations stays.

## Levers and stacked outcome

| # | Lever | Effect |
|---|---|---|
| 1 | Prompt caching on proposer + ticker briefs | Mark static prompt parts cacheable; ~80% billing reduction on cache hits, transparent to model output. |
| 2 | Batch 5 proposals per LLM call | One call returns 5 distinct strategy proposals against the same ticker context; per-proposal overhead drops ~40%. |
| 3 | Switch proposer Opus → Sonnet | ~75% cheaper per call; quality A/B-tested with same gate. |
| 4 | Skip ticker briefs for retired tickers (`phase IN ('no_edge', 'killed')`) | Saves ~$0.04/day; nil quality impact (briefs were never read by retired tickers). |
| 5 | Raise `PLATEAU_COUNTER_MAX` 3 → 10 | Resurrects 8 retired tickers; lets exploration run longer before retirement. |
| 6 | `ITERATIONS_PER_TICK` 1 → 5 | Each `discovering` ticker gets 5 evolver iterations per daily tick instead of 1. |
| 7 | Expand `UNIVERSE` +10 tickers | XLC, XLY, XLP, XLU, XLRE, XLB, TLT, UVXY, KRE, SMH. |

### Throughput math

| | Today | After |
|---|---|---|
| `discovering` tickers | 5 | ~26 (resurrect retired + universe expansion) |
| Iterations per ticker per tick | 1 | 5 |
| Proposals per LLM call | 1 | 5 |
| **Proposals per day** | **5** | **~650** |
| Proposer model | Opus 4.6 | Sonnet 4.6 |
| Prompt caching | off | on |
| Cost per proposal | ~$0.05 | ~$0.001–0.002 |
| **Total daily LLM cost** | **$0.33** | **~$0.80–$1.20** |
| Annual cost | $120 | ~$365 |

130× more proposals validated against the same gate, for ~3× the dollars. Still trivially under budget.

## Architecture

### Files affected

```
NEW   bullbot/llm/__init__.py                  (empty package marker)
NEW   bullbot/llm/cache.py                     ~80 LOC — cached-block helper
MOD   bullbot/evolver/proposer.py              accept n_proposals arg, return list[Proposal]
MOD   bullbot/evolver/iteration.py             loop ITERATIONS_PER_TICK times, walkforward each batched proposal
MOD   bullbot/features/regime_agent.py         skip ticker_brief refresh for retired tickers
MOD   bullbot/scheduler.py                     no change in dispatch logic; iteration.py owns the loop
MOD   bullbot/config.py                        new tuning knobs (see below)

NEW   tests/unit/test_llm_cache.py
MOD   tests/unit/test_evolver_proposer.py      batched-proposal parser tests
MOD   tests/unit/test_evolver_iteration.py     loop-count + walkforward-each tests
MOD   tests/unit/test_regime_agent.py          retired-ticker skip test
MOD   tests/unit/test_config.py                assert new constants
```

### New config knobs

```python
# --- Agentic throughput ---

PROPOSER_CACHE_ENABLED = True
PROPOSER_BATCH_SIZE = 5              # strategies per LLM call
ITERATIONS_PER_TICK = 5              # evolver iterations per discovering ticker per tick
PROPOSER_MODEL = "claude-sonnet-4-6"  # was claude-opus-4-6
PROPOSER_MODEL_FALLBACK = "claude-haiku-4-6"
SKIP_BRIEFS_FOR_RETIRED = True
```

`PLATEAU_COUNTER_MAX` raised from 3 to 10. `UNIVERSE` extended with the 10 candidate tickers.

### Prompt caching strategy

Anthropic's API supports `cache_control: {"type": "ephemeral"}` on individual content blocks. We mark blocks that don't change call-to-call as cacheable; the API returns 90% off on cache hits.

For the proposer call, the prompt is built from these blocks:

| Block | Cacheable? | Reason |
|---|---|---|
| System prompt | yes | Identical across all proposer calls |
| Strategy-class catalog (PCS, IC, CCS, …) | yes | Static reference |
| Output format instructions | yes | Static |
| Per-ticker iteration history | partially | The first N-1 iterations are stable; only the latest changes |
| Current regime brief | no | Changes daily |
| Current ticker brief | no | Changes daily |

Implementation: `bullbot/llm/cache.py` exposes a helper `build_cached_blocks(system, catalog, history, fresh)` that returns a list of `MessagesParam` content blocks with the right `cache_control` markers. Proposer + ticker_brief callers use it.

### Batched proposals

Today's proposer returns one strategy:
```json
{"class_name": "PutCreditSpread", "params": {...}, "rationale": "..."}
```

After the change, it returns 5:
```json
{
  "proposals": [
    {"class_name": "PutCreditSpread", "params": {...}, "rationale": "..."},
    {"class_name": "IronCondor", "params": {...}, "rationale": "..."},
    ...
  ]
}
```

The system prompt is updated to ask for 5 distinct strategies (different classes, different parameter regions, different theses) and to forbid duplicates. Walkforward backtests each one independently; only those that pass the edge gate are persisted to `strategies` and `evolver_proposals`. Failed proposals are still logged in `evolver_proposals` with `passed_gate=0` for retro analysis.

### Iteration loop

`iteration.run(conn, ...)` currently does one `propose_strategy → backtest → record` cycle per call. After the change, it loops `ITERATIONS_PER_TICK` times. Each iteration's history feeds the next iteration's prompt cache (so iteration N reads the cached blocks of iterations 1..N-1).

For batched proposals, each iteration returns a list of 5 candidates; all 5 get backtested in the same iteration, but they're treated as one "iteration" for plateau-counting purposes (the best of the 5 is what advances the ticker's `best_pf_oos`).

### Skip briefs for retired tickers

`scheduler._refresh_regime` currently iterates `config.UNIVERSE` and refreshes a brief for each. Add a check: if `ticker_state.phase IN ('no_edge', 'killed')` and `SKIP_BRIEFS_FOR_RETIRED` is True, skip. The brief data isn't read by anything other than the proposer, and retired tickers don't run the proposer.

## Phased rollout

Each phase ships separately, in this order:

1. **Phase 1 (~1 day):** Prompt caching + skip-briefs-for-retired.
   - Lowest risk; no behavior change. Measure cache hit rate on day 2 of production.
2. **Phase 2 (~2 days):** Sonnet swap, with A/B harness.
   - Tag each proposal with `proposer_model`. For one week, half the tickers run Sonnet, half run Opus. Compare gate-pass rates.
3. **Phase 3 (~3 days):** Batched proposals (5 per call).
   - Schema change for proposer output. Parser tests with synthetic LLM responses.
4. **Phase 4 (~1 day):** Raise `PLATEAU_COUNTER_MAX` 3 → 10 + `ITERATIONS_PER_TICK` 1 → 5.
   - Config-only. Resurrects retired tickers via the existing `cli.py rearm` command (run once during deploy).
5. **Phase 5 (~1 day):** Universe expansion (+10 tickers).
   - Bar backfill for the new tickers (Yahoo, 5y), then config edit.

## Testing strategy

### Unit tests
- `test_llm_cache.py`: building cached-prompt blocks; sentinel tokens persist; structurally correct `cache_control` markers.
- `test_evolver_proposer.py`: parser handles the new `{"proposals": [...]}` shape; rejects malformed responses; emits `n` Proposal objects.
- `test_evolver_iteration.py`: looping `ITERATIONS_PER_TICK` times produces N×N evolver_proposals rows; best-of-batch advances `best_pf_oos`.
- `test_regime_agent.py`: skipping briefs for retired tickers works only when `SKIP_BRIEFS_FOR_RETIRED=True`.
- `test_config.py`: new constants exist with the right defaults; `PLATEAU_COUNTER_MAX == 10`.

### Integration tests
- Full tick with `ITERATIONS_PER_TICK=3` and `PROPOSER_BATCH_SIZE=3` finishes within 10 minutes; produces ≥9 evolver_proposals rows for one discovering ticker.
- Cache-billing assertion: dispatched twice on the same day, second tick's cost ledger shows ≥80% reduction in proposer cost.

### A/B harness (Sonnet vs Opus)
- `proposer_model` field added to `evolver_proposals`.
- After 7 days, compare:
  - Pass rate (proposals passing edge gate) by model
  - Avg `pf_oos` by model
  - Latency / token usage by model
- Decision rule: if Sonnet's pass rate ≥ 80% of Opus's, ship Sonnet. Else revert.

## Error handling

- **Cache miss / cache invalidation:** the API silently degrades to full billing. No crash, just higher cost. Detected by daily cost-ledger spike.
- **Malformed batched response:** parser returns 0 proposals; iteration logs a warning and continues. The ticker just doesn't progress that iteration.
- **Sonnet quality regression:** A/B harness catches it within 7 days. Manual revert via config flip.

## Migration

- Phase 1 deploys cleanly: cache markers don't break uncached calls.
- Phase 2 deploys via `PROPOSER_MODEL` config swap. Reversible.
- Phase 3 introduces a new response shape; the parser handles both shapes during transition (single-proposal fallback if `proposals` key missing).
- Phase 4 needs a one-time `cli.py rearm --all-no-edge` to flip retired tickers back to `discovering`.
- Phase 5 needs Yahoo bar backfill (existing tooling).

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Cached prompt becomes stale (history changes mid-day) | low | Cache key includes the static-tail-truncation point; iteration appends are below the cache boundary |
| Sonnet proposals are noticeably worse | med | A/B harness; revert if fail |
| Batched proposals are correlated (LLM emits 5 variants of the same idea) | med | Prompt explicitly forbids duplicates; walkforward gate filters anyway |
| Raised plateau wastes compute on truly dead tickers | low | Cost is ~$0.005 × dead-ticker-iterations/day; trivial |
| Universe expansion has thinner option chains for some tickers (UVXY, KRE) | med | Health-brief data shortfall check already flags missing bar data; fill model already handles thin chains |

## Open questions

- **Cache TTL:** Anthropic's default is 5 minutes. Daily ticks are 24h apart. Each tick will see a cache miss on its first call, then hits on subsequent calls within the tick. This works in our favor — the first proposer call per tick warms the cache, the next 4 iterations × 5 batched proposals = 24 more calls all hit the cache. We don't need to do anything special.
- **A/B sample size:** with 5 discovering tickers × 5 iterations × 5 batched = 125 proposals/day, one week of A/B = 875 proposals. Plenty of statistical power for a binary pass/fail rate comparison.

## Acceptance criteria

- [ ] Cache hit rate ≥ 60% on proposer calls (measured via cost-ledger anomalies, or via Anthropic's response headers if surfaced).
- [ ] Daily proposal count ≥ 100 within 3 days of Phase 4 ship.
- [ ] Daily LLM cost ≤ $2.00 sustained.
- [ ] Sonnet pass rate within 80% of Opus over a 7-day window (else revert).
- [ ] Universe size ≥ 25 active tickers (`phase != 'killed'`).
- [ ] No regression on `EDGE_PF_OOS_MIN`, `EDGE_TRADE_COUNT_MIN`, or paper-promotion gate.

## Sequencing hint for the implementation plan

1. Phase 1: prompt caching scaffolding + cost-ledger metric for cache hits + skip-briefs-for-retired
2. Phase 2: A/B harness (`proposer_model` field) → Sonnet swap
3. Phase 3: batched proposer + parser
4. Phase 4: raise plateau + iterations + rearm script
5. Phase 5: universe expansion + Yahoo backfill
