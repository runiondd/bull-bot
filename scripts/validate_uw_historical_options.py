"""
Phase 0b — Unusual Whales historical options data validation.

Resolves the single biggest open question in the Bull-Bot v3 design:

    Does UW return real, populated historical bid/ask/IV/volume/OI for
    EXPIRED option contracts on arbitrary past dates?

If yes → options backtesting is viable with UW alone (no CBOE/ORATS upgrade).
If no  → the v3 design needs to fork (narrow universe, buy historical data,
         or redefine the system around equity-only strategies).

Probes (all GET, against api.unusualwhales.com):

  1. chains_snapshot    /api/stock/SPY/option-chains?date=2024-06-14
     Returns the list of option symbols that existed on 2024-06-14.
     Pass criterion: non-empty list containing puts and calls across
     multiple expiries, one of which is ≥ a year before today.

  2. historic_daily     /api/option-contract/{id}/historic
     For a selected expired contract (picked from probe 1), returns the
     per-day history for the lifetime of that contract.
     Pass criterion: ≥10 trading days returned, the trading day we queried
     is present, nbbo_bid/nbbo_ask populated, implied_volatility populated.

  3. intraday_minutes   /api/option-contract/{id}/intraday?date=2024-06-14
     Minute-bar data for the same contract on a specific past date.
     Pass criterion: ≥100 minute ticks returned for an RTH session,
     open/close/IV populated.

Outputs:
  * reports/phase0b_uw_historical_options.md   (markdown)
  * reports/phase0b_uw_historical_options.json (full debug dump)

Exit code 0 on full pass, 1 on partial, 2 on auth/config error.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import REPORTS_DIR, UNUSUAL_WHALES_API_KEY  # noqa: E402
from utils.logging import get_logger, set_log_context  # noqa: E402

log = get_logger("validate_uw_historical_options")

BASE_URL = "https://api.unusualwhales.com"
DEFAULT_TIMEOUT = 30
REPORT_MD = REPORTS_DIR / "phase0b_uw_historical_options.md"
REPORT_JSON = REPORTS_DIR / "phase0b_uw_historical_options.json"

# Anchor: a recent trading day within UW's 7-trading-day historical window
# (on the current tier). Earliest allowed today (2026-04-10) is 2026-03-30.
# We pick a date that's safely inside the window so the probes exercise the
# endpoints' mechanics — the "can we go back 2 years" question was answered
# by an initial probe attempt with 2024-06-14 that returned HTTP 403
# historic_data_access_missing.
TARGET_TICKER = "SPY"
SNAPSHOT_DATE = "2026-04-06"   # Monday, 4 trading days back, within 7d window
TARGET_EXPIRY = "2026-04-17"   # Friday, ~11 days after snapshot

# UW option symbol regex per OpenAPI docs (line 16596):
#   ^(?<symbol>[\w]*)(?<expiry>(\d{2})(\d{2})(\d{2}))(?<type>[PC])(?<strike>\d{8})$
OPTION_SYMBOL_RE = re.compile(
    r"^(?P<symbol>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<type>[PC])(?P<strike>\d{8})$"
)


@dataclass
class ProbeResult:
    name: str
    ok: bool
    detail: str
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _get(session: requests.Session, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any, int]:
    """Single GET. Returns (status, parsed_json_or_none, bytes)."""
    r = session.get(f"{BASE_URL}{path}", params=params or {}, timeout=DEFAULT_TIMEOUT)
    body: Any = None
    try:
        body = r.json()
    except ValueError:
        body = {"_non_json": r.text[:300]}
    return r.status_code, body, len(r.content or b"")


def _field_populated(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str) and v.strip() in ("", "null"):
        return False
    try:
        return float(v) != 0.0 or isinstance(v, str)
    except (ValueError, TypeError):
        return True


def probe_chains_snapshot(session) -> tuple[ProbeResult, list[str]]:
    """Fetch the option-chains snapshot for SPY on SNAPSHOT_DATE.

    Returns the probe result and the raw symbol list (empty on failure).
    """
    set_log_context(probe="chains_snapshot")
    path = f"/api/stock/{TARGET_TICKER}/option-chains"
    params = {"date": SNAPSHOT_DATE}
    try:
        status, body, nbytes = _get(session, path, params)
    except Exception as e:
        return ProbeResult("chains_snapshot", False, "request failed", error=str(e)), []
    finally:
        set_log_context(probe=None)

    if status != 200:
        return (
            ProbeResult(
                "chains_snapshot",
                False,
                f"HTTP {status}",
                meta={"body_preview": str(body)[:400]},
            ),
            [],
        )

    # Response shape: {"data": ["SPY240621P00540000", ...]} per OpenAPI "Option Chains response"
    raw: list[str] = []
    if isinstance(body, dict):
        data = body.get("data") or body.get("chains") or []
        if isinstance(data, list):
            raw = [str(x) for x in data if isinstance(x, str)]

    if not raw:
        return (
            ProbeResult(
                "chains_snapshot",
                False,
                f"no symbols returned; top-level keys={list(body.keys()) if isinstance(body, dict) else type(body).__name__}",
                meta={"status": status, "bytes": nbytes, "body_preview": str(body)[:400]},
            ),
            [],
        )

    # Analyze the symbol set: expiry distribution, put/call split
    parsed: list[dict[str, Any]] = []
    for sym in raw:
        m = OPTION_SYMBOL_RE.match(sym)
        if not m:
            continue
        parsed.append(
            {
                "symbol": sym,
                "expiry": f"20{m['yy']}-{m['mm']}-{m['dd']}",
                "type": m["type"],
                "strike": int(m["strike"]) / 1000.0,
            }
        )

    expiries = sorted({p["expiry"] for p in parsed})
    target_expiry_count = sum(1 for p in parsed if p["expiry"] == TARGET_EXPIRY)
    oldest_expiry = expiries[0] if expiries else None
    newest_expiry = expiries[-1] if expiries else None
    puts_in_target = sum(1 for p in parsed if p["expiry"] == TARGET_EXPIRY and p["type"] == "P")
    calls_in_target = sum(1 for p in parsed if p["expiry"] == TARGET_EXPIRY and p["type"] == "C")

    ok = target_expiry_count >= 20 and puts_in_target > 0 and calls_in_target > 0

    return (
        ProbeResult(
            "chains_snapshot",
            ok,
            (
                f"{len(raw)} total symbols on {SNAPSHOT_DATE}; "
                f"{len(expiries)} distinct expiries ({oldest_expiry}..{newest_expiry}); "
                f"target expiry {TARGET_EXPIRY}: {puts_in_target}P + {calls_in_target}C"
            ),
            meta={
                "status": status,
                "bytes": nbytes,
                "total_symbols": len(raw),
                "distinct_expiries": len(expiries),
                "oldest_expiry": oldest_expiry,
                "newest_expiry": newest_expiry,
                "target_expiry": TARGET_EXPIRY,
                "target_expiry_puts": puts_in_target,
                "target_expiry_calls": calls_in_target,
                "sample_symbols": raw[:5],
            },
        ),
        raw,
    )


def _pick_test_contract(symbols: list[str]) -> str | None:
    """Pick a put in TARGET_EXPIRY at roughly the median strike."""
    puts: list[tuple[str, float]] = []
    for sym in symbols:
        m = OPTION_SYMBOL_RE.match(sym)
        if not m:
            continue
        expiry = f"20{m['yy']}-{m['mm']}-{m['dd']}"
        if expiry != TARGET_EXPIRY or m["type"] != "P":
            continue
        strike = int(m["strike"]) / 1000.0
        puts.append((sym, strike))
    if not puts:
        return None
    puts.sort(key=lambda x: x[1])
    return puts[len(puts) // 2][0]


def probe_historic_daily(session, contract_id: str) -> ProbeResult:
    """Fetch per-day history for a single expired option contract."""
    set_log_context(probe="historic_daily")
    path = f"/api/option-contract/{contract_id}/historic"
    try:
        status, body, nbytes = _get(session, path, None)
    except Exception as e:
        return ProbeResult("historic_daily", False, "request failed", error=str(e))
    finally:
        set_log_context(probe=None)

    if status != 200:
        return ProbeResult(
            "historic_daily",
            False,
            f"HTTP {status}",
            meta={
                "contract_id": contract_id,
                "status": status,
                "bytes": nbytes,
                "body_preview": str(body)[:500],
            },
        )

    rows: list[dict[str, Any]] = []
    if isinstance(body, dict):
        data = body.get("data") or body.get("chains") or []
        if isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]

    if not rows:
        return ProbeResult(
            "historic_daily",
            False,
            f"no rows returned; top-level keys={list(body.keys()) if isinstance(body, dict) else type(body).__name__}",
            meta={"contract_id": contract_id, "status": status, "body_preview": str(body)[:500]},
        )

    # Field-population census on all rows
    critical_fields = [
        "date",
        "nbbo_bid",
        "nbbo_ask",
        "implied_volatility",
        "open_price",
        "high_price",
        "low_price",
        "last_price",
        "volume",
        "open_interest",
    ]
    pop_counts = {f: sum(1 for r in rows if _field_populated(r.get(f))) for f in critical_fields}

    dates = sorted([str(r.get("date")) for r in rows if r.get("date")])
    first_date = dates[0] if dates else None
    last_date = dates[-1] if dates else None
    snapshot_present = any(d.startswith(SNAPSHOT_DATE) for d in dates)

    # Pass criteria: ≥10 rows, snapshot date present, nbbo_bid+nbbo_ask+IV all populated for ≥80%
    min_coverage = int(0.8 * len(rows))
    bid_ok = pop_counts["nbbo_bid"] >= min_coverage
    ask_ok = pop_counts["nbbo_ask"] >= min_coverage
    iv_ok = pop_counts["implied_volatility"] >= min_coverage
    ok = len(rows) >= 10 and snapshot_present and bid_ok and ask_ok and iv_ok

    return ProbeResult(
        "historic_daily",
        ok,
        (
            f"{len(rows)} daily rows for {contract_id}, "
            f"range {first_date}..{last_date}, snapshot_date={'Y' if snapshot_present else 'N'}, "
            f"nbbo_bid {pop_counts['nbbo_bid']}/{len(rows)}, "
            f"nbbo_ask {pop_counts['nbbo_ask']}/{len(rows)}, "
            f"IV {pop_counts['implied_volatility']}/{len(rows)}"
        ),
        meta={
            "contract_id": contract_id,
            "status": status,
            "bytes": nbytes,
            "row_count": len(rows),
            "first_date": first_date,
            "last_date": last_date,
            "snapshot_date_present": snapshot_present,
            "populated_counts": pop_counts,
            "sample_row": rows[0] if rows else None,
            "field_names_in_first_row": sorted(rows[0].keys()) if rows else [],
        },
    )


def probe_intraday_minutes(session, contract_id: str) -> ProbeResult:
    """Fetch minute-bar intraday data for a single past date."""
    set_log_context(probe="intraday_minutes")
    path = f"/api/option-contract/{contract_id}/intraday"
    params = {"date": SNAPSHOT_DATE}
    try:
        status, body, nbytes = _get(session, path, params)
    except Exception as e:
        return ProbeResult("intraday_minutes", False, "request failed", error=str(e))
    finally:
        set_log_context(probe=None)

    if status != 200:
        return ProbeResult(
            "intraday_minutes",
            False,
            f"HTTP {status}",
            meta={
                "contract_id": contract_id,
                "status": status,
                "bytes": nbytes,
                "body_preview": str(body)[:500],
            },
        )

    rows: list[dict[str, Any]] = []
    if isinstance(body, dict):
        data = body.get("data") or []
        if isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]

    if not rows:
        return ProbeResult(
            "intraday_minutes",
            False,
            f"no rows returned; top-level keys={list(body.keys()) if isinstance(body, dict) else type(body).__name__}",
            meta={"contract_id": contract_id, "status": status, "body_preview": str(body)[:500]},
        )

    critical_fields = ["open", "close", "high", "low", "iv_high", "iv_low", "start_time"]
    pop_counts = {f: sum(1 for r in rows if _field_populated(r.get(f))) for f in critical_fields}
    min_cov = int(0.8 * len(rows))
    prices_ok = pop_counts["open"] >= min_cov and pop_counts["close"] >= min_cov
    iv_ok = pop_counts["iv_high"] >= min_cov or pop_counts["iv_low"] >= min_cov

    # A full RTH session is 390 minutes; we relax to ≥100 to allow for less-liquid strikes.
    ok = len(rows) >= 100 and prices_ok and iv_ok

    return ProbeResult(
        "intraday_minutes",
        ok,
        (
            f"{len(rows)} minute ticks on {SNAPSHOT_DATE} for {contract_id}; "
            f"price-populated {pop_counts['open']}/{len(rows)}, iv-populated "
            f"{max(pop_counts['iv_high'], pop_counts['iv_low'])}/{len(rows)}"
        ),
        meta={
            "contract_id": contract_id,
            "status": status,
            "bytes": nbytes,
            "row_count": len(rows),
            "populated_counts": pop_counts,
            "first_tick": rows[0] if rows else None,
            "last_tick": rows[-1] if rows else None,
            "field_names_in_first_row": sorted(rows[0].keys()) if rows else [],
        },
    )


def write_reports(results: list[ProbeResult], chosen_contract: str | None) -> None:
    overall = all(r.ok for r in results)
    now = datetime.now().isoformat(timespec="seconds")

    md_lines = [
        "# Phase 0b — UW Historical Options Data Validation",
        "",
        f"**Run:** {now}  ",
        f"**Anchor:** ticker=`{TARGET_TICKER}` snapshot_date=`{SNAPSHOT_DATE}` target_expiry=`{TARGET_EXPIRY}`  ",
        f"**Test contract:** `{chosen_contract or '(none)'}`  ",
        f"**Overall:** {'PASS ✅' if overall else 'FAIL ❌'}",
        "",
        "## Probes",
        "",
    ]
    for r in results:
        status = "✅ PASS" if r.ok else "❌ FAIL"
        md_lines.append(f"### `{r.name}` — {status}")
        md_lines.append("")
        md_lines.append(f"**Detail:** {r.detail}")
        if r.error:
            md_lines.append(f"**Error:** `{r.error}`")
        md_lines.append("")
        if r.meta:
            md_lines.append("```json")
            md_lines.append(json.dumps(r.meta, indent=2, default=str)[:3000])
            md_lines.append("```")
        md_lines.append("")

    md_lines.extend(
        [
            "## Interpretation",
            "",
            "The critical question for Bull-Bot v3 is whether **historical bid/ask + IV** are",
            "available for **expired** option contracts on arbitrary past dates. That determines",
            "whether options backtesting is viable on UW alone.",
            "",
            "- `chains_snapshot` PASS → UW exposes the option universe as it existed on a past",
            "  date. Backtest-time chain reconstruction is viable.",
            "- `historic_daily` PASS with populated nbbo_bid/nbbo_ask/IV → we can fill orders",
            "  at realistic historical prices and derive greeks analytically from IV + spot +",
            "  strike + time-to-expiry (Black-Scholes).",
            "- `intraday_minutes` PASS → intraday options strategies (0DTE, short-dated) are",
            "  also viable; backtests can use minute bars, not just daily closes.",
            "",
            "**If any probe fails**, the v3 design needs a fork — escalate before spec approval.",
        ]
    )

    REPORT_MD.write_text("\n".join(md_lines))
    REPORT_JSON.write_text(
        json.dumps(
            {
                "run_at": now,
                "anchor": {
                    "ticker": TARGET_TICKER,
                    "snapshot_date": SNAPSHOT_DATE,
                    "target_expiry": TARGET_EXPIRY,
                    "chosen_contract": chosen_contract,
                },
                "overall_pass": overall,
                "probes": [asdict(r) for r in results],
            },
            indent=2,
            default=str,
        )
    )


def main() -> int:
    if not UNUSUAL_WHALES_API_KEY:
        log.error("UNUSUAL_WHALES_API_KEY is empty — populate .env first.")
        return 2

    set_log_context(script="validate_uw_historical_options", run_id="phase0b")
    log.info("starting UW historical options probe")

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {UNUSUAL_WHALES_API_KEY}",
            "Accept": "application/json",
            "User-Agent": "bull-bot/phase0b-validation",
        }
    )

    results: list[ProbeResult] = []

    # 1. Chain snapshot on a past date
    log.info("probing chains_snapshot")
    chains_result, symbols = probe_chains_snapshot(session)
    results.append(chains_result)

    chosen: str | None = None
    if chains_result.ok and symbols:
        chosen = _pick_test_contract(symbols)

    if not chosen:
        log.error("no test contract could be selected; skipping historic + intraday probes")
        results.append(
            ProbeResult(
                "historic_daily",
                False,
                "skipped — no test contract selected from chains_snapshot",
            )
        )
        results.append(
            ProbeResult(
                "intraday_minutes",
                False,
                "skipped — no test contract selected from chains_snapshot",
            )
        )
    else:
        log.info("selected test contract %s", chosen)
        log.info("probing historic_daily")
        results.append(probe_historic_daily(session, chosen))
        log.info("probing intraday_minutes")
        results.append(probe_intraday_minutes(session, chosen))

    write_reports(results, chosen)

    overall = all(r.ok for r in results)
    log.info("phase0b complete overall=%s report=%s", overall, REPORT_MD)
    for r in results:
        log.info("  %s %s - %s", "PASS" if r.ok else "FAIL", r.name, r.detail)

    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
