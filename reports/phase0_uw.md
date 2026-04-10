# Phase 0 — Unusual Whales API Validation

**Generated:** 2026-04-10 01:29:54Z  
**Overall:** ❌ PARTIAL / FAIL  
**Client rate limit:** 2.0 req/sec (token bucket)  
**Requests issued:** 15  

## Probe Summary

| Probe | Result | Detail |
|---|---|---|
| `auth` | ✅ | stock-state ok; fields: ['close', 'high', 'low', 'open', 'volume', 'total_volume', 'market_time', 'tape_time', 'prev_close'] |
| `spy_daily_10y` | ✅ | 4572 bars, range=2016-04-11..2026-04-09, span=9.99y, last_bar_age=1d [OK] |
| `tsla_4h_3y` | ❌ | 2500 bars, range=2024-07-25..2026-04-09, span=1.71y, last_bar_age=0d [depth FAIL (got 1.71y, need 2.8y)] |
| `tsla_1h_2y` | ❌ | 2500 bars, range=2025-08-25..2026-04-09, span=0.62y, last_bar_age=0d [depth FAIL (got 0.62y, need 1.4y)] |
| `tsla_15m_1y` | ❌ | 2500 bars, range=2026-02-12..2026-04-09, span=0.15y, last_bar_age=0d [depth FAIL (got 0.15y, need 0.4y)] |
| `spy_daily_10y_narrow` | ✅ | got 22 bars at end_date=2016-04-05; newest bar 2016-04-06 (0d from anchor) — tier DOES support this depth |
| `tsla_4h_3y_narrow` | ✅ | got 10 bars at end_date=2023-04-04; newest bar 2023-04-04 (0d from anchor) — tier DOES support this depth |
| `tsla_1h_2y_narrow` | ✅ | got 10 bars at end_date=2024-04-03; newest bar 2024-04-03 (0d from anchor) — tier DOES support this depth |
| `tsla_15m_1y_narrow` | ✅ | got 10 bars at end_date=2025-04-03; newest bar 2025-04-03 (0d from anchor) — tier DOES support this depth |
| `tsla_option_chains` | ✅ | returned 5734 option symbols (not full contract objects); sample=['TSLA260529P00355000', 'TSLA260417C00230000', 'TSLA260417C00150000']. No IV/bid/ask at this endpoint — must use atm-chains or option-contract endpoints for quotes. |
| `tsla_greeks` | ✅ | expiry=2026-05-29, 29 strikes; populated 10/10 greek fields in first strike |
| `tsla_atm_chains` | ✅ | 2 ATM contracts; IV=✓ bid/ask=✓ |
| `spy_gex` | ✅ | 249 items; first-item keys=['date', 'call_delta', 'put_delta', 'call_charm', 'call_vanna', 'put_charm', 'put_vanna', 'call_gamma', 'put_gamma'] |
| `spy_flow_alerts` | ✅ | 100 items; first-item keys=['type', 'ticker', 'created_at', 'price', 'volume', 'open_interest', 'expiry', 'strike', 'underlying_price', 'total_premium', 'trade_count', 'iv_end'] |
| `spy_iv_rank` | ✅ | 4 items; first-item keys=['close', 'date', 'updated_at', 'volatility', 'iv_rank_1y'] |

## Polygon Decisions — Resolution

### Decision 1 — Weekly SPY depth (need ≥10y)

_UW's OHLC endpoint does not support `1w` natively (despite the OpenAPI enum). We test 10y daily SPY instead and resample to weekly in code._

- **UW serves 10y daily SPY.** Wide: 4572 bars, range=2016-04-11..2026-04-09, span=9.99y, last_bar_age=1d [OK]. Narrow: got 22 bars at end_date=2016-04-05; newest bar 2016-04-06 (0d from anchor) — tier DOES support this depth.
- **→ No Polygon stocks upgrade needed.** Source daily SPY from UW, resample to weekly in `clients/uw_client.py` or `data/`.

### Decision 2 — Options analytics (greeks + IV + bid/ask)
- **UW covers options analytics via:**
  - `option-chains` (returned 5734 option symbols (not full contract objects); sample=['TSLA260529P00355000', 'TSLA260417C00230000', 'TSLA260417C00150000']. No IV/bid/ask at this endpoint — must use atm-chains or option-contract endpoints for quotes.)
  - `atm-chains` (2 ATM contracts; IV=✓ bid/ask=✓)
  - `greeks` (expiry=2026-05-29, 29 strikes; populated 10/10 greek fields in first strike)
- **No Polygon options upgrade needed.** Bull-Bot sources greeks/IV/quotes from UW.

## Historical OHLC Depth

| Probe | Ticker | Candle | Bars | Oldest | Newest | Span (y) | Last Bar Age (d) | Verdict |
|---|---|---|---|---|---|---|---|---|
| `spy_daily_10y` | SPY | 1d | 4572 | 2016-04-11 | 2026-04-09 | 9.99 | 1 | ✅ |
| `tsla_4h_3y` | TSLA | 4h | 2500 | 2024-07-25 | 2026-04-09 | 1.71 | 0 | ❌ |
| `tsla_1h_2y` | TSLA | 1h | 2500 | 2025-08-25 | 2026-04-09 | 0.62 | 0 | ❌ |
| `tsla_15m_1y` | TSLA | 15m | 2500 | 2026-02-12 | 2026-04-09 | 0.15 | 0 | ❌ |
| `spy_daily_10y_narrow` | SPY | 1d | 22 |  |  | None | None | ✅ |
| `tsla_4h_3y_narrow` | TSLA | 4h | 10 |  |  | None | None | ✅ |
| `tsla_1h_2y_narrow` | TSLA | 1h | 10 |  |  | None | None | ✅ |
| `tsla_15m_1y_narrow` | TSLA | 15m | 10 |  |  | None | None | ✅ |

## Options Probe Detail

### `tsla_option_chains` — ✅

- returned 5734 option symbols (not full contract objects); sample=['TSLA260529P00355000', 'TSLA260417C00230000', 'TSLA260417C00150000']. No IV/bid/ask at this endpoint — must use atm-chains or option-contract endpoints for quotes.
```json
{
  "count": 5734,
  "response_type": "symbol_list",
  "sample_symbols": [
    "TSLA260529P00355000",
    "TSLA260417C00230000",
    "TSLA260417C00150000",
    "TSLA261120C00425000",
    "TSLA260417C00305000"
  ],
  "extracted_expiry": "2026-05-29"
}
```

### `tsla_atm_chains` — ✅

- 2 ATM contracts; IV=✓ bid/ask=✓
```json
{
  "expiry": "2026-05-29",
  "count": 2,
  "first_contract_keys": [
    "close",
    "high",
    "low",
    "open",
    "date",
    "iv",
    "ask",
    "bid",
    "volume",
    "sector",
    "open_interest",
    "option_symbol",
    "trades",
    "premium",
    "tape_time",
    "mid_volume",
    "avg_price",
    "next_earnings_date",
    "cross_volume",
    "floor_volume",
    "neutral_volume",
    "stock_multi_leg_volume",
    "sweep_volume",
    "total_ask_changes",
    "total_bid_changes",
    "stock_price",
    "er_time",
    "ticker_vol",
    "ask_side_volume",
    "bid_side_volume",
    "multileg_volume",
    "chain_prev_close"
  ],
  "sample_values": {
    "implied_volatility": "0.486092302478374",
    "nbbo_bid": "22.85",
    "nbbo_ask": "27.00",
    "open_interest": 0,
    "volume": 86
  }
}
```

### `tsla_greeks` — ✅

- expiry=2026-05-29, 29 strikes; populated 10/10 greek fields in first strike
```json
{
  "expiry": "2026-05-29",
  "strike_count": 29,
  "first_strike_keys": [
    "date",
    "expiry",
    "strike",
    "call_delta",
    "put_delta",
    "call_charm",
    "call_vanna",
    "put_charm",
    "put_vanna",
    "call_gamma",
    "put_gamma",
    "put_volatility",
    "put_vega",
    "put_theta",
    "put_rho",
    "call_volatility",
    "call_vega",
    "call_theta",
    "call_rho",
    "call_option_symbol",
    "put_option_symbol"
  ],
  "first_strike_sample": {
    "date": "2026-04-09",
    "expiry": "2026-05-29",
    "strike": "275",
    "call_delta": "0.877410404930717",
    "put_delta": "-0.1022364132839988",
    "call_charm": "-0.01593494309437054",
    "call_vanna": "-0.01469238101918298",
    "put_charm": "-0.01060061277711647",
    "put_vanna": "-0.0623805561271241",
    "call_gamma": "0.00270988725273213",
    "put_gamma": "0.00264581814258394",
    "put_volatility": "0.527002227270021",
    "put_vega": "0.228126084303868",
    "put_theta": "-0.1201423936199705",
    "put_rho": "-0.0532837564502357",
    "call_volatility": "0.585849289877525",
    "call_vega": "0.259740477821986",
    "call_theta": "-0.1520668068155533",
    "call_rho": "0.311825229627157",
    "call_option_symbol": "TSLA260529C00275000",
    "put_option_symbol": "TSLA260529P00275000"
  },
  "greek_population": {
    "call_delta": true,
    "call_gamma": true,
    "call_theta": true,
    "call_vega": true,
    "put_delta": true,
    "put_gamma": true,
    "put_theta": true,
    "put_vega": true,
    "call_volatility": true,
    "put_volatility": true
  }
}
```

## Rate Limit Observations

| Header | Last Value |
|---|---|
| `x-request-id` | `273af448aadb4959658ce2167d38e698` |

## Per-Request Log

| # | Status | Path | Bytes | Elapsed (ms) | Headers |
|---|---|---|---|---|---|
| 1 | 200 | `/api/stock/SPY/stock-state` | 198 | 124.5 | x-request-id=c8b0267d49621d7b81e9ee5b1967b657 |
| 2 | 200 | `/api/stock/SPY/ohlc/1d` | 667027 | 271.4 | x-request-id=a3ae368d4d941f00b10598b10a90c573 |
| 3 | 200 | `/api/stock/TSLA/ohlc/4h` | 490872 | 277.5 | x-request-id=12701ac4dc0116e40b515ce0666c5a18 |
| 4 | 200 | `/api/stock/TSLA/ohlc/1h` | 487289 | 247.9 | x-request-id=24a5dd934a29607d7437ec43d0538cae |
| 5 | 200 | `/api/stock/TSLA/ohlc/15m` | 485029 | 209.9 | x-request-id=71d304cb41f55def1da8eaf8862898cd |
| 6 | 200 | `/api/stock/SPY/ohlc/1d` | 3235 | 61.3 | x-request-id=3c334ad18c40cd7aee4d3216365d895b |
| 7 | 200 | `/api/stock/TSLA/ohlc/4h` | 2050 | 75.9 | x-request-id=c25a267e9de290188caa2fb1636762b9 |
| 8 | 200 | `/api/stock/TSLA/ohlc/1h` | 1977 | 73.6 | x-request-id=c490008c7c291a12ef93ba78616df86d |
| 9 | 200 | `/api/stock/TSLA/ohlc/15m` | 1973 | 80.8 | x-request-id=2020043d561bd84985391954edad7c37 |
| 10 | 200 | `/api/stock/TSLA/option-chains` | 126158 | 107.6 | x-request-id=0eaf5ce57bfe8ecec718246143b6bb23 |
| 11 | 200 | `/api/stock/TSLA/greeks` | 19884 | 66.6 | x-request-id=48e76f2dd66ac705c868295925069144 |
| 12 | 200 | `/api/stock/TSLA/atm-chains` | 1397 | 81.1 | x-request-id=c10a0c74a96f805361a32a02a31227d3 |
| 13 | 200 | `/api/stock/SPY/greek-exposure` | 64053 | 93.9 | x-request-id=25cfbb3b2bd59a4cecc2f67aa0d0ff66 |
| 14 | 200 | `/api/stock/SPY/flow-alerts` | 59169 | 66.4 | x-request-id=0acbc84f759161a1b4c8ecaca04fa9e9 |
| 15 | 200 | `/api/stock/SPY/iv-rank` | 517 | 51.8 | x-request-id=273af448aadb4959658ce2167d38e698 |

---
_Generated by `scripts/validate_uw.py`. Re-run anytime; the report is overwritten._
