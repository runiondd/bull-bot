"""
One-shot: build the Tier 3 regression fixture from UW data.

Run once at Stage 1 kickoff. Produces:
    tests/fixtures/spy_regression.json

Contains: ~251 daily SPY bars (trailing 12 months from UW) + option
contracts within ±10% of SPY spot for the same period. The committed
fixture is the source of truth — never regenerate casually, every commit
to this file must be reviewed because it shifts the golden values in the
regression test.

Note: UW free tier only serves ~1 year of daily bars. The original plan
targeted 2023 data but that's not available. We use whatever trailing
year the API returns and freeze it.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bullbot import config
from bullbot.data import fetchers, options_backfill
from bullbot.data.fetchers import UWHttpClient


FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "spy_regression.json"


def build() -> None:
    client = UWHttpClient(api_key=config.UW_API_KEY)

    # Fetch all available daily SPY bars (UW returns ~1 year trailing)
    print("Fetching SPY daily bars...")
    all_bars = fetchers.fetch_daily_ohlc(client, "SPY", limit=2500)

    # Deduplicate to one bar per date (UW returns pr/r/po rows; keep regular hours 'r')
    # Use the bar with the highest volume per date as proxy for regular session
    by_date: dict[int, list] = {}
    for b in all_bars:
        by_date.setdefault(b.ts, []).append(b)
    bars = []
    for ts in sorted(by_date):
        group = by_date[ts]
        bars.append(max(group, key=lambda b: b.volume))

    print(f"  → {len(bars)} unique-date bars")
    if not bars:
        print("ERROR: no bars returned from UW API")
        sys.exit(1)

    min_date = datetime.fromtimestamp(bars[0].ts, tz=timezone.utc).date()
    max_date = datetime.fromtimestamp(bars[-1].ts, tz=timezone.utc).date()
    print(f"  → date range: {min_date} to {max_date}")

    # Use the midpoint spot for strike range estimation
    spot_estimate = sum(b.close for b in bars) / len(bars)
    print(f"  → average spot: ${spot_estimate:.2f}")

    # Build option symbol universe: pick every 3rd expiry (to span the
    # full year) but fetch ALL strikes at each expiry. This gives dense
    # strike coverage (needed for spreads) at representative dates.
    print("Building option symbol candidates...")
    all_expiries = options_backfill.enumerate_expiries(min_date, max_date)
    # Sample every 3rd expiry to spread across the year within API budget
    sampled_expiries = all_expiries[::3]
    strikes = options_backfill.enumerate_strikes_around_spot(
        spot_estimate, range_fraction=0.10, step=5.0
    )
    symbols = []
    for exp in sampled_expiries:
        for k in strikes:
            for kind in ("P", "C"):
                symbols.append(options_backfill.format_osi_symbol("SPY", exp, k, kind))
    max_symbols = min(len(symbols), 700)
    print(f"  → {len(symbols)} symbols ({len(sampled_expiries)} expiries × {len(strikes)} strikes × 2), fetching up to {max_symbols}")

    contracts_by_symbol: dict[str, list] = {}
    for i, sym in enumerate(symbols[:max_symbols]):
        if i > 0:
            time.sleep(0.25)  # stay under UW rate limit
        if i % 50 == 0:
            print(f"  fetching option contracts... {i}/{min(len(symbols), 500)}")
        try:
            rows = fetchers.fetch_option_historic(client, sym)
        except Exception as e:
            print(f"    WARN: {sym} failed ({e}), sleeping 30s and retrying...")
            time.sleep(30)
            try:
                rows = fetchers.fetch_option_historic(client, sym)
            except Exception:
                print(f"    SKIP: {sym}")
                continue
        if rows:
            contracts_by_symbol[sym] = [
                {
                    "ticker": c.ticker, "expiry": c.expiry, "strike": c.strike,
                    "kind": c.kind, "ts": c.ts,
                    "nbbo_bid": c.nbbo_bid, "nbbo_ask": c.nbbo_ask,
                    "last": c.last, "volume": c.volume,
                    "open_interest": c.open_interest, "iv": c.iv,
                }
                for c in rows
            ]

    out = {
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(),
            "bar_range": [min_date.isoformat(), max_date.isoformat()],
            "spot_estimate": round(spot_estimate, 2),
        },
        "bars": [
            {
                "ticker": b.ticker, "timeframe": b.timeframe, "ts": b.ts,
                "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                "volume": b.volume, "source": b.source,
            }
            for b in bars
        ],
        "contracts": contracts_by_symbol,
    }
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(out, indent=2))
    total_rows = sum(len(v) for v in contracts_by_symbol.values())
    print(f"wrote {FIXTURE_PATH} with {len(bars)} bars and {total_rows} contract rows across {len(contracts_by_symbol)} symbols")


if __name__ == "__main__":
    build()
