# Phase 0 — Polygon API Validation

**Generated:** 2026-05-12 10:12:22Z  
**Overall:** ❌ FAIL  
**Client rate limit:** 5.0 req/sec (token bucket)  
**Requests issued:** 38  

## Probe Summary

| Probe | Result | Detail |
|---|---|---|
| `spy_weekly_10y` | ❌ | 262 bars, first=2021-05-09..last=2026-05-10, slack=1828d, age=2d, next_url=False [depth FAIL (slack=1828d)] |
| `spy_weekly_10y_narrow` | ❌ | request failed |
| `spy_daily_5y` | ✅ | 1254 bars, first=2021-05-13..last=2026-05-11, slack=7d, age=1d, next_url=False [OK] |
| `spy_daily_5y_narrow` | ✅ | 6 bars in far-end 14-day window 2021-05-06..2021-05-20 (first=2021-05-13, last=2021-05-20) — tier DOES support this depth |
| `tsla_4h_3y` | ✅ | 3270 bars, first=2023-05-08..last=2026-05-12, slack=2d, age=0d, next_url=False [OK] |
| `tsla_4h_3y_narrow` | ✅ | 40 bars in far-end 14-day window 2023-05-06..2023-05-20 (first=2023-05-08, last=2023-05-19) — tier DOES support this depth |
| `tsla_1h_2y` | ✅ | 8052 bars, first=2024-05-06..last=2026-05-12, slack=1d, age=0d, next_url=False [OK] |
| `tsla_1h_2y_narrow` | ✅ | 160 bars in far-end 14-day window 2024-05-05..2024-05-19 (first=2024-05-06, last=2024-05-17) — tier DOES support this depth |
| `tsla_15m_1y` | ✅ | 16323 bars, first=2025-05-05..last=2026-05-12, slack=0d, age=0d, next_url=False [OK] |
| `tsla_15m_1y_narrow` | ✅ | 704 bars in far-end 14-day window 2025-05-05..2025-05-19 (first=2025-05-05, last=2025-05-19) — tier DOES support this depth |
| `tsla_options_chain` | ✅ | 250 puts across 2 expirations; front month 2026-05-13 has 84 strikes; picked O:TSLA260513P00400000 @ strike 400 |
| `tsla_option_snapshot` | ✅ | snapshot ok for O:TSLA260513P00400000: iv=0.7471369250866753 delta=-0.02210276362023465 bid=None ask=None day_volume=5169 |

## Historical Depth — Wide Probes

| Probe | Ticker | Bars | First Bar | Last Bar | Slack (d) | Last Bar Age (d) | next_url | Verdict |
|---|---|---|---|---|---|---|---|---|
| `spy_weekly_10y` | SPY | 262 | 2021-05-09 | 2026-05-10 | 1828 | 2 | no | ❌ |
| `spy_daily_5y` | SPY | 1254 | 2021-05-13 | 2026-05-11 | 7 | 1 | no | ✅ |
| `tsla_4h_3y` | TSLA | 3270 | 2023-05-08 | 2026-05-12 | 2 | 0 | no | ✅ |
| `tsla_1h_2y` | TSLA | 8052 | 2024-05-06 | 2026-05-12 | 1 | 0 | no | ✅ |
| `tsla_15m_1y` | TSLA | 16323 | 2025-05-05 | 2026-05-12 | 0 | 0 | no | ✅ |

> *Wide probes pass iff first bar is within 60d of requested start AND last bar is within 10d of today. `next_url=yes` means Polygon paginated the response — the single page we got is not the full range.*

## Historical Depth — Narrow Probes (14-day window at far end)

| Probe | Ticker | Window | Bars | Verdict |
|---|---|---|---|---|
| `spy_daily_5y_narrow` | SPY | 2021-05-06..2021-05-20 | 6 | ✅ tier has this depth |
| `tsla_4h_3y_narrow` | TSLA | 2023-05-06..2023-05-20 | 40 | ✅ tier has this depth |
| `tsla_1h_2y_narrow` | TSLA | 2024-05-05..2024-05-19 | 160 | ✅ tier has this depth |
| `tsla_15m_1y_narrow` | TSLA | 2025-05-05..2025-05-19 | 704 | ✅ tier has this depth |

> *Narrow probes request a tiny window at the far end of the lookback to decouple tier limits from per-request pagination. Zero bars here means the Polygon tier does not expose data that far back.*

## Options

### `tsla_options_chain` — ✅

- 250 puts across 2 expirations; front month 2026-05-13 has 84 strikes; picked O:TSLA260513P00400000 @ strike 400
```json
{
  "contract_count": 250,
  "front_expiration": "2026-05-13",
  "picked_ticker": "O:TSLA260513P00400000",
  "picked_strike": 400
}
```

### `tsla_option_snapshot` — ✅

- snapshot ok for O:TSLA260513P00400000: iv=0.7471369250866753 delta=-0.02210276362023465 bid=None ask=None day_volume=5169
```json
{
  "option_ticker": "O:TSLA260513P00400000",
  "implied_volatility": 0.7471369250866753,
  "delta": -0.02210276362023465,
  "gamma": 0.0024383299467777343,
  "theta": -0.36083267971783356,
  "vega": 0.013992659818159918,
  "bid": null,
  "ask": null,
  "day_volume": 5169
}
```

## Rate Limit Observations

_No `X-RateLimit-*` or `Retry-After` headers returned by Polygon on any response. Polygon does not advertise quotas on every tier — monitor 429 counts instead._

## Per-Request Log

| # | Status | Path | Elapsed (ms) | Rate-Limit Headers |
|---|---|---|---|---|
| 1 | 200 | `/v2/aggs/ticker/SPY/range/1/week/2016-05-07/2026-05-12` | 295.4 | — |
| 2 | 403 | `/v2/aggs/ticker/SPY/range/1/week/2016-05-07/2016-05-21` | 51.3 | — |
| 3 | 200 | `/v2/aggs/ticker/SPY/range/1/day/2021-05-06/2026-05-12` | 140.4 | — |
| 4 | 200 | `/v2/aggs/ticker/SPY/range/1/day/2021-05-06/2021-05-20` | 59.6 | — |
| 5 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/2023-05-06/2026-05-12` | 627.6 | — |
| 6 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1690329600000/2026-05-12` | 701.3 | — |
| 7 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1697054400000/2026-05-12` | 797.3 | — |
| 8 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1704196800000/2026-05-12` | 607.7 | — |
| 9 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1710979200000/2026-05-12` | 745.1 | — |
| 10 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1718107200000/2026-05-12` | 703.4 | — |
| 11 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1724932800000/2026-05-12` | 918.9 | — |
| 12 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1731600000000/2026-05-12` | 880.4 | — |
| 13 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1738756800000/2026-05-12` | 762.0 | — |
| 14 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1745352000000/2026-05-12` | 701.2 | — |
| 15 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1752076800000/2026-05-12` | 825.2 | — |
| 16 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1758729600000/2026-05-12` | 1113.1 | — |
| 17 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1765324800000/2026-05-12` | 823.9 | — |
| 18 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1772222400000/2026-05-12` | 742.3 | — |
| 19 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/2023-05-06/2023-05-20` | 185.7 | — |
| 20 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/2024-05-05/2026-05-12` | 1000.7 | — |
| 21 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1722013200000/2026-05-12` | 851.5 | — |
| 22 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1728925200000/2026-05-12` | 932.3 | — |
| 23 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1735668000000/2026-05-12` | 784.9 | — |
| 24 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1742490000000/2026-05-12` | 872.4 | — |
| 25 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1749081600000/2026-05-12` | 901.7 | — |
| 26 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1755806400000/2026-05-12` | 687.6 | — |
| 27 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1762455600000/2026-05-12` | 830.1 | — |
| 28 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1769518800000/2026-05-12` | 764.8 | — |
| 29 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1776182400000/2026-05-12` | 482.9 | — |
| 30 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/2024-05-05/2024-05-19` | 236.7 | — |
| 31 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/2025-05-05/2026-05-12` | 1016.1 | — |
| 32 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/1753142400000/2026-05-12` | 952.4 | — |
| 33 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/1759837500000/2026-05-12` | 1302.6 | — |
| 34 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/1766432700000/2026-05-12` | 838.8 | — |
| 35 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/1773331200000/2026-05-12` | 668.7 | — |
| 36 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/2025-05-05/2025-05-19` | 226.6 | — |
| 37 | 200 | `/v3/reference/options/contracts` | 77.8 | — |
| 38 | 200 | `/v3/snapshot/options/TSLA/O:TSLA260513P00400000` | 58.9 | — |

## Errors

### `spy_weekly_10y_narrow`

```
403 on /v2/aggs/ticker/SPY/range/1/week/2016-05-07/2016-05-21: {"status":"NOT_AUTHORIZED","request_id":"a87701216757362b8f72fcb2ae4e1d07","message":"Your plan doesn't include this data timeframe. Please upgrade your plan at https://polygon.io/pricing"}
```

---
_Generated by `scripts/validate_polygon.py`. Re-run anytime; the report is overwritten._
