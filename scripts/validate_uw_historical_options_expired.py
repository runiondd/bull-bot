"""
Phase 0b extension — probe /historic on ALREADY-EXPIRED SPY contracts.

The Phase 0b main probe revealed:
  * /option-chains?date=X  — gated to last 7 trading days
  * /option-contract/{id}/historic — returned 55 days of data for a CURRENTLY-
    LIVE contract (SPY260417P00666000), reaching back to 2026-01-21

Critical question: does /historic also work for contracts that have already
expired? If yes, we can backtest by algorithmically constructing symbols for
past expiries and fetching their histories directly, bypassing the gated
chain-discovery endpoint entirely.

Strategy: attempt /historic on three SPY put contracts with past expiries
(1 month ago, 6 months ago, ~2 years ago). For each, report: status code,
row count, date range, population of nbbo_bid/nbbo_ask/IV.

Contracts tested (symbols constructed from the OSI regex):
  * SPY260320P00570000   (expired ~3 weeks ago; just outside the 7d gate)
  * SPY251219P00570000   (expired ~4 months ago)
  * SPY240621P00540000   (expired ~22 months ago — the original Phase 0b target)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import UNUSUAL_WHALES_API_KEY  # noqa: E402

BASE_URL = "https://api.unusualwhales.com"

CONTRACTS = [
    ("SPY260320P00570000", "~3 weeks expired (just outside 7d chain-discovery gate)"),
    ("SPY251219P00570000", "~4 months expired"),
    ("SPY240621P00540000", "~22 months expired (original Phase 0b target)"),
]


def field_pop(rows: list[dict[str, Any]], field: str) -> int:
    return sum(
        1
        for r in rows
        if r.get(field) is not None
        and not (isinstance(r.get(field), str) and r[field].strip() in ("", "null"))
    )


def probe(session: requests.Session, contract_id: str) -> dict[str, Any]:
    path = f"/api/option-contract/{contract_id}/historic"
    r = session.get(f"{BASE_URL}{path}", timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = {"_non_json": r.text[:300]}

    result: dict[str, Any] = {
        "contract_id": contract_id,
        "status": r.status_code,
        "bytes": len(r.content or b""),
    }

    if r.status_code != 200:
        result["error_body"] = str(body)[:400]
        return result

    rows: list[dict[str, Any]] = []
    if isinstance(body, dict):
        data = body.get("data") or body.get("chains") or []
        if isinstance(data, list):
            rows = [row for row in data if isinstance(row, dict)]

    result["row_count"] = len(rows)
    if rows:
        dates = sorted([str(row.get("date", "")) for row in rows if row.get("date")])
        result["first_date"] = dates[0] if dates else None
        result["last_date"] = dates[-1] if dates else None
        result["populated"] = {
            "nbbo_bid": field_pop(rows, "nbbo_bid"),
            "nbbo_ask": field_pop(rows, "nbbo_ask"),
            "implied_volatility": field_pop(rows, "implied_volatility"),
            "open_price": field_pop(rows, "open_price"),
            "last_price": field_pop(rows, "last_price"),
            "volume": field_pop(rows, "volume"),
            "open_interest": field_pop(rows, "open_interest"),
        }
        result["sample_row"] = rows[0]
        result["field_names"] = sorted(rows[0].keys())
    return result


def main() -> int:
    if not UNUSUAL_WHALES_API_KEY:
        print("UNUSUAL_WHALES_API_KEY is empty", file=sys.stderr)
        return 2

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {UNUSUAL_WHALES_API_KEY}",
            "Accept": "application/json",
            "User-Agent": "bull-bot/phase0b-expired-probe",
        }
    )

    results = []
    for contract_id, label in CONTRACTS:
        print(f"\n=== {contract_id} — {label} ===", flush=True)
        res = probe(session, contract_id)
        res["label"] = label
        results.append(res)
        print(json.dumps(res, indent=2, default=str)[:2000], flush=True)

    # Summary
    print("\n=== SUMMARY ===", flush=True)
    for res in results:
        status = res.get("status")
        rows = res.get("row_count", 0)
        if status == 200 and rows > 0:
            print(
                f"PASS {res['contract_id']}  "
                f"{rows} rows  {res.get('first_date')}..{res.get('last_date')}  "
                f"bid/ask/iv populated={res.get('populated', {}).get('nbbo_bid', 0)}/"
                f"{res.get('populated', {}).get('nbbo_ask', 0)}/"
                f"{res.get('populated', {}).get('implied_volatility', 0)}"
            )
        else:
            print(f"FAIL {res['contract_id']}  status={status}  rows={rows}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
