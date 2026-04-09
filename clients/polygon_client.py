"""
Polygon.io REST client — multi-timeframe bars, options chains, and snapshots.

Docs: https://polygon.io/docs
Only the endpoints we actually use. Keep it thin so it's easy to reason about.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import requests

from config import POLYGON_API_KEY

log = logging.getLogger(__name__)

BASE_URL = "https://api.polygon.io"
DEFAULT_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # seconds


class PolygonError(RuntimeError):
    pass


@dataclass
class Bar:
    ts: datetime       # bar close time (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None
    transactions: int | None = None


class PolygonClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or POLYGON_API_KEY
        if not self.api_key:
            log.warning("POLYGON_API_KEY is empty — requests will fail until you populate .env")
        self._session = requests.Session()

    # ---------- low-level ----------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        params = dict(params or {})
        params["apiKey"] = self.api_key
        url = f"{BASE_URL}{path}"

        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self._session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
                if r.status_code == 429:
                    wait = RETRY_BACKOFF * (attempt + 1)
                    log.warning("Polygon rate limited; sleeping %.1fs", wait)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                last_err = e
                log.warning("Polygon GET failed (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, e)
                time.sleep(RETRY_BACKOFF * (attempt + 1))
        raise PolygonError(f"Polygon GET {path} failed after {MAX_RETRIES} attempts: {last_err}")

    # ---------- aggregates ----------
    def get_aggregates(
        self,
        ticker: str,
        multiplier: int,
        timespan: str,
        start: datetime | str,
        end: datetime | str,
        adjusted: bool = True,
        limit: int = 50_000,
    ) -> list[Bar]:
        """
        Aggregate bars for a ticker.

        timespan: one of 'minute', 'hour', 'day', 'week', 'month', 'quarter', 'year'.
        start/end: datetime or 'YYYY-MM-DD' string.
        """
        if isinstance(start, datetime):
            start = start.strftime("%Y-%m-%d")
        if isinstance(end, datetime):
            end = end.strftime("%Y-%m-%d")

        path = (
            f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{start}/{end}"
        )
        data = self._get(path, {"adjusted": str(adjusted).lower(), "sort": "asc", "limit": limit})
        results = data.get("results") or []
        bars: list[Bar] = []
        for row in results:
            bars.append(
                Bar(
                    ts=datetime.fromtimestamp(row["t"] / 1000, tz=timezone.utc),
                    open=row["o"],
                    high=row["h"],
                    low=row["l"],
                    close=row["c"],
                    volume=row.get("v", 0.0),
                    vwap=row.get("vw"),
                    transactions=row.get("n"),
                )
            )
        return bars

    def get_recent_bars(self, ticker: str, multiplier: int, timespan: str, lookback_bars: int) -> list[Bar]:
        """Fetch roughly `lookback_bars` of recent bars. Over-fetches then trims."""
        # Pad the lookback window — markets don't run 24/7 so we fetch generously.
        now = datetime.now(timezone.utc)
        pad_days = {
            "minute": max(3, lookback_bars * multiplier // (60 * 6)),   # ~6.5 trading hours
            "hour":   max(10, lookback_bars * multiplier // 6),
            "day":    max(30, int(lookback_bars * 1.5)),
            "week":   max(52, lookback_bars * 7),
            "month":  max(36, lookback_bars * 30),
        }.get(timespan, 60)
        start = (now - timedelta(days=pad_days)).strftime("%Y-%m-%d")
        end = now.strftime("%Y-%m-%d")
        bars = self.get_aggregates(ticker, multiplier, timespan, start, end)
        return bars[-lookback_bars:]

    # ---------- snapshots ----------
    def get_snapshot(self, ticker: str) -> dict:
        path = f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
        return self._get(path).get("ticker", {})

    # ---------- options ----------
    def get_options_chain(self, underlying: str, expiration_date: str | None = None, limit: int = 250) -> list[dict]:
        """
        List options contracts for an underlying, optionally filtered by expiration.
        Returns the raw contract objects — callers pick what they need.
        """
        params: dict[str, Any] = {"underlying_ticker": underlying, "limit": limit}
        if expiration_date:
            params["expiration_date"] = expiration_date
        data = self._get("/v3/reference/options/contracts", params)
        return data.get("results", []) or []

    def get_option_snapshot(self, option_ticker: str) -> dict:
        """
        Single-option snapshot including greeks and last quote.
        option_ticker format: O:TSLA250620C00200000
        """
        path = f"/v3/snapshot/options/{option_ticker}"
        return self._get(path).get("results", {}) or {}

    def list_expirations(self, underlying: str) -> list[str]:
        """Unique expiration dates for an underlying, sorted ascending."""
        contracts = self.get_options_chain(underlying, limit=1000)
        exps = sorted({c.get("expiration_date") for c in contracts if c.get("expiration_date")})
        return exps
