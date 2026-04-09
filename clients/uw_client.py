"""
Unusual Whales REST client — options flow, dark pool prints, GEX, IV rank.

Endpoint reference: https://api.unusualwhales.com/docs
This wrapper exposes only the endpoints we consume; add more as the agents need them.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from config import UNUSUAL_WHALES_API_KEY

log = logging.getLogger(__name__)

BASE_URL = "https://api.unusualwhales.com/api"
DEFAULT_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0


class UWError(RuntimeError):
    pass


class UnusualWhalesClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or UNUSUAL_WHALES_API_KEY
        if not self.api_key:
            log.warning("UNUSUAL_WHALES_API_KEY is empty — requests will fail until you populate .env")
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        url = f"{BASE_URL}{path}"
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self._session.get(url, params=params or {}, timeout=DEFAULT_TIMEOUT)
                if r.status_code == 429:
                    wait = RETRY_BACKOFF * (attempt + 1)
                    log.warning("UW rate limited; sleeping %.1fs", wait)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                last_err = e
                log.warning("UW GET failed (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, e)
                time.sleep(RETRY_BACKOFF * (attempt + 1))
        raise UWError(f"UW GET {path} failed: {last_err}")

    # ---------- stock-level endpoints ----------
    def stock_state(self, ticker: str) -> dict:
        """
        Aggregate stock state — price, IV rank, sector, next earnings, ATR, etc.
        (Verify exact path against your UW API plan — adjust if needed.)
        """
        return self._get(f"/stock/{ticker}/stock-state")

    def options_flow(self, ticker: str, limit: int = 100) -> list[dict]:
        """Unusual options flow prints for a ticker."""
        data = self._get(f"/stock/{ticker}/flow-alerts", {"limit": limit})
        return data.get("data", []) or []

    def gex(self, ticker: str) -> dict:
        """Gamma exposure profile for a ticker."""
        return self._get(f"/stock/{ticker}/greek-exposure")

    def dark_pool_prints(self, ticker: str, limit: int = 50) -> list[dict]:
        data = self._get(f"/darkpool/{ticker}", {"limit": limit})
        return data.get("data", []) or []

    def iv_rank(self, ticker: str) -> float | None:
        """Convenience — pulls IV rank from stock-state if available."""
        try:
            st = self.stock_state(ticker)
            return float(st.get("iv_rank") or st.get("ivRank") or 0.0)
        except Exception as e:
            log.warning("iv_rank fetch failed for %s: %s", ticker, e)
            return None

    def earnings_calendar(self, from_date: str, to_date: str) -> list[dict]:
        """Upcoming earnings in a date window (YYYY-MM-DD)."""
        data = self._get("/earnings", {"from": from_date, "to": to_date})
        return data.get("data", []) or []
