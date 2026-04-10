"""
HTTP fetchers for UW and Polygon. These take a `client` argument rather
than constructing one themselves, so tests can inject FakeUWClient.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Protocol

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bullbot import config
from bullbot.data.schemas import Bar, OptionContract

log = logging.getLogger("bullbot.fetchers")


class DataFetchError(Exception):
    """Non-retryable data fetch failure."""


class DataSchemaError(Exception):
    """Schema mismatch in the response body."""


class UWRateLimited(RuntimeError):
    pass


class UWTransient(RuntimeError):
    pass


class _ClientLike(Protocol):
    def get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[int, Any]: ...


class UWHttpClient:
    """Real UW HTTP client using requests + tenacity retry."""

    BASE_URL = "https://api.unusualwhales.com"

    def __init__(self, api_key: str, rps: float = 10.0) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": "bull-bot/v3",
            }
        )

    @retry(
        retry=retry_if_exception_type((UWRateLimited, UWTransient, requests.RequestException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=1, max=30),
        reraise=True,
    )
    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        r = self._session.get(f"{self.BASE_URL}{path}", params=params or {}, timeout=30)
        if r.status_code == 429:
            raise UWRateLimited(f"429 on {path}")
        if 500 <= r.status_code < 600:
            raise UWTransient(f"{r.status_code} on {path}")
        try:
            body = r.json()
        except ValueError:
            body = {"_non_json": r.text[:200]}
        return r.status_code, body


def _parse_ts(raw: Any) -> int:
    """Parse a UW timestamp field into UTC epoch seconds."""
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw)
    if s.isdigit():
        return int(s)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError as e:
            raise DataSchemaError(f"cannot parse ts: {raw}") from e


def _data_list(body: Any) -> list[Any]:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in ("data", "results", "chains"):
            v = body.get(k)
            if isinstance(v, list):
                return v
    return []


def fetch_daily_ohlc(client: _ClientLike, ticker: str, limit: int = 2500) -> list[Bar]:
    """Fetch daily OHLC bars for a ticker. Raises DataFetchError on empty or 4xx."""
    status, body = client.get(
        f"/api/stock/{ticker}/ohlc/1d",
        params={"limit": limit},
    )
    if status == 200:
        rows = _data_list(body)
        if not rows:
            raise DataFetchError(f"empty OHLC response for {ticker}")
        bars: list[Bar] = []
        for r in rows:
            try:
                bars.append(
                    Bar(
                        ticker=ticker,
                        timeframe="1d",
                        ts=_parse_ts(r.get("candle_start_time") or r.get("ts") or r.get("date")),
                        open=float(r.get("open") or 0),
                        high=float(r.get("high") or 0),
                        low=float(r.get("low") or 0),
                        close=float(r.get("close") or 0),
                        volume=int(r.get("volume") or 0),
                        source="uw",
                    )
                )
            except Exception as e:
                raise DataSchemaError(f"bad bar row {r}: {e}") from e
        return bars
    if status >= 400:
        raise DataFetchError(f"{status} on ohlc/1d for {ticker}: {str(body)[:200]}")
    raise DataFetchError(f"unexpected status {status}")


def fetch_chains_snapshot(client: _ClientLike, ticker: str, date: str | None = None) -> list[str]:
    """Fetch list of option symbols that existed on `date` (or today)."""
    params: dict[str, Any] = {}
    if date:
        params["date"] = date
    status, body = client.get(f"/api/stock/{ticker}/option-chains", params=params)
    if status == 200:
        return [str(x) for x in _data_list(body) if isinstance(x, str)]
    if status == 403 and isinstance(body, dict) and body.get("code") == "historic_data_access_missing":
        log.warning("chains_snapshot gated for %s date=%s", ticker, date)
        return []
    if status >= 400:
        raise DataFetchError(f"{status} on option-chains for {ticker}: {str(body)[:200]}")
    return []


def fetch_option_historic(client: _ClientLike, option_symbol: str) -> list[OptionContract]:
    """Fetch full historical daily series for a specific option contract."""
    status, body = client.get(f"/api/option-contract/{option_symbol}/historic")
    if status == 200:
        rows = _data_list(body)
        result: list[OptionContract] = []
        m = re.match(
            r"^(?P<t>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<k>[PC])(?P<s>\d{8})$",
            option_symbol,
        )
        if not m:
            raise DataSchemaError(f"cannot parse option symbol: {option_symbol}")
        ticker = m["t"]
        expiry = f"20{m['yy']}-{m['mm']}-{m['dd']}"
        strike = int(m["s"]) / 1000.0
        kind = m["k"]

        for r in rows:
            try:
                iv_raw = r.get("implied_volatility")
                iv_val = float(iv_raw) if iv_raw not in (None, "", "null") else None
                bid = float(r.get("nbbo_bid") or 0)
                ask = float(r.get("nbbo_ask") or 0)
                if bid <= 0 or ask <= 0:
                    continue
                result.append(
                    OptionContract(
                        ticker=ticker,
                        expiry=expiry,
                        strike=strike,
                        kind=kind,
                        ts=_parse_ts(r.get("date")),
                        nbbo_bid=bid,
                        nbbo_ask=ask,
                        last=float(r["last_price"]) if r.get("last_price") else None,
                        volume=int(r["volume"]) if r.get("volume") is not None else None,
                        open_interest=int(r["open_interest"]) if r.get("open_interest") is not None else None,
                        iv=iv_val,
                    )
                )
            except Exception as e:
                log.warning("skipping bad row on %s: %s", option_symbol, e)
        return result
    if status == 404:
        return []
    if status >= 400:
        raise DataFetchError(f"{status} on historic for {option_symbol}: {str(body)[:200]}")
    return []
