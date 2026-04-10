# Phase 0 — Polygon API Validation

**Generated:** 2026-04-10 00:26:56Z  
**Overall:** ❌ FAIL  
**Client rate limit:** 5.0 req/sec (token bucket)  
**Requests issued:** 12  

## Probe Summary

| Probe | Result | Detail |
|---|---|---|
| `spy_weekly_10y` | ❌ | 262 bars, first=2021-04-04..last=2026-04-05, slack=1825d, age=4d, next_url=False [depth FAIL (slack=1825d)] |
| `spy_weekly_10y_narrow` | ❌ | request failed |
| `spy_daily_5y` | ✅ | 1255 bars, first=2021-04-12..last=2026-04-09, slack=8d, age=0d, next_url=False [OK] |
| `spy_daily_5y_narrow` | ✅ | 5 bars in far-end 14-day window 2021-04-04..2021-04-18 (first=2021-04-12, last=2021-04-16) — tier DOES support this depth |
| `tsla_4h_3y` | ❌ | 217 bars, first=2023-04-04..last=2023-06-22, slack=0d, age=1022d, next_url=True [freshness FAIL (last_bar 1022d old)] |
| `tsla_4h_3y_narrow` | ✅ | 40 bars in far-end 14-day window 2023-04-04..2023-04-18 (first=2023-04-04, last=2023-04-18) — tier DOES support this depth |
| `tsla_1h_2y` | ❌ | 904 bars, first=2024-04-03..last=2024-06-24, slack=0d, age=654d, next_url=True [freshness FAIL (last_bar 654d old)] |
| `tsla_1h_2y_narrow` | ✅ | 176 bars in far-end 14-day window 2024-04-03..2024-04-17 (first=2024-04-03, last=2024-04-17) — tier DOES support this depth |
| `tsla_15m_1y` | ❌ | 3358 bars, first=2025-04-03..last=2025-06-18, slack=0d, age=295d, next_url=True [freshness FAIL (last_bar 295d old)] |
| `tsla_15m_1y_narrow` | ✅ | 704 bars in far-end 14-day window 2025-04-03..2025-04-17 (first=2025-04-03, last=2025-04-17) — tier DOES support this depth |
| `tsla_options_chain` | ✅ | 250 puts across 3 expirations; front month 2026-04-10 has 141 strikes; picked O:TSLA260410P00405000 @ strike 405 |
| `tsla_option_snapshot` | ✅ | snapshot ok for O:TSLA260410P00405000: iv=None delta=None bid=None ask=None day_volume=92 |

## Historical Depth — Wide Probes

| Probe | Ticker | Bars | First Bar | Last Bar | Slack (d) | Last Bar Age (d) | next_url | Verdict |
|---|---|---|---|---|---|---|---|---|
| `spy_weekly_10y` | SPY | 262 | 2021-04-04 | 2026-04-05 | 1825 | 4 | no | ❌ |
| `spy_daily_5y` | SPY | 1255 | 2021-04-12 | 2026-04-09 | 8 | 0 | no | ✅ |
| `tsla_4h_3y` | TSLA | 217 | 2023-04-04 | 2023-06-22 | 0 | 1022 | yes | ❌ |
| `tsla_1h_2y` | TSLA | 904 | 2024-04-03 | 2024-06-24 | 0 | 654 | yes | ❌ |
| `tsla_15m_1y` | TSLA | 3358 | 2025-04-03 | 2025-06-18 | 0 | 295 | yes | ❌ |

> *Wide probes pass iff first bar is within 60d of requested start AND last bar is within 10d of today. `next_url=yes` means Polygon paginated the response — the single page we got is not the full range.*

## Historical Depth — Narrow Probes (14-day window at far end)

| Probe | Ticker | Window | Bars | Verdict |
|---|---|---|---|---|
| `spy_daily_5y_narrow` | SPY | 2021-04-04..2021-04-18 | 5 | ✅ tier has this depth |
| `tsla_4h_3y_narrow` | TSLA | 2023-04-04..2023-04-18 | 40 | ✅ tier has this depth |
| `tsla_1h_2y_narrow` | TSLA | 2024-04-03..2024-04-17 | 176 | ✅ tier has this depth |
| `tsla_15m_1y_narrow` | TSLA | 2025-04-03..2025-04-17 | 704 | ✅ tier has this depth |

> *Narrow probes request a tiny window at the far end of the lookback to decouple tier limits from per-request pagination. Zero bars here means the Polygon tier does not expose data that far back.*

## Options

### `tsla_options_chain` — ✅

- 250 puts across 3 expirations; front month 2026-04-10 has 141 strikes; picked O:TSLA260410P00405000 @ strike 405
```json
{
  "contract_count": 250,
  "front_expiration": "2026-04-10",
  "picked_ticker": "O:TSLA260410P00405000",
  "picked_strike": 405
}
```

### `tsla_option_snapshot` — ✅

- snapshot ok for O:TSLA260410P00405000: iv=None delta=None bid=None ask=None day_volume=92
```json
{
  "option_ticker": "O:TSLA260410P00405000",
  "implied_volatility": null,
  "delta": null,
  "gamma": null,
  "theta": null,
  "vega": null,
  "bid": null,
  "ask": null,
  "day_volume": 92
}
```

## Rate Limit Observations

_No `X-RateLimit-*` or `Retry-After` headers returned by Polygon on any response. Polygon does not advertise quotas on every tier — monitor 429 counts instead._

## Per-Request Log

| # | Status | Path | Elapsed (ms) | Rate-Limit Headers |
|---|---|---|---|---|
| 1 | 200 | `/v2/aggs/ticker/SPY/range/1/week/2016-04-05/2026-04-10` | 182.6 | — |
| 2 | 403 | `/v2/aggs/ticker/SPY/range/1/week/2016-04-05/2016-04-19` | 36.8 | — |
| 3 | 200 | `/v2/aggs/ticker/SPY/range/1/day/2021-04-04/2026-04-10` | 129.8 | — |
| 4 | 200 | `/v2/aggs/ticker/SPY/range/1/day/2021-04-04/2021-04-18` | 44.3 | — |
| 5 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/2023-04-04/2026-04-10` | 705.2 | — |
| 6 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/2023-04-04/2023-04-18` | 160.9 | — |
| 7 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/2024-04-03/2026-04-10` | 795.2 | — |
| 8 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/2024-04-03/2024-04-17` | 206.7 | — |
| 9 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/2025-04-03/2026-04-10` | 809.1 | — |
| 10 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/2025-04-03/2025-04-17` | 188.3 | — |
| 11 | 200 | `/v3/reference/options/contracts` | 52.5 | — |
| 12 | 200 | `/v3/snapshot/options/TSLA/O:TSLA260410P00405000` | 91.8 | — |

## Errors

### `spy_weekly_10y_narrow`

```
403 on /v2/aggs/ticker/SPY/range/1/week/2016-04-05/2016-04-19: {"status":"NOT_AUTHORIZED","request_id":"3c256f10ba3c9f41b5cad798215c3032","message":"Your plan doesn't include this data timeframe. Please upgrade your plan at https://polygon.io/pricing"}
```

---
_Generated by `scripts/validate_polygon.py`. Re-run anytime; the report is overwritten._
