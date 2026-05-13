# Phase 0 — Polygon API Validation

**Generated:** 2026-05-13 14:33:21Z  
**Overall:** ✅ PASS  
**Client rate limit:** 5.0 req/sec (token bucket)  
**Requests issued:** 38  

## Probe Summary

| Probe | Result | Detail |
|---|---|---|
| `spy_weekly_5y` | ✅ | 262 bars, first=2021-05-09..last=2026-05-10, slack=2d, age=3d, next_url=False [OK] |
| `spy_weekly_5y_narrow` | ✅ | 2 bars in far-end 14-day window 2021-05-07..2021-05-21 (first=2021-05-09, last=2021-05-16) — tier DOES support this depth |
| `spy_daily_5y` | ✅ | 1255 bars, first=2021-05-14..last=2026-05-13, slack=7d, age=0d, next_url=False [OK] |
| `spy_daily_5y_narrow` | ✅ | 6 bars in far-end 14-day window 2021-05-07..2021-05-21 (first=2021-05-14, last=2021-05-21) — tier DOES support this depth |
| `tsla_4h_3y` | ✅ | 3275 bars, first=2023-05-08..last=2026-05-13, slack=1d, age=0d, next_url=False [OK] |
| `tsla_4h_3y_narrow` | ✅ | 40 bars in far-end 14-day window 2023-05-07..2023-05-21 (first=2023-05-08, last=2023-05-19) — tier DOES support this depth |
| `tsla_1h_2y` | ✅ | 8073 bars, first=2024-05-06..last=2026-05-13, slack=0d, age=0d, next_url=False [OK] |
| `tsla_1h_2y_narrow` | ✅ | 176 bars in far-end 14-day window 2024-05-06..2024-05-20 (first=2024-05-06, last=2024-05-20) — tier DOES support this depth |
| `tsla_15m_1y` | ✅ | 16341 bars, first=2025-05-06..last=2026-05-13, slack=0d, age=0d, next_url=False [OK] |
| `tsla_15m_1y_narrow` | ✅ | 704 bars in far-end 14-day window 2025-05-06..2025-05-20 (first=2025-05-06, last=2025-05-20) — tier DOES support this depth |
| `tsla_options_chain` | ✅ | 250 puts across 2 expirations; front month 2026-05-13 has 119 strikes; picked O:TSLA260513P00442500 @ strike 442.5 |
| `tsla_option_snapshot` | ✅ | snapshot ok for O:TSLA260513P00442500: iv=0.6877817270768248 delta=-0.6544556896752068 bid=None ask=None day_volume=1198 |

## Historical Depth — Wide Probes

| Probe | Ticker | Bars | First Bar | Last Bar | Slack (d) | Last Bar Age (d) | next_url | Verdict |
|---|---|---|---|---|---|---|---|---|
| `spy_weekly_5y` | SPY | 262 | 2021-05-09 | 2026-05-10 | 2 | 3 | no | ✅ |
| `spy_daily_5y` | SPY | 1255 | 2021-05-14 | 2026-05-13 | 7 | 0 | no | ✅ |
| `tsla_4h_3y` | TSLA | 3275 | 2023-05-08 | 2026-05-13 | 1 | 0 | no | ✅ |
| `tsla_1h_2y` | TSLA | 8073 | 2024-05-06 | 2026-05-13 | 0 | 0 | no | ✅ |
| `tsla_15m_1y` | TSLA | 16341 | 2025-05-06 | 2026-05-13 | 0 | 0 | no | ✅ |

> *Wide probes pass iff first bar is within 60d of requested start AND last bar is within 10d of today. `next_url=yes` means Polygon paginated the response — the single page we got is not the full range.*

## Historical Depth — Narrow Probes (14-day window at far end)

| Probe | Ticker | Window | Bars | Verdict |
|---|---|---|---|---|
| `spy_weekly_5y_narrow` | SPY | 2021-05-07..2021-05-21 | 2 | ✅ tier has this depth |
| `spy_daily_5y_narrow` | SPY | 2021-05-07..2021-05-21 | 6 | ✅ tier has this depth |
| `tsla_4h_3y_narrow` | TSLA | 2023-05-07..2023-05-21 | 40 | ✅ tier has this depth |
| `tsla_1h_2y_narrow` | TSLA | 2024-05-06..2024-05-20 | 176 | ✅ tier has this depth |
| `tsla_15m_1y_narrow` | TSLA | 2025-05-06..2025-05-20 | 704 | ✅ tier has this depth |

> *Narrow probes request a tiny window at the far end of the lookback to decouple tier limits from per-request pagination. Zero bars here means the Polygon tier does not expose data that far back.*

## Options

### `tsla_options_chain` — ✅

- 250 puts across 2 expirations; front month 2026-05-13 has 119 strikes; picked O:TSLA260513P00442500 @ strike 442.5
```json
{
  "contract_count": 250,
  "front_expiration": "2026-05-13",
  "picked_ticker": "O:TSLA260513P00442500",
  "picked_strike": 442.5
}
```

### `tsla_option_snapshot` — ✅

- snapshot ok for O:TSLA260513P00442500: iv=0.6877817270768248 delta=-0.6544556896752068 bid=None ask=None day_volume=1198
```json
{
  "option_ticker": "O:TSLA260513P00442500",
  "implied_volatility": 0.6877817270768248,
  "delta": -0.6544556896752068,
  "gamma": 0.03795933485649646,
  "theta": -4.699773258708006,
  "vega": 0.050096058727969864,
  "bid": null,
  "ask": null,
  "day_volume": 1198
}
```

## Rate Limit Observations

_No `X-RateLimit-*` or `Retry-After` headers returned by Polygon on any response. Polygon does not advertise quotas on every tier — monitor 429 counts instead._

## Per-Request Log

| # | Status | Path | Elapsed (ms) | Rate-Limit Headers |
|---|---|---|---|---|
| 1 | 200 | `/v2/aggs/ticker/SPY/range/1/week/2021-05-07/2026-05-13` | 339.3 | — |
| 2 | 200 | `/v2/aggs/ticker/SPY/range/1/week/2021-05-07/2021-05-21` | 225.9 | — |
| 3 | 200 | `/v2/aggs/ticker/SPY/range/1/day/2021-05-07/2026-05-13` | 181.3 | — |
| 4 | 200 | `/v2/aggs/ticker/SPY/range/1/day/2021-05-07/2021-05-21` | 72.1 | — |
| 5 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/2023-05-07/2026-05-13` | 1197.9 | — |
| 6 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1690329600000/2026-05-13` | 959.5 | — |
| 7 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1697054400000/2026-05-13` | 1797.6 | — |
| 8 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1704196800000/2026-05-13` | 1140.0 | — |
| 9 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1710979200000/2026-05-13` | 1207.6 | — |
| 10 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1718107200000/2026-05-13` | 1066.9 | — |
| 11 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1724932800000/2026-05-13` | 1296.5 | — |
| 12 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1731600000000/2026-05-13` | 900.4 | — |
| 13 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1738756800000/2026-05-13` | 1621.3 | — |
| 14 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1745352000000/2026-05-13` | 807.5 | — |
| 15 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1752076800000/2026-05-13` | 1032.2 | — |
| 16 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1758729600000/2026-05-13` | 1213.5 | — |
| 17 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1765324800000/2026-05-13` | 1261.4 | — |
| 18 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/1772222400000/2026-05-13` | 1569.9 | — |
| 19 | 200 | `/v2/aggs/ticker/TSLA/range/4/hour/2023-05-07/2023-05-21` | 241.1 | — |
| 20 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/2024-05-06/2026-05-13` | 1104.0 | — |
| 21 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1722013200000/2026-05-13` | 3555.3 | — |
| 22 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1728925200000/2026-05-13` | 1202.9 | — |
| 23 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1735668000000/2026-05-13` | 1385.2 | — |
| 24 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1742490000000/2026-05-13` | 915.0 | — |
| 25 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1749081600000/2026-05-13` | 1361.8 | — |
| 26 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1755806400000/2026-05-13` | 1142.3 | — |
| 27 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1762455600000/2026-05-13` | 1194.3 | — |
| 28 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1769518800000/2026-05-13` | 1362.3 | — |
| 29 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/1776182400000/2026-05-13` | 664.5 | — |
| 30 | 200 | `/v2/aggs/ticker/TSLA/range/1/hour/2024-05-06/2024-05-20` | 244.1 | — |
| 31 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/2025-05-06/2026-05-13` | 1225.2 | — |
| 32 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/1753228800000/2026-05-13` | 1478.1 | — |
| 33 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/1759923900000/2026-05-13` | 1292.2 | — |
| 34 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/1766518200000/2026-05-13` | 1396.7 | — |
| 35 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/1773417600000/2026-05-13` | 1042.8 | — |
| 36 | 200 | `/v2/aggs/ticker/TSLA/range/15/minute/2025-05-06/2025-05-20` | 478.0 | — |
| 37 | 200 | `/v3/reference/options/contracts` | 149.0 | — |
| 38 | 200 | `/v3/snapshot/options/TSLA/O:TSLA260513P00442500` | 99.3 | — |

---
_Generated by `scripts/validate_polygon.py`. Re-run anytime; the report is overwritten._
