"""
T0.1 — Polygon API validation.

Probes Polygon.io to confirm it can serve the historical depth Bull-Bot needs,
that options data is reachable, and that we can see rate-limit headers so we
know what tier the key actually has.

Tests performed
---------------
Historical depth (aggregates endpoint):
    * SPY weekly, 5y lookback   (was 10y; shortened 2026-05-13 — Polygon Starter
      tier caps weekly history at ~5y. Decision: don't pay for the tier upgrade
      until paper P&L justifies it. See .mentor/proposals/2026-05-13-polygon-tier-decision.md)
    * SPY daily,  5y lookback
    * TSLA 4-hour, 3y lookback
    * TSLA 1-hour, 2y lookback
    * TSLA 15-min, 1y lookback

Options:
    * List front-month TSLA contracts via /v3/reference/options/contracts
    * Pick one put and fetch its snapshot via /v3/snapshot/options/{ticker}

Rate limits:
    * Every response's `X-RateLimit-*` / `Retry-After` headers are captured
      and summarized in the report so we can see the true tier.

Outputs
-------
* Writes ``reports/phase0_polygon.md`` (markdown, re-runnable — overwrites).
* Prints a short human summary to stderr via the Bull-Bot logger.
* Exit code 0 if every probe succeeded, 1 otherwise.

Usage
-----
    source .venv/bin/activate
    python scripts/validate_polygon.py [--rps 5] [--verbose]

Defaults to 5 requests/second (safe for any tier, including Starter). Bump
with ``--rps 50`` if you know your tier allows it.
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
from urllib.parse import parse_qsl, urlparse

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Make `config` and `utils` importable when run as a script.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import POLYGON_API_KEY, REPORTS_DIR  # noqa: E402
from utils.logging import get_logger, set_log_context  # noqa: E402

log = get_logger("validate_polygon")

BASE_URL = "https://api.polygon.io"
DEFAULT_TIMEOUT = 30
REPORT_PATH = REPORTS_DIR / "phase0_polygon.md"


# ---------------------------------------------------------------------------
# Token bucket limiter
# ---------------------------------------------------------------------------
class TokenBucket:
    """Minimal thread-safe token bucket.

    `rps` tokens per second, capacity == rps (1-second burst). `acquire()`
    blocks until one token is available.
    """

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
                needed = tokens - self._tokens
                sleep_for = needed / self.rps
            time.sleep(sleep_for)


# ---------------------------------------------------------------------------
# Request helper
# ---------------------------------------------------------------------------
class PolygonRateLimited(RuntimeError):
    pass


class PolygonTransient(RuntimeError):
    pass


@dataclass
class ResponseSample:
    """What we remember about a single HTTP response for the report."""

    status: int
    url_path: str
    rate_limit_headers: dict[str, str]
    elapsed_ms: float


@retry(
    retry=retry_if_exception_type((PolygonRateLimited, PolygonTransient, requests.RequestException)),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1.5, min=1, max=30),
    reraise=True,
)
def _http_get(
    session: requests.Session,
    bucket: TokenBucket,
    path: str,
    params: dict[str, Any] | None,
    samples: list[ResponseSample],
) -> dict[str, Any]:
    bucket.acquire()
    params = dict(params or {})
    params["apiKey"] = POLYGON_API_KEY
    url = f"{BASE_URL}{path}"
    t0 = time.monotonic()
    r = session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    elapsed_ms = (time.monotonic() - t0) * 1000

    # Capture rate-limit headers for the report.
    rl_headers = {
        k: v
        for k, v in r.headers.items()
        if k.lower().startswith("x-ratelimit") or k.lower() == "retry-after"
    }
    samples.append(
        ResponseSample(
            status=r.status_code,
            url_path=path,
            rate_limit_headers=rl_headers,
            elapsed_ms=round(elapsed_ms, 1),
        )
    )

    if r.status_code == 429:
        retry_after = r.headers.get("Retry-After")
        log.warning("429 from Polygon; Retry-After=%s path=%s", retry_after, path)
        if retry_after:
            try:
                time.sleep(min(float(retry_after), 30))
            except ValueError:
                pass
        raise PolygonRateLimited(f"429 on {path}")
    if 500 <= r.status_code < 600:
        raise PolygonTransient(f"{r.status_code} on {path}")
    if r.status_code >= 400:
        # 4xx other than 429 — no retry, surface the body.
        raise RuntimeError(f"{r.status_code} on {path}: {r.text[:300]}")
    return r.json()


# Max bars we'll accumulate across paginated pages for a single probe.
# Caps pathological queries (e.g. years of 1-min bars) without blocking
# realistic multi-year intraday lookbacks.
MAX_PAGINATED_BARS = 200_000


def _follow_aggs_pagination(
    session: requests.Session,
    bucket: TokenBucket,
    samples: list[ResponseSample],
    first_body: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, int]:
    """Follow Polygon `next_url` cursors until exhausted or the bar cap is hit.

    Polygon's /v2/aggs endpoint paginates regardless of the `limit` parameter
    for multi-year intraday windows; the response includes a `next_url` cursor
    pointing at the next page (a full absolute URL with apiKey embedded).
    Returns (combined_results, hit_cap, pages_followed). `hit_cap` is True if
    we stopped due to MAX_PAGINATED_BARS rather than running out of pages.
    """
    results = list(first_body.get("results") or [])
    next_url = first_body.get("next_url")
    pages_followed = 0
    hit_cap = False
    while next_url:
        if len(results) >= MAX_PAGINATED_BARS:
            hit_cap = True
            break
        parsed = urlparse(next_url)
        # Strip apiKey from the cursor's query — _http_get adds it from config.
        qs = {k: v for k, v in parse_qsl(parsed.query) if k != "apiKey"}
        try:
            body = _http_get(session, bucket, parsed.path, qs, samples)
        except Exception as e:
            log.error("pagination follow failed on %s: %s", parsed.path, e)
            break
        page = body.get("results") or []
        if not page:
            break
        results.extend(page)
        next_url = body.get("next_url")
        pages_followed += 1
    return results, hit_cap, pages_followed


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------
@dataclass
class ProbeResult:
    name: str
    ok: bool
    detail: str
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _today_utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _years_ago_date(years: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=365 * years + 7)).strftime("%Y-%m-%d")


def probe_aggregates(
    session: requests.Session,
    bucket: TokenBucket,
    samples: list[ResponseSample],
    ticker: str,
    multiplier: int,
    timespan: str,
    years: int,
    label: str,
) -> ProbeResult:
    """Wide probe: request the full lookback in one call.

    Passes iff: (a) first returned bar is within 60 days of requested start
    (depth), AND (b) last returned bar is within 10 days of today (freshness —
    catches per-request truncation where Polygon only returns the first page).
    """
    set_log_context(probe=label)
    start = _years_ago_date(years)
    end = _today_utc_date()
    path = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{start}/{end}"
    try:
        body = _http_get(
            session,
            bucket,
            path,
            {"adjusted": "true", "sort": "asc", "limit": 50_000},
            samples,
        )
    except Exception as e:
        log.error("probe %s failed: %s", label, e)
        return ProbeResult(name=label, ok=False, detail="request failed", error=str(e))
    finally:
        set_log_context(probe=None)

    initial_count = len(body.get("results") or [])
    results, hit_cap, pages_followed = _follow_aggs_pagination(
        session, bucket, samples, body
    )
    count = len(results)
    # next_url_present now means "we still had more pages but stopped at the cap"
    # — i.e. only True when the result is truncated by our own safety cap, not
    # by Polygon. Real freshness/depth checks below decide pass/fail.
    next_url_present = hit_cap
    if count == 0:
        return ProbeResult(
            name=label,
            ok=False,
            detail=f"0 bars returned for {ticker} {multiplier}{timespan} {start}..{end}",
            meta={"requested_start": start, "requested_end": end, "next_url": next_url_present},
        )

    first_ts = datetime.fromtimestamp(results[0]["t"] / 1000, tz=timezone.utc)
    last_ts = datetime.fromtimestamp(results[-1]["t"] / 1000, tz=timezone.utc)
    requested_start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    slack_days = (first_ts - requested_start_dt).days
    last_bar_age_days = (datetime.now(timezone.utc) - last_ts).days

    meets_depth = slack_days <= 60
    fresh = last_bar_age_days <= 10
    ok = meets_depth and fresh

    reason_bits = []
    if not meets_depth:
        reason_bits.append(f"depth FAIL (slack={slack_days}d)")
    if not fresh:
        reason_bits.append(f"freshness FAIL (last_bar {last_bar_age_days}d old)")
    status_str = "OK" if ok else "; ".join(reason_bits)

    detail = (
        f"{count} bars, first={first_ts.date()}..last={last_ts.date()}, "
        f"slack={slack_days}d, age={last_bar_age_days}d, next_url={next_url_present} "
        f"[{status_str}]"
    )
    return ProbeResult(
        name=label,
        ok=ok,
        detail=detail,
        meta={
            "ticker": ticker,
            "multiplier": multiplier,
            "timespan": timespan,
            "count": count,
            "first_ts": first_ts.isoformat(),
            "last_ts": last_ts.isoformat(),
            "requested_start": start,
            "requested_end": end,
            "slack_days": slack_days,
            "last_bar_age_days": last_bar_age_days,
            "meets_depth": meets_depth,
            "fresh": fresh,
            "next_url_present": next_url_present,
            "status": body.get("status"),
            "resultsTruncated": count >= MAX_PAGINATED_BARS,
            "initial_page_bars": initial_count,
            "pages_followed": pages_followed,
            "hit_pagination_cap": hit_cap,
        },
    )


def probe_narrow_window(
    session: requests.Session,
    bucket: TokenBucket,
    samples: list[ResponseSample],
    ticker: str,
    multiplier: int,
    timespan: str,
    years: int,
    label: str,
) -> ProbeResult:
    """Narrow probe: request a 14-day window at the far end of the lookback.

    Tests whether data exists that far back, independent of pagination caps.
    If this returns zero bars, the tier truly lacks that depth.
    """
    set_log_context(probe=label)
    start_dt = datetime.now(timezone.utc) - timedelta(days=365 * years + 7)
    end_dt = start_dt + timedelta(days=14)
    start = start_dt.strftime("%Y-%m-%d")
    end = end_dt.strftime("%Y-%m-%d")
    path = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{start}/{end}"
    try:
        body = _http_get(
            session,
            bucket,
            path,
            {"adjusted": "true", "sort": "asc", "limit": 50_000},
            samples,
        )
    except Exception as e:
        log.error("narrow probe %s failed: %s", label, e)
        return ProbeResult(name=label, ok=False, detail="request failed", error=str(e))
    finally:
        set_log_context(probe=None)

    results = body.get("results") or []
    count = len(results)
    if count == 0:
        return ProbeResult(
            name=label,
            ok=False,
            detail=(
                f"TIER LIMIT: 0 bars at far-end window {start}..{end} for "
                f"{ticker} {multiplier}{timespan} — Polygon tier does not expose "
                f"data this far back."
            ),
            meta={"window_start": start, "window_end": end, "count": 0},
        )

    first_ts = datetime.fromtimestamp(results[0]["t"] / 1000, tz=timezone.utc)
    last_ts = datetime.fromtimestamp(results[-1]["t"] / 1000, tz=timezone.utc)
    detail = (
        f"{count} bars in far-end 14-day window {start}..{end} "
        f"(first={first_ts.date()}, last={last_ts.date()}) — tier DOES support this depth"
    )
    return ProbeResult(
        name=label,
        ok=True,
        detail=detail,
        meta={
            "ticker": ticker,
            "multiplier": multiplier,
            "timespan": timespan,
            "window_start": start,
            "window_end": end,
            "count": count,
            "first_ts": first_ts.isoformat(),
            "last_ts": last_ts.isoformat(),
        },
    )


def probe_options_chain(
    session: requests.Session,
    bucket: TokenBucket,
    samples: list[ResponseSample],
) -> tuple[ProbeResult, str | None]:
    """List TSLA options contracts expiring soon, return a front-month put ticker."""
    set_log_context(probe="tsla_options_chain")
    today = _today_utc_date()
    horizon = (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%d")
    path = "/v3/reference/options/contracts"
    try:
        body = _http_get(
            session,
            bucket,
            path,
            {
                "underlying_ticker": "TSLA",
                "contract_type": "put",
                "expired": "false",
                "expiration_date.gte": today,
                "expiration_date.lte": horizon,
                "limit": 250,
                "order": "asc",
                "sort": "expiration_date",
            },
            samples,
        )
    except Exception as e:
        log.error("options_chain probe failed: %s", e)
        return (
            ProbeResult(name="tsla_options_chain", ok=False, detail="request failed", error=str(e)),
            None,
        )
    finally:
        set_log_context(probe=None)

    contracts = body.get("results") or []
    if not contracts:
        return (
            ProbeResult(
                name="tsla_options_chain",
                ok=False,
                detail=f"no TSLA put contracts returned between {today} and {horizon}",
            ),
            None,
        )

    # Pick the front (earliest) expiration, nearest-to-ATM-ish = median strike.
    earliest_exp = min(c.get("expiration_date") for c in contracts if c.get("expiration_date"))
    front_month = [c for c in contracts if c.get("expiration_date") == earliest_exp]
    front_month.sort(key=lambda c: c.get("strike_price") or 0)
    pick = front_month[len(front_month) // 2]
    picked_ticker = pick.get("ticker")

    detail = (
        f"{len(contracts)} puts across {len({c.get('expiration_date') for c in contracts})} "
        f"expirations; front month {earliest_exp} has {len(front_month)} strikes; "
        f"picked {picked_ticker} @ strike {pick.get('strike_price')}"
    )
    return (
        ProbeResult(
            name="tsla_options_chain",
            ok=bool(picked_ticker),
            detail=detail,
            meta={
                "contract_count": len(contracts),
                "front_expiration": earliest_exp,
                "picked_ticker": picked_ticker,
                "picked_strike": pick.get("strike_price"),
            },
        ),
        picked_ticker,
    )


def probe_option_snapshot(
    session: requests.Session,
    bucket: TokenBucket,
    samples: list[ResponseSample],
    option_ticker: str,
) -> ProbeResult:
    set_log_context(probe="tsla_option_snapshot")
    path = f"/v3/snapshot/options/TSLA/{option_ticker}"
    try:
        body = _http_get(session, bucket, path, None, samples)
    except Exception as e:
        log.error("option_snapshot probe failed: %s", e)
        return ProbeResult(
            name="tsla_option_snapshot",
            ok=False,
            detail=f"request failed for {option_ticker}",
            error=str(e),
        )
    finally:
        set_log_context(probe=None)

    result = body.get("results") or {}
    if not result:
        return ProbeResult(
            name="tsla_option_snapshot",
            ok=False,
            detail=f"empty snapshot payload for {option_ticker}",
        )

    greeks = result.get("greeks") or {}
    last_quote = result.get("last_quote") or {}
    day = result.get("day") or {}
    detail = (
        f"snapshot ok for {option_ticker}: "
        f"iv={result.get('implied_volatility')} "
        f"delta={greeks.get('delta')} "
        f"bid={last_quote.get('bid')} ask={last_quote.get('ask')} "
        f"day_volume={day.get('volume')}"
    )
    return ProbeResult(
        name="tsla_option_snapshot",
        ok=True,
        detail=detail,
        meta={
            "option_ticker": option_ticker,
            "implied_volatility": result.get("implied_volatility"),
            "delta": greeks.get("delta"),
            "gamma": greeks.get("gamma"),
            "theta": greeks.get("theta"),
            "vega": greeks.get("vega"),
            "bid": last_quote.get("bid"),
            "ask": last_quote.get("ask"),
            "day_volume": day.get("volume"),
        },
    )


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------
def write_report(
    results: list[ProbeResult],
    samples: list[ResponseSample],
    rps: float,
) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    overall_ok = all(r.ok for r in results)
    header_status = "✅ PASS" if overall_ok else "❌ FAIL"

    lines: list[str] = []
    lines.append("# Phase 0 — Polygon API Validation")
    lines.append("")
    lines.append(f"**Generated:** {now}  ")
    lines.append(f"**Overall:** {header_status}  ")
    lines.append(f"**Client rate limit:** {rps} req/sec (token bucket)  ")
    lines.append(f"**Requests issued:** {len(samples)}  ")
    lines.append("")

    # --- Summary table ---
    lines.append("## Probe Summary")
    lines.append("")
    lines.append("| Probe | Result | Detail |")
    lines.append("|---|---|---|")
    for r in results:
        mark = "✅" if r.ok else "❌"
        safe_detail = r.detail.replace("|", "\\|")
        lines.append(f"| `{r.name}` | {mark} | {safe_detail} |")
    lines.append("")

    # --- Historical depth: wide probes ---
    lines.append("## Historical Depth — Wide Probes")
    lines.append("")
    lines.append(
        "| Probe | Ticker | Bars | First Bar | Last Bar | Slack (d) | Last Bar Age (d) | next_url | Verdict |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        m = r.meta
        if "slack_days" not in m:
            continue
        verdict = "✅" if r.ok else "❌"
        lines.append(
            f"| `{r.name}` | {m.get('ticker')} | {m.get('count')} | "
            f"{m.get('first_ts','')[:10]} | {m.get('last_ts','')[:10]} | "
            f"{m.get('slack_days')} | {m.get('last_bar_age_days')} | "
            f"{'yes' if m.get('next_url_present') else 'no'} | {verdict} |"
        )
    lines.append("")
    lines.append(
        "> *Wide probes pass iff first bar is within 60d of requested start AND "
        "last bar is within 10d of today. `next_url=yes` means Polygon paginated "
        "the response — the single page we got is not the full range.*"
    )
    lines.append("")

    # --- Historical depth: narrow probes ---
    lines.append("## Historical Depth — Narrow Probes (14-day window at far end)")
    lines.append("")
    lines.append("| Probe | Ticker | Window | Bars | Verdict |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        m = r.meta
        if "window_start" not in m:
            continue
        verdict = "✅ tier has this depth" if r.ok else "❌ TIER LIMIT"
        bars = m.get("count", 0)
        window = f"{m.get('window_start')}..{m.get('window_end')}"
        lines.append(
            f"| `{r.name}` | {m.get('ticker','')} | {window} | {bars} | {verdict} |"
        )
    lines.append("")
    lines.append(
        "> *Narrow probes request a tiny window at the far end of the lookback to "
        "decouple tier limits from per-request pagination. Zero bars here means "
        "the Polygon tier does not expose data that far back.*"
    )
    lines.append("")

    # --- Options detail ---
    lines.append("## Options")
    lines.append("")
    for r in results:
        if r.name.startswith("tsla_option"):
            lines.append(f"### `{r.name}` — {'✅' if r.ok else '❌'}")
            lines.append("")
            lines.append(f"- {r.detail}")
            if r.meta:
                lines.append("```json")
                lines.append(json.dumps(r.meta, indent=2, default=str))
                lines.append("```")
            if r.error:
                lines.append(f"- **Error:** `{r.error}`")
            lines.append("")

    # --- Rate limit observations ---
    lines.append("## Rate Limit Observations")
    lines.append("")
    any_headers = any(s.rate_limit_headers for s in samples)
    if any_headers:
        seen_keys: dict[str, str] = {}
        for s in samples:
            for k, v in s.rate_limit_headers.items():
                seen_keys[k] = v  # keep last-observed
        lines.append("Last-observed rate-limit headers across all requests:")
        lines.append("")
        lines.append("| Header | Last Value |")
        lines.append("|---|---|")
        for k in sorted(seen_keys):
            lines.append(f"| `{k}` | `{seen_keys[k]}` |")
        lines.append("")
    else:
        lines.append(
            "_No `X-RateLimit-*` or `Retry-After` headers returned by Polygon on any "
            "response. Polygon does not advertise quotas on every tier — monitor 429 "
            "counts instead._"
        )
        lines.append("")

    # --- Per-request log ---
    lines.append("## Per-Request Log")
    lines.append("")
    lines.append("| # | Status | Path | Elapsed (ms) | Rate-Limit Headers |")
    lines.append("|---|---|---|---|---|")
    for i, s in enumerate(samples, 1):
        rl = (
            ", ".join(f"{k}={v}" for k, v in s.rate_limit_headers.items())
            if s.rate_limit_headers
            else "—"
        )
        rl = rl.replace("|", "\\|")
        short_path = s.url_path if len(s.url_path) <= 80 else s.url_path[:77] + "..."
        lines.append(f"| {i} | {s.status} | `{short_path}` | {s.elapsed_ms} | {rl} |")
    lines.append("")

    # --- Errors (if any) ---
    errors = [r for r in results if r.error]
    if errors:
        lines.append("## Errors")
        lines.append("")
        for r in errors:
            lines.append(f"### `{r.name}`")
            lines.append("")
            lines.append("```")
            lines.append(r.error or "")
            lines.append("```")
            lines.append("")

    lines.append("---")
    lines.append(
        "_Generated by `scripts/validate_polygon.py`. Re-run anytime; the report is overwritten._"
    )
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    log.info("wrote report to %s", REPORT_PATH)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 0 Polygon API validation")
    parser.add_argument(
        "--rps",
        type=float,
        default=5.0,
        help="Token-bucket requests per second (default 5; bump to 50 on Stocks Starter+).",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging.")
    args = parser.parse_args()

    if args.verbose:
        import logging as _logging
        _logging.getLogger().setLevel(_logging.DEBUG)

    if not POLYGON_API_KEY:
        log.error("POLYGON_API_KEY is empty — populate .env first.")
        return 2

    set_log_context(script="validate_polygon", run_id="phase0")
    log.info("starting Polygon validation rps=%s", args.rps)

    session = requests.Session()
    bucket = TokenBucket(args.rps)
    samples: list[ResponseSample] = []
    results: list[ProbeResult] = []

    # Historical depth probes — each depth target gets a wide probe (full
    # range, tests pagination + freshness) AND a narrow probe (14-day window
    # at the far end, tests whether the tier actually exposes data that deep).
    depth_probes = [
        ("SPY", 1, "week", 5, "spy_weekly_5y"),
        ("SPY", 1, "day", 5, "spy_daily_5y"),
        ("TSLA", 4, "hour", 3, "tsla_4h_3y"),
        ("TSLA", 1, "hour", 2, "tsla_1h_2y"),
        ("TSLA", 15, "minute", 1, "tsla_15m_1y"),
    ]
    for ticker, mult, span, years, label in depth_probes:
        log.info("probing %s (wide)", label)
        results.append(probe_aggregates(session, bucket, samples, ticker, mult, span, years, label))
        narrow_label = f"{label}_narrow"
        log.info("probing %s", narrow_label)
        results.append(
            probe_narrow_window(session, bucket, samples, ticker, mult, span, years, narrow_label)
        )

    # Options probes
    log.info("probing tsla_options_chain")
    chain_result, picked_ticker = probe_options_chain(session, bucket, samples)
    results.append(chain_result)
    if picked_ticker:
        log.info("probing tsla_option_snapshot for %s", picked_ticker)
        results.append(probe_option_snapshot(session, bucket, samples, picked_ticker))
    else:
        results.append(
            ProbeResult(
                name="tsla_option_snapshot",
                ok=False,
                detail="skipped — no contract ticker available from chain probe",
            )
        )

    # Report
    write_report(results, samples, args.rps)

    # Console summary
    passed = sum(1 for r in results if r.ok)
    total = len(results)
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        log.info("%-25s %s  %s", r.name, mark, r.detail)
    log.info("result: %d/%d probes passed", passed, total)
    log.info("report: %s", REPORT_PATH)

    # Dump full results as JSON next to the markdown for debugging.
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
    log.info("debug json: %s", debug_path)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
