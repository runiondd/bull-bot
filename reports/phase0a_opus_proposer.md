# Phase 0a — Opus 4.6 Proposer Validation

**Generated:** 2026-04-10 04:33:03Z
**Model:** `claude-opus-4-6`
**Calls:** 5
**Overall:** PASS ✅

## Summary

- **Successful calls:** 5/5
- **JSON valid (structural):** 5/5
- **p50 latency:** 8164 ms
- **p90 latency:** 8362 ms
- **Mean latency:** 8145 ms
- **Mean input tokens:** 1285
- **Mean output tokens:** 243
- **Mean $/call:** $0.0375
- **Total cost:** $0.1876

## Per-call detail

| # | latency (ms) | in tok | out tok | $/call | JSON valid |
|---|---|---|---|---|---|
| 1 | 8164 | 1285 | 241 | $0.0374 | YES |
| 2 | 7969 | 1285 | 241 | $0.0374 | YES |
| 3 | 7916 | 1285 | 241 | $0.0374 | YES |
| 4 | 8214 | 1285 | 253 | $0.0382 | YES |
| 5 | 8462 | 1285 | 240 | $0.0373 | YES |

## Sample response (call 1)

```json
{"class_name":"LongCall","params":{"dte":30,"delta":0.55,"exit_dte":7,"profit_target":1.5,"stop_loss":0.5,"sma_filter":"close_above_sma20","rsi_min":50,"rsi_max":70},"rationale":"All four prior iterations used short-premium strategies (credit spreads, iron condors) that degraded OOS, especially in bull regimes where upside moves erode short call legs or leave insufficient premium. With a confirmed
```

## Cost projection

At measured mean cost per call, 50 iterations per ticker (plateau safety cap) across the 10-ticker universe = 18.76 USD for a full discovery cycle.

Against the $1,000 research-ratthole kill threshold, that leaves 26656 iterations of headroom before the kill switch would fire.

## Conclusion

**Opus 4.6 validated as Bull-Bot v3 proposer.** All 5 calls succeeded and produced structurally valid JSON. PROPOSER_MODEL = 'claude-opus-4-6' is locked for Stage 1 build.