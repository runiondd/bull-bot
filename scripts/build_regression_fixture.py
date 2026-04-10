"""
One-shot: build the Tier 3 regression fixture from UW data.

Run once at Stage 1 kickoff. Produces:
    tests/fixtures/spy_regression_2023_2024.json

Contains: 252 daily SPY bars (2023-01-01 to 2023-12-31) + all option
contracts within ±10% of SPY spot for the same period, one entry per
unique (expiry, strike, kind). The committed fixture is the source of
truth — never regenerate casually, every commit to this file must be
reviewed because it shifts the golden values in the regression test.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bullbot import config
from bullbot.data import fetchers, options_backfill
from bullbot.data.fetchers import UWHttpClient


FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "spy_regression_2023_2024.json"


def build() -> None:
    client = UWHttpClient(api_key=config.UW_API_KEY)

    # Fetch daily SPY bars
    bars = fetchers.fetch_daily_ohlc(client, "SPY", limit=2500)
    bars_2023 = [b for b in bars if b.ts >= 1672531200 and b.ts < 1704067200]

    # Build symbol universe for 2023 and fetch historic series
    spot_estimate = 440.0  # SPY average 2023
    symbols = options_backfill.build_candidate_symbols(
        ticker="SPY",
        spot=spot_estimate,
        backfill_start=date(2023, 1, 1),
        backfill_end=date(2023, 12, 31),
        strike_range_fraction=0.10,
        strike_step=5.0,
    )
    contracts_by_symbol: dict[str, list] = {}
    for sym in symbols[:500]:  # cap to keep fixture size manageable
        rows = fetchers.fetch_option_historic(client, sym)
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
        "bars": [
            {
                "ticker": b.ticker, "timeframe": b.timeframe, "ts": b.ts,
                "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                "volume": b.volume, "source": b.source,
            }
            for b in bars_2023
        ],
        "contracts": contracts_by_symbol,
    }
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(out, indent=2))
    print(f"wrote {FIXTURE_PATH} with {len(bars_2023)} bars and {sum(len(v) for v in contracts_by_symbol.values())} contract rows")


if __name__ == "__main__":
    build()
