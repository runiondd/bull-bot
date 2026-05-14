"""
T0.2 — Unusual Whales API validation.

Probes UW to confirm coverage of everything Bull-Bot needs, and specifically
to resolve two open Polygon tier findings from T0.1:

  * Polygon Decision 1: does UW serve weekly SPY bars with ≥10y history?
      → If yes, we skip the Polygon stocks-tier upgrade.
  * Polygon Decision 2: does UW serve options greeks, IV, and bid/ask quotes?
      → If yes, we skip the Polygon options-tier upgrade and source analytics
        from UW.

Probes performed (all GET):
  * auth                — /api/stock/SPY/stock-state  (simplest call)
  * spy_weekly_depth    — /api/stock/SPY/ohlc/1w?limit=600           [Decision 1]
  * spy_daily_depth     — /api/stock/SPY/ohlc/1d?limit=5000
  * tsla_4h_depth       — /api/stock/TSLA/ohlc/4h?limit=5000
  * tsla_1h_depth       — /api/stock/TSLA/ohlc/1h?limit=5000
  * tsla_15m_depth      — /api/stock/TSLA/ohlc/15m?limit=5000
  * tsla_option_chains  — /api/stock/TSLA/option-chains              [Decision 2]
  * tsla_greeks         — /api/stock/TSLA/greeks?expiry=<front>      [Decision 2]
  * tsla_atm_chains     — /api/stock/TSLA/atm-chains?expirations[]=<front>
  * spy_gex             — /api/stock/SPY/greek-exposure
  * spy_flow_alerts     — /api/stock/SPY/flow-alerts?limit=10
  * spy_iv_rank         — /api/stock/SPY/iv-rank

Each response is introspected: top-level keys, count of items, and whether
the key fields we care about (IV, bid/ask, greeks, timestamps) are populated.

Outputs:
  * reports/phase0_uw.md   (markdown, overwrites each run)
  * reports/phase0_uw.json (full debug dump)
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import REPORTS_DIR, UNUSUAL_WHALES_API_KEY  # noqa: E402
from utils.logging import get_logger, set_log_context  # noqa: E402

log = get_logger("validate_uw")

BASE_URL = "https://api.unusualwhales.com"
DEFAULT_TIMEOUT = 30
REPORT_PATH = REPORTS_DIR / "phase0_uw.md"


# ---------------------------------------------------------------------------
# Token bucket (same shape as validate_polygon.py — keep them independent)
# ---------------------------------------------------------------------------
class TokenBucket:
    def __init__(self, rps: float) -> None:
        self.rps = float(rps)
        self.capacity = float(rps)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rps)
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                sleep_for = (tokens - self._tokens) / self.rps
            time.sleep(sleep_for)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
class UWRateLimited(RuntimeError):
    pass


class UWTransient(RuntimeError):
    pass


@dataclass
class ResponseSample:
    status: int
    url_path: str
    rate_limit_headers: dict[str, str]
    elapsed_ms: float
    response_bytes: int


@retry(
    retry=retry_if_exception_type((UWRateLimited, UWTransient, requests.RequestException)),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1.5, min=1, max=30),
    reraise=True,
)
def _http_get(
    session: requests.Session,
    bucket: TokenBucket,
    path: str,
    params: list[tuple[str, Any]] | dict[str, Any] | None,
    samples: list[ResponseSample],
) -> dict[str, Any]:
    """Raw GET. Returns parsed JSON body. Tolerant of 200 with non-JSON."""
    bucket.acquire()
    url = f"{BASE_URL}{path}"
    t0 = time.monotonic()
    r = session.get(url, params=params or {}, timeout=DEFAULT_TIMEOUT)
    elapsed_ms = (time.monotonic() - t0) * 1000

    rl_headers = {
        k: v
        for k, v in r.headers.items()
        if k.lower().startswith("x-ratelimit")
        or k.lower() in ("retry-after", "x-request-id")
    }
    samples.append(
        ResponseSample(
            status=r.status_code,
            url_path=path,
            rate_limit_headers=rl_headers,
            elapsed_ms=round(elapsed_ms, 1),
            response_bytes=len(r.content or b""),
        )
    )

    if r.status_code == 429:
        log.warning("429 from UW path=%s retry-after=%s", path, r.headers.get("Retry-After"))
        raise UWRateLimited(f"429 on {path}")
    if 500 <= r.status_code < 600:
        raise UWTransient(f"{r.status_code} on {path}")
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code} on {path}: {r.text[:400]}")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"non-JSON response on {path}: {r.text[:200]}")


# ---------------------------------------------------------------------------
# Probe model
# ---------------------------------------------------------------------------
@dataclass
class ProbeResult:
    name: str
    ok: bool
    detail: str
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _extract_data_list(body: Any) -> list[Any]:
    """UW usually wraps arrays under 'data'; sometimes the body IS the array."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("data", "results", "chains", "ohlc"):
            val = body.get(key)
            if isinstance(val, list):
                return val
    return []


def _field_is_populated(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------
def probe_auth(session, bucket, samples) -> ProbeResult:
    set_log_context(probe="auth")
    try:
        body = _http_get(session, bucket, "/api/stock/SPY/stock-state", None, samples)
    except Exception as e:
        return ProbeResult(name="auth", ok=False, detail="stock-state call failed", error=str(e))
    finally:
        set_log_context(probe=None)

    data = body.get("data") if isinstance(body, dict) else None
    if data is None:
        return ProbeResult(
            name="auth",
            ok=False,
            detail=f"unexpected response shape: top-level keys={list(body.keys()) if isinstance(body, dict) else type(body).__name__}",
            meta={"body_preview": str(body)[:300]},
        )
    return ProbeResult(
        name="auth",
        ok=True,
        detail=f"stock-state ok; fields: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}",
        meta={"sample": data if isinstance(data, dict) else None},
    )


def probe_ohlc(
    session,
    bucket,
    samples,
    ticker: str,
    candle_size: str,
    timeframe: str,
    label: str,
    target_years: float | None = None,
    end_date: str | None = None,
    limit: int = 2500,
) -> ProbeResult:
    """Fetch OHLC and report the returned time span.

    UW's OHLC endpoint has a 2500-row cap per call. Use `timeframe` (e.g.
    '10Y', '3Y', '1Y') to anchor the lookback window; intraday calls will hit
    the 2500 cap well before the timeframe is exhausted — pagination via
    `end_date` happens in the production client, not here.

    If target_years is set, passes iff (newest - oldest) >= target_years. For
    intraday probes a narrow companion probe (see probe_ohlc_narrow) tests
    whether the tier actually exposes data that far back.
    """
    set_log_context(probe=label)
    path = f"/api/stock/{ticker}/ohlc/{candle_size}"
    params: dict[str, Any] = {"timeframe": timeframe, "limit": limit}
    if end_date:
        params["end_date"] = end_date
    try:
        body = _http_get(session, bucket, path, params, samples)
    except Exception as e:
        return ProbeResult(name=label, ok=False, detail="request failed", error=str(e))
    finally:
        set_log_context(probe=None)

    bars = _extract_data_list(body)
    if not bars:
        return ProbeResult(
            name=label,
            ok=False,
            detail=f"0 bars returned; top-level={list(body.keys()) if isinstance(body, dict) else type(body).__name__}",
            meta={"body_preview": str(body)[:300]},
        )

    def _try_parse(value: Any) -> datetime | None:
        if value is None:
            return None
        s = str(value)
        # ISO format (with optional Z suffix)
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            pass
        # Plain YYYY-MM-DD
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def _bar_dt(bar: dict[str, Any]) -> datetime | None:
        # Try the most specific timestamp fields first; SKIP `market_time`
        # because UW uses it as a session marker ('r' / 'po' / 'pr'), not a
        # timestamp.
        for k in ("start_time", "end_time", "timestamp", "date"):
            if k in bar and bar[k]:
                dt = _try_parse(bar[k])
                if dt is not None:
                    return dt
        return None

    # Bars may come ASC or DESC — parse all (cheap) and compute extremes.
    parsed_dts = [d for d in (_bar_dt(b) for b in bars if isinstance(b, dict)) if d]
    oldest = min(parsed_dts, default=None)
    newest = max(parsed_dts, default=None)
    span_days = (newest - oldest).days if (oldest and newest) else None
    span_years = round(span_days / 365.25, 2) if span_days is not None else None

    now_utc = datetime.now(timezone.utc)
    last_age_days: int | None = None
    if newest:
        last_age_days = (now_utc - (newest if newest.tzinfo else newest.replace(tzinfo=timezone.utc))).days

    ok = True
    reasons: list[str] = []
    if target_years is not None and (span_years is None or span_years < target_years - 0.1):
        ok = False
        reasons.append(f"depth FAIL (got {span_years}y, need {target_years}y)")
    if last_age_days is not None and last_age_days > 10:
        ok = False
        reasons.append(f"freshness FAIL (last bar {last_age_days}d old)")

    status_str = "OK" if ok else "; ".join(reasons)
    detail = (
        f"{len(bars)} bars, "
        f"range={oldest.date() if oldest else '?'}..{newest.date() if newest else '?'}, "
        f"span={span_years}y, last_bar_age={last_age_days}d [{status_str}]"
    )
    return ProbeResult(
        name=label,
        ok=ok,
        detail=detail,
        meta={
            "ticker": ticker,
            "candle_size": candle_size,
            "timeframe": timeframe,
            "limit": limit,
            "end_date": end_date,
            "count": len(bars),
            "oldest": oldest.isoformat() if oldest else None,
            "newest": newest.isoformat() if newest else None,
            "span_days": span_days,
            "span_years": span_years,
            "last_bar_age_days": last_age_days,
            "target_years": target_years,
            "first_bar_keys": list(bars[0].keys()) if isinstance(bars[0], dict) else None,
            "first_bar_sample": bars[0] if isinstance(bars[0], dict) else None,
        },
    )


def probe_ohlc_narrow(
    session,
    bucket,
    samples,
    ticker: str,
    candle_size: str,
    years_back: int,
    label: str,
) -> ProbeResult:
    """Narrow probe: ask for ~10 bars anchored at end_date=<years_back ago>.

    Tests whether the tier exposes data that far back, independent of the
    2500-row per-call cap. Returns OK iff the response contains bars whose
    newest timestamp is within ~30 days of the requested end_date.
    """
    set_log_context(probe=label)
    end_dt = datetime.now(timezone.utc) - timedelta(days=365 * years_back + 7)
    end_str = end_dt.strftime("%Y-%m-%d")
    path = f"/api/stock/{ticker}/ohlc/{candle_size}"
    try:
        body = _http_get(
            session,
            bucket,
            path,
            {"end_date": end_str, "limit": 10, "timeframe": "1M"},
            samples,
        )
    except Exception as e:
        return ProbeResult(
            name=label,
            ok=False,
            detail=f"request failed for end_date={end_str}",
            error=str(e),
        )
    finally:
        set_log_context(probe=None)

    bars = _extract_data_list(body)
    if not bars:
        return ProbeResult(
            name=label,
            ok=False,
            detail=(
                f"TIER LIMIT: 0 bars at end_date={end_str} for {ticker} {candle_size} — "
                f"UW tier does not expose data this far back."
            ),
            meta={"end_date": end_str, "count": 0},
        )

    def _ts(bar: dict[str, Any]) -> datetime | None:
        # Skip market_time — UW uses it as a session marker, not a timestamp.
        for k in ("start_time", "end_time", "date"):
            if k in bar and bar[k]:
                try:
                    return datetime.fromisoformat(str(bar[k]).replace("Z", "+00:00"))
                except Exception:
                    try:
                        return datetime.strptime(str(bar[k])[:10], "%Y-%m-%d").replace(
                            tzinfo=timezone.utc
                        )
                    except Exception:
                        pass
        return None

    parsed = [t for t in (_ts(b) for b in bars if isinstance(b, dict)) if t]
    if not parsed:
        return ProbeResult(
            name=label,
            ok=False,
            detail=f"got {len(bars)} bars at end_date={end_str} but no parseable timestamps",
            meta={"end_date": end_str, "count": len(bars)},
        )
    newest = max(parsed)
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    end_anchor = end_dt if end_dt.tzinfo else end_dt.replace(tzinfo=timezone.utc)
    distance_days = abs((newest - end_anchor).days)
    ok = distance_days <= 60  # within ~2 months of requested anchor

    return ProbeResult(
        name=label,
        ok=ok,
        detail=(
            f"got {len(bars)} bars at end_date={end_str}; newest bar {newest.date()} "
            f"({distance_days}d from anchor) — tier {'DOES' if ok else 'does NOT'} "
            f"support this depth"
        ),
        meta={
            "ticker": ticker,
            "candle_size": candle_size,
            "end_date": end_str,
            "count": len(bars),
            "newest_returned": newest.isoformat(),
            "distance_days": distance_days,
        },
    )


def probe_option_chains(session, bucket, samples) -> tuple[ProbeResult, str | None]:
    """Verify option-chains returns IV and bid/ask per contract. Returns a
    front-month expiry date string for downstream greeks probes, if found.
    """
    set_log_context(probe="tsla_option_chains")
    try:
        body = _http_get(session, bucket, "/api/stock/TSLA/option-chains", None, samples)
    except Exception as e:
        return (
            ProbeResult(name="tsla_option_chains", ok=False, detail="request failed", error=str(e)),
            None,
        )
    finally:
        set_log_context(probe=None)

    data = _extract_data_list(body)
    if not data:
        return (
            ProbeResult(
                name="tsla_option_chains",
                ok=False,
                detail=f"empty; body top-level={list(body.keys()) if isinstance(body, dict) else type(body).__name__}",
                meta={"body_preview": str(body)[:300]},
            ),
            None,
        )

    # UW sometimes returns a list of contract symbols, sometimes full objects.
    # Inspect the first item.
    first = data[0]
    if isinstance(first, str):
        # The "chains" endpoint may just return a list of option_symbol strings.
        # That won't give us IV/bid/ask — we'll probe atm-chains instead.
        # Try to extract an expiry from a symbol like O:TSLA260620P00200000.
        sample_symbols = data[:5]
        exp_found = None
        for s in sample_symbols:
            # Parse OCC-ish format: TSLA YYMMDD P/C STRIKE
            import re
            m = re.search(r"(\d{6})[CP]\d+", s)
            if m:
                yymmdd = m.group(1)
                exp_found = f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
                break
        detail = (
            f"returned {len(data)} option symbols (not full contract objects); "
            f"sample={sample_symbols[:3]}. No IV/bid/ask at this endpoint — must "
            f"use atm-chains or option-contract endpoints for quotes."
        )
        return (
            ProbeResult(
                name="tsla_option_chains",
                ok=True,
                detail=detail,
                meta={
                    "count": len(data),
                    "response_type": "symbol_list",
                    "sample_symbols": sample_symbols,
                    "extracted_expiry": exp_found,
                },
            ),
            exp_found,
        )

    if isinstance(first, dict):
        keys = list(first.keys())
        iv = first.get("implied_volatility")
        bid = first.get("nbbo_bid") or first.get("bid")
        ask = first.get("nbbo_ask") or first.get("ask")
        delta = first.get("delta")
        have_iv = _field_is_populated(iv)
        have_quotes = _field_is_populated(bid) and _field_is_populated(ask)
        have_delta = _field_is_populated(delta)

        # Try to find an expiry field for downstream probe
        exp_candidate = None
        for k in ("expiry", "expiration_date", "expires_at"):
            if k in first and first[k]:
                exp_candidate = str(first[k])[:10]
                break

        ok = have_iv and have_quotes  # Decision 2 core criteria
        status_bits = [
            f"IV={'✓' if have_iv else '✗'}",
            f"bid/ask={'✓' if have_quotes else '✗'}",
            f"delta={'✓' if have_delta else '✗'}",
        ]
        detail = (
            f"{len(data)} contracts; first contract fields populated: "
            f"{' '.join(status_bits)}; keys={keys[:15]}"
        )
        return (
            ProbeResult(
                name="tsla_option_chains",
                ok=ok,
                detail=detail,
                meta={
                    "count": len(data),
                    "response_type": "contract_object",
                    "first_contract_keys": keys,
                    "sample_values": {
                        "implied_volatility": iv,
                        "nbbo_bid": bid,
                        "nbbo_ask": ask,
                        "delta": delta,
                        "open_interest": first.get("open_interest"),
                        "volume": first.get("volume"),
                    },
                    "extracted_expiry": exp_candidate,
                },
            ),
            exp_candidate,
        )

    return (
        ProbeResult(
            name="tsla_option_chains",
            ok=False,
            detail=f"unexpected item type in data[0]: {type(first).__name__}",
        ),
        None,
    )


def probe_greeks(session, bucket, samples, expiry: str) -> ProbeResult:
    set_log_context(probe="tsla_greeks")
    try:
        body = _http_get(
            session, bucket, "/api/stock/TSLA/greeks", {"expiry": expiry}, samples
        )
    except Exception as e:
        return ProbeResult(name="tsla_greeks", ok=False, detail="request failed", error=str(e))
    finally:
        set_log_context(probe=None)

    data = _extract_data_list(body)
    if not data:
        return ProbeResult(
            name="tsla_greeks",
            ok=False,
            detail=f"empty for expiry={expiry}; top-level={list(body.keys()) if isinstance(body, dict) else type(body).__name__}",
            meta={"body_preview": str(body)[:300]},
        )

    first = data[0] if isinstance(data[0], dict) else {}
    greek_fields = [
        "call_delta",
        "call_gamma",
        "call_theta",
        "call_vega",
        "put_delta",
        "put_gamma",
        "put_theta",
        "put_vega",
        "call_volatility",
        "put_volatility",
    ]
    populated = {k: _field_is_populated(first.get(k)) for k in greek_fields}
    n_populated = sum(populated.values())
    ok = n_populated >= 6  # expect at least 6 of the 10 greek fields

    detail = (
        f"expiry={expiry}, {len(data)} strikes; "
        f"populated {n_populated}/{len(greek_fields)} greek fields in first strike"
    )
    return ProbeResult(
        name="tsla_greeks",
        ok=ok,
        detail=detail,
        meta={
            "expiry": expiry,
            "strike_count": len(data),
            "first_strike_keys": list(first.keys()),
            "first_strike_sample": first,
            "greek_population": populated,
        },
    )


def probe_atm_chains(session, bucket, samples, expiry: str) -> ProbeResult:
    set_log_context(probe="tsla_atm_chains")
    try:
        body = _http_get(
            session,
            bucket,
            "/api/stock/TSLA/atm-chains",
            [("expirations[]", expiry)],
            samples,
        )
    except Exception as e:
        return ProbeResult(name="tsla_atm_chains", ok=False, detail="request failed", error=str(e))
    finally:
        set_log_context(probe=None)

    data = _extract_data_list(body)
    if not data:
        return ProbeResult(
            name="tsla_atm_chains",
            ok=False,
            detail=f"empty; top-level={list(body.keys()) if isinstance(body, dict) else type(body).__name__}",
            meta={"body_preview": str(body)[:300]},
        )

    first = data[0] if isinstance(data[0], dict) else {}
    iv = first.get("implied_volatility") or first.get("iv")
    bid = first.get("nbbo_bid") or first.get("bid")
    ask = first.get("nbbo_ask") or first.get("ask")
    has_iv = _field_is_populated(iv)
    has_quotes = _field_is_populated(bid) and _field_is_populated(ask)
    ok = has_iv and has_quotes

    return ProbeResult(
        name="tsla_atm_chains",
        ok=ok,
        detail=(
            f"{len(data)} ATM contracts; IV={'✓' if has_iv else '✗'} "
            f"bid/ask={'✓' if has_quotes else '✗'}"
        ),
        meta={
            "expiry": expiry,
            "count": len(data),
            "first_contract_keys": list(first.keys()),
            "sample_values": {
                "implied_volatility": iv,
                "nbbo_bid": bid,
                "nbbo_ask": ask,
                "open_interest": first.get("open_interest"),
                "volume": first.get("volume"),
            },
        },
    )


def probe_simple(session, bucket, samples, path: str, label: str) -> ProbeResult:
    """Light probe for endpoints where 200 + non-empty data is good enough."""
    set_log_context(probe=label)
    try:
        body = _http_get(session, bucket, path, None, samples)
    except Exception as e:
        return ProbeResult(name=label, ok=False, detail="request failed", error=str(e))
    finally:
        set_log_context(probe=None)

    data = _extract_data_list(body)
    if data:
        sample_keys = list(data[0].keys()) if isinstance(data[0], dict) else None
        return ProbeResult(
            name=label,
            ok=True,
            detail=f"{len(data)} items; first-item keys={sample_keys[:12] if sample_keys else '-'}",
            meta={"count": len(data), "first_item_keys": sample_keys},
        )

    # No list payload — check for a single dict payload
    payload = body.get("data") if isinstance(body, dict) else None
    if isinstance(payload, dict) and payload:
        return ProbeResult(
            name=label,
            ok=True,
            detail=f"single-object payload; keys={list(payload.keys())[:12]}",
            meta={"payload_keys": list(payload.keys())},
        )
    return ProbeResult(
        name=label,
        ok=False,
        detail=f"empty/unexpected; top-level={list(body.keys()) if isinstance(body, dict) else type(body).__name__}",
        meta={"body_preview": str(body)[:300]},
    )


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------
def write_report(results: list[ProbeResult], samples: list[ResponseSample], rps: float) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    overall_ok = all(r.ok for r in results)
    header_status = "✅ PASS" if overall_ok else "❌ PARTIAL / FAIL"

    lines: list[str] = []
    lines.append("# Phase 0 — Unusual Whales API Validation")
    lines.append("")
    lines.append(f"**Generated:** {now}  ")
    lines.append(f"**Overall:** {header_status}  ")
    lines.append(f"**Client rate limit:** {rps} req/sec (token bucket)  ")
    lines.append(f"**Requests issued:** {len(samples)}  ")
    lines.append("")

    lines.append("## Probe Summary")
    lines.append("")
    lines.append("| Probe | Result | Detail |")
    lines.append("|---|---|---|")
    for r in results:
        mark = "✅" if r.ok else "❌"
        detail = r.detail.replace("|", "\\|")
        lines.append(f"| `{r.name}` | {mark} | {detail} |")
    lines.append("")

    # Polygon-decision callouts
    lines.append("## Polygon Decisions — Resolution")
    lines.append("")
    daily_wide = next((r for r in results if r.name == "spy_daily_10y"), None)
    daily_narrow = next((r for r in results if r.name == "spy_daily_10y_narrow"), None)
    chains = next((r for r in results if r.name == "tsla_option_chains"), None)
    greeks = next((r for r in results if r.name == "tsla_greeks"), None)
    atm = next((r for r in results if r.name == "tsla_atm_chains"), None)

    lines.append("### Decision 1 — Weekly SPY depth (need ≥10y)")
    lines.append("")
    lines.append(
        "_UW's OHLC endpoint does not support `1w` natively (despite the OpenAPI enum). "
        "We test 10y daily SPY instead and resample to weekly in code._"
    )
    lines.append("")
    if daily_wide and daily_wide.ok and daily_narrow and daily_narrow.ok:
        lines.append(
            f"- **UW serves 10y daily SPY.** Wide: {daily_wide.detail}. "
            f"Narrow: {daily_narrow.detail}."
        )
        lines.append(
            "- **→ No Polygon stocks upgrade needed.** Source daily SPY from UW, "
            "resample to weekly in `clients/uw_client.py` or `data/`."
        )
    elif daily_wide and daily_wide.ok:
        lines.append(
            f"- Wide probe passed but narrow far-end probe failed: {daily_narrow.detail if daily_narrow else 'no narrow probe'}."
        )
        lines.append(
            "- Tier may have a soft depth cap. Treat with skepticism — re-test before committing."
        )
    elif daily_wide:
        lines.append(f"- **UW daily 10y FAIL.** {daily_wide.detail}")
        lines.append(
            "- Fallback options: (a) upgrade Polygon stocks tier (~$50/mo) "
            "OR (b) accept 5y depth and reframe backtest."
        )
    else:
        lines.append("- _daily depth probe did not run_")
    lines.append("")

    lines.append("### Decision 2 — Options analytics (greeks + IV + bid/ask)")
    analytic_sources = []
    if chains and chains.ok:
        analytic_sources.append(f"`option-chains` ({chains.detail})")
    if atm and atm.ok:
        analytic_sources.append(f"`atm-chains` ({atm.detail})")
    if greeks and greeks.ok:
        analytic_sources.append(f"`greeks` ({greeks.detail})")

    if analytic_sources:
        lines.append("- **UW covers options analytics via:**")
        for s in analytic_sources:
            lines.append(f"  - {s}")
        lines.append(
            "- **No Polygon options upgrade needed.** "
            "Bull-Bot sources greeks/IV/quotes from UW."
        )
    else:
        lines.append(
            "- **UW does NOT cover options analytics.** "
            "Need to upgrade Polygon options tier or drop options strategies."
        )
    lines.append("")

    # --- OHLC depth table ---
    depth_rows = [r for r in results if "candle_size" in r.meta]
    if depth_rows:
        lines.append("## Historical OHLC Depth")
        lines.append("")
        lines.append(
            "| Probe | Ticker | Candle | Bars | Oldest | Newest | Span (y) | Last Bar Age (d) | Verdict |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in depth_rows:
            m = r.meta
            verdict = "✅" if r.ok else "❌"
            lines.append(
                f"| `{r.name}` | {m.get('ticker')} | {m.get('candle_size')} | "
                f"{m.get('count')} | {(m.get('oldest') or '')[:10]} | "
                f"{(m.get('newest') or '')[:10]} | {m.get('span_years')} | "
                f"{m.get('last_bar_age_days')} | {verdict} |"
            )
        lines.append("")

    # --- Detailed probe dumps for the most important probes ---
    lines.append("## Options Probe Detail")
    lines.append("")
    for r in (chains, atm, greeks):
        if not r:
            continue
        lines.append(f"### `{r.name}` — {'✅' if r.ok else '❌'}")
        lines.append("")
        lines.append(f"- {r.detail}")
        if r.meta:
            lines.append("```json")
            lines.append(json.dumps(r.meta, indent=2, default=str)[:2500])
            lines.append("```")
        if r.error:
            lines.append(f"- **Error:** `{r.error}`")
        lines.append("")

    # --- Rate-limit section ---
    lines.append("## Rate Limit Observations")
    lines.append("")
    any_headers = any(s.rate_limit_headers for s in samples)
    if any_headers:
        seen: dict[str, str] = {}
        for s in samples:
            for k, v in s.rate_limit_headers.items():
                seen[k] = v
        lines.append("| Header | Last Value |")
        lines.append("|---|---|")
        for k in sorted(seen):
            lines.append(f"| `{k}` | `{seen[k]}` |")
    else:
        lines.append(
            "_No `X-RateLimit-*` / `Retry-After` headers returned by UW. "
            "Monitor 429s in production instead._"
        )
    lines.append("")

    # --- Per-request log ---
    lines.append("## Per-Request Log")
    lines.append("")
    lines.append("| # | Status | Path | Bytes | Elapsed (ms) | Headers |")
    lines.append("|---|---|---|---|---|---|")
    for i, s in enumerate(samples, 1):
        rl = (
            ", ".join(f"{k}={v}" for k, v in s.rate_limit_headers.items())
            if s.rate_limit_headers
            else "—"
        )
        rl = rl.replace("|", "\\|")
        short_path = s.url_path if len(s.url_path) <= 80 else s.url_path[:77] + "..."
        lines.append(
            f"| {i} | {s.status} | `{short_path}` | {s.response_bytes} | {s.elapsed_ms} | {rl} |"
        )
    lines.append("")

    errors = [r for r in results if r.error]
    if errors:
        lines.append("## Errors")
        lines.append("")
        for r in errors:
            lines.append(f"### `{r.name}`")
            lines.append("```")
            lines.append(r.error or "")
            lines.append("```")
            lines.append("")

    lines.append("---")
    lines.append(
        "_Generated by `scripts/validate_uw.py`. Re-run anytime; the report is overwritten._"
    )
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    log.info("wrote report to %s", REPORT_PATH)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 0 Unusual Whales API validation")
    parser.add_argument("--rps", type=float, default=2.0, help="Token-bucket rps (default 2).")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        import logging as _logging
        _logging.getLogger().setLevel(_logging.DEBUG)

    if not UNUSUAL_WHALES_API_KEY:
        log.error("UNUSUAL_WHALES_API_KEY is empty — populate .env first.")
        return 2

    set_log_context(script="validate_uw", run_id="phase0")
    log.info("starting UW validation rps=%s", args.rps)

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {UNUSUAL_WHALES_API_KEY}",
            "Accept": "application/json",
            "User-Agent": "bull-bot/phase0-validation",
        }
    )
    bucket = TokenBucket(args.rps)
    samples: list[ResponseSample] = []
    results: list[ProbeResult] = []

    # 1. Auth
    log.info("probing auth")
    results.append(probe_auth(session, bucket, samples))

    # 2. Historical OHLC depth probes.
    # UW does not support `1w` natively (the OpenAPI enum is stale; the live
    # API rejects it). Strategy: probe `1d` with timeframe=10Y — that resolves
    # Polygon Decision 1 because we can resample daily → weekly in code.
    # Intraday probes hit the 2500 cap before exhausting the timeframe; the
    # narrow companion probes test true tier depth via end_date.
    wide_probes = [
        # (ticker, candle, timeframe, label, target_years)
        ("SPY", "1d", "10Y", "spy_daily_10y", 9.5),
        ("TSLA", "4h", "3Y", "tsla_4h_3y", 2.8),
        ("TSLA", "1h", "2Y", "tsla_1h_2y", 1.4),  # 2500 cap → ~1.5y in single call
        ("TSLA", "15m", "1Y", "tsla_15m_1y", 0.4),  # 2500 cap → ~4-5 months in single call
    ]
    for ticker, candle, timeframe, label, target_years in wide_probes:
        log.info("probing %s (wide)", label)
        results.append(
            probe_ohlc(
                session,
                bucket,
                samples,
                ticker,
                candle,
                timeframe,
                label,
                target_years=target_years,
            )
        )

    # Narrow far-end probes — verify UW tier exposes data at the target depth,
    # independent of the 2500-row cap.
    narrow_probes = [
        ("SPY", "1d", 10, "spy_daily_10y_narrow"),
        ("TSLA", "4h", 3, "tsla_4h_3y_narrow"),
        ("TSLA", "1h", 2, "tsla_1h_2y_narrow"),
        ("TSLA", "15m", 1, "tsla_15m_1y_narrow"),
    ]
    for ticker, candle, years_back, label in narrow_probes:
        log.info("probing %s", label)
        results.append(
            probe_ohlc_narrow(session, bucket, samples, ticker, candle, years_back, label)
        )

    # 3. Options chains (primary: does UW serve IV + bid/ask per contract?)
    log.info("probing tsla_option_chains")
    chain_result, front_expiry = probe_option_chains(session, bucket, samples)
    results.append(chain_result)

    # 4. Greeks — needs an expiry
    if front_expiry:
        log.info("probing tsla_greeks for expiry=%s", front_expiry)
        results.append(probe_greeks(session, bucket, samples, front_expiry))
        log.info("probing tsla_atm_chains for expiry=%s", front_expiry)
        results.append(probe_atm_chains(session, bucket, samples, front_expiry))
    else:
        results.append(
            ProbeResult(
                name="tsla_greeks",
                ok=False,
                detail="skipped — no front-month expiry extractable from option-chains probe",
            )
        )
        results.append(
            ProbeResult(
                name="tsla_atm_chains",
                ok=False,
                detail="skipped — no front-month expiry extractable from option-chains probe",
            )
        )

    # 5. GEX, flow alerts, IV rank — simple presence probes
    log.info("probing spy_gex")
    results.append(probe_simple(session, bucket, samples, "/api/stock/SPY/greek-exposure", "spy_gex"))
    log.info("probing spy_flow_alerts")
    results.append(
        probe_simple(session, bucket, samples, "/api/stock/SPY/flow-alerts", "spy_flow_alerts")
    )
    log.info("probing spy_iv_rank")
    results.append(probe_simple(session, bucket, samples, "/api/stock/SPY/iv-rank", "spy_iv_rank"))

    # Write report + debug json
    write_report(results, samples, args.rps)
    debug_path = REPORT_PATH.with_suffix(".json")
    debug_path.write_text(
        json.dumps(
            {
                "generated": datetime.now(timezone.utc).isoformat(),
                "rps": args.rps,
                "results": [asdict(r) for r in results],
                "samples": [asdict(s) for s in samples],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    passed = sum(1 for r in results if r.ok)
    total = len(results)
    for r in results:
        log.info("%-22s %s  %s", r.name, "PASS" if r.ok else "FAIL", r.detail)
    log.info("result: %d/%d probes passed", passed, total)
    log.info("report: %s", REPORT_PATH)
    log.info("debug json: %s", debug_path)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
