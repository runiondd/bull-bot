"""Live option-chain integration for v2 Phase C.

Two public entry points:
- fetch_chain(ticker, asof, client=None): pull a Yahoo chain, cache rows
  into v2_chain_snapshots, return a Chain.
- price_leg(leg, spot, iv, today, conn=None): return (mid_price, source)
  for a single OptionLeg, trying Yahoo (cached snapshot or fresh fetch)
  before falling back to Black-Scholes.

BS math is reused from bullbot.data.synthetic_chain — do not re-implement.
"""
from __future__ import annotations

from dataclasses import dataclass

VALID_KINDS = ("call", "put")
VALID_SOURCES = ("yahoo", "bs")


@dataclass
class ChainQuote:
    """A single (expiry, strike, kind) quote with both market and model fields."""

    expiry: str            # 'YYYY-MM-DD'
    strike: float
    kind: str              # 'call' | 'put'
    bid: float | None
    ask: float | None
    last: float | None
    iv: float | None
    oi: int | None
    source: str            # 'yahoo' | 'bs'

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}; got {self.kind!r}")
        if self.source not in VALID_SOURCES:
            raise ValueError(f"source must be one of {VALID_SOURCES}; got {self.source!r}")

    def mid_price(self) -> float | None:
        """Bid-ask midpoint, or last price if either bid or ask is missing,
        or None if no prices are available."""
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        if self.last is not None:
            return self.last
        return None


@dataclass
class Chain:
    """A collection of ChainQuotes for one (ticker, asof_ts)."""

    ticker: str
    asof_ts: int
    quotes: list[ChainQuote]

    def find_quote(self, *, expiry: str, strike: float, kind: str) -> ChainQuote | None:
        """Linear lookup. Chains are O(few hundred) entries in practice, so
        a hash index would be premature optimization."""
        for q in self.quotes:
            if q.expiry == expiry and q.strike == strike and q.kind == kind:
                return q
        return None


from statistics import median

from bullbot.data.synthetic_chain import realized_vol

IV_PROXY_MIN = 0.05   # 5% — floor; lower than this and BS produces nonsense
IV_PROXY_MAX = 3.00   # 300% — ceiling; higher than this almost always means bad inputs


def _iv_proxy(*, underlying_bars: list, vix_bars: list) -> float:
    """Annualized IV estimate when Yahoo gives no IV for a strike.

    Formula:  realized_vol_30(underlying) * (vix_today / median(vix_last_60))
    Bounded to [IV_PROXY_MIN, IV_PROXY_MAX].

    Bars expected to be ordered oldest-first with a `.close` attribute (matches
    the shape that bullbot.v2.runner._load_bars and the bars table both produce).
    Falls back gracefully when either series is too short for its respective
    sub-computation:
        - underlying < 31 bars → realized_vol returns its 0.30 default
        - vix < 60 bars        → regime multiplier defaults to 1.0
    """
    rv = realized_vol(underlying_bars, window=30)
    if len(vix_bars) < 60:
        multiplier = 1.0
    else:
        vix_today = vix_bars[-1].close
        vix_baseline = median(b.close for b in vix_bars[-60:])
        multiplier = vix_today / vix_baseline if vix_baseline > 0 else 1.0
    iv = rv * multiplier
    return max(IV_PROXY_MIN, min(IV_PROXY_MAX, iv))


from datetime import date

from bullbot.data.synthetic_chain import bs_price
from bullbot.v2.positions import OptionLeg

try:
    from bullbot.config import RISK_FREE_RATE as _RISK_FREE_RATE  # type: ignore[attr-defined]
except (ImportError, AttributeError):
    _RISK_FREE_RATE = 0.045  # 4.5% — matches v1 synthetic chain default


def _price_leg_bs(
    *,
    leg: OptionLeg,
    spot: float,
    iv: float,
    today: date,
) -> float:
    """Per-share Black-Scholes price for one OptionLeg.

    Returns spot for share legs (no time value), max(intrinsic, 0) for expired
    options, and the standard BS formula otherwise. The returned value is in
    per-share dollars — callers multiply by qty * 100 (for option legs) or
    qty (for share legs) to get position-level dollar value.
    """
    if leg.kind == "share":
        return spot
    expiry_date = date.fromisoformat(leg.expiry)
    t_years = max(0.0, (expiry_date - today).days / 365.0)
    bs_kind = "C" if leg.kind == "call" else "P"
    return bs_price(
        spot=spot, strike=leg.strike, t_years=t_years,
        vol=iv, r=_RISK_FREE_RATE, kind=bs_kind,
    )


import math
import sqlite3
from typing import Callable


def _default_yf_client():
    """Lazy yfinance import — keeps tests independent of yfinance availability.
    Mirrors the pattern at bullbot/data/daily_refresh.py:36."""
    import yfinance as yf
    return lambda symbol: yf.Ticker(symbol)


def _nan_to_none(x):
    """yfinance frequently returns NaN for impliedVolatility on illiquid
    strikes. Map NaN → None so downstream consumers can branch cleanly."""
    if x is None:
        return None
    try:
        if isinstance(x, float) and math.isnan(x):
            return None
    except (TypeError, ValueError):
        return None
    return x


def _row_to_quote(row, *, expiry: str, kind: str) -> ChainQuote:
    """Convert one yfinance DataFrame row to a ChainQuote.

    yfinance column names: strike, bid, ask, lastPrice, impliedVolatility, openInterest.
    """
    return ChainQuote(
        expiry=expiry,
        strike=float(row["strike"]),
        kind=kind,
        bid=_nan_to_none(row.get("bid")),
        ask=_nan_to_none(row.get("ask")),
        last=_nan_to_none(row.get("lastPrice")),
        iv=_nan_to_none(row.get("impliedVolatility")),
        oi=int(row["openInterest"]) if row.get("openInterest") is not None else None,
        source="yahoo",
    )


def _persist_quote(conn: sqlite3.Connection, *, ticker: str, asof_ts: int, quote: ChainQuote) -> None:
    """INSERT OR REPLACE keyed on the PK (ticker, asof_ts, expiry, strike, kind).
    Idempotent — re-fetching the same chain overwrites prior values."""
    conn.execute(
        "INSERT OR REPLACE INTO v2_chain_snapshots "
        "(ticker, asof_ts, expiry, strike, kind, bid, ask, last, iv, oi, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticker, asof_ts, quote.expiry, quote.strike, quote.kind,
            quote.bid, quote.ask, quote.last, quote.iv, quote.oi, quote.source,
        ),
    )


import logging

_log = logging.getLogger(__name__)


def fetch_chain(
    *,
    conn: sqlite3.Connection,
    ticker: str,
    asof_ts: int,
    client: Callable[[str], object] | None = None,
) -> Chain | None:
    """Pull a full Yahoo option chain for `ticker`, persist into
    v2_chain_snapshots, and return the assembled Chain.

    Returns None if:
      - the Yahoo client constructor raises (network error, bad ticker)
      - the ticker has no listed options (Ticker.options is empty)
      - any option_chain(expiry) call raises mid-fetch

    On failure, NO rows are persisted (transaction is rolled back if any
    persistence happened). On success, all rows are persisted atomically.

    `client` is a callable `(symbol) -> Ticker-like object` injected for
    testing; defaults to a lazy yfinance.Ticker factory.
    """
    if client is None:
        client = _default_yf_client()

    try:
        ticker_obj = client(ticker)
        expiries: list[str] = list(ticker_obj.options)
    except Exception as exc:  # noqa: BLE001 — Yahoo can raise anything
        _log.warning("fetch_chain: client construct failed for %s: %s", ticker, exc)
        return None

    if not expiries:
        _log.info("fetch_chain: %s has no listed options", ticker)
        return None

    quotes: list[ChainQuote] = []
    try:
        for expiry in expiries:
            chain_pair = ticker_obj.option_chain(expiry)
            for _, row in chain_pair.calls.iterrows():
                quotes.append(_row_to_quote(row, expiry=expiry, kind="call"))
            for _, row in chain_pair.puts.iterrows():
                quotes.append(_row_to_quote(row, expiry=expiry, kind="put"))
    except Exception as exc:  # noqa: BLE001
        _log.warning("fetch_chain: parse failed for %s: %s", ticker, exc)
        return None

    # All expiries parsed cleanly — persist atomically.
    #
    # Grok review Tier 1 Finding A: SQLite's default isolation behavior auto-
    # begins a transaction on the first write but the semantics are fragile
    # across Python versions. Use explicit BEGIN / COMMIT / ROLLBACK so the
    # partial-failure test is a real guarantee, not a coincidence of
    # autocommit timing.
    try:
        conn.execute("BEGIN")
        for q in quotes:
            _persist_quote(conn, ticker=ticker, asof_ts=asof_ts, quote=q)
        conn.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001
        conn.execute("ROLLBACK")
        _log.warning("fetch_chain: persist failed for %s: %s", ticker, exc)
        return None

    return Chain(ticker=ticker, asof_ts=asof_ts, quotes=quotes)


SNAPSHOT_FRESHNESS_SECONDS = 86_400  # 24h — Grok review Tier 1 Finding B


def _load_bars(conn: sqlite3.Connection, ticker: str, asof_ts: int, limit: int = 100):
    """Load daily bars for `ticker` with ts <= asof_ts, oldest-first. Same
    shape as bullbot.v2.runner._load_bars (intentionally duplicated to keep
    this module self-contained — runner._load_bars is a private symbol)."""
    from types import SimpleNamespace
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM bars "
        "WHERE ticker=? AND timeframe='1d' AND ts<=? "
        "ORDER BY ts DESC LIMIT ?",
        (ticker, asof_ts, limit),
    ).fetchall()
    bars = [
        SimpleNamespace(
            ts=r["ts"], open=r["open"], high=r["high"],
            low=r["low"], close=r["close"], volume=r["volume"],
        )
        for r in rows
    ]
    bars.reverse()
    return bars


def _snapshot_at(
    conn: sqlite3.Connection, *, ticker: str, asof_ts: int,
    expiry: str, strike: float, kind: str,
) -> tuple[ChainQuote, int] | None:
    """Look up the most recent snapshot for (ticker, expiry, strike, kind)
    with asof_ts <= the requested asof_ts. Returns (ChainQuote, snapshot_asof)
    or None if no row exists.

    Returning the snapshot's own asof_ts (rather than checking freshness
    inside this helper) lets the caller decide what 'fresh' means in context
    (forward runner = strict 24h, backtest replay = might want longer)."""
    row = conn.execute(
        "SELECT asof_ts, bid, ask, last, iv, oi, source FROM v2_chain_snapshots "
        "WHERE ticker=? AND asof_ts<=? AND expiry=? AND strike=? AND kind=? "
        "ORDER BY asof_ts DESC LIMIT 1",
        (ticker, asof_ts, expiry, strike, kind),
    ).fetchone()
    if row is None:
        return None
    quote = ChainQuote(
        expiry=expiry, strike=strike, kind=kind,
        bid=row["bid"], ask=row["ask"], last=row["last"],
        iv=row["iv"], oi=row["oi"], source=row["source"],
    )
    return (quote, row["asof_ts"])


def price_leg(
    *,
    conn: sqlite3.Connection,
    ticker: str,
    leg: OptionLeg,
    spot: float,
    today: date,
    asof_ts: int,
) -> tuple[float, str]:
    """Return (per-share mid price, source) for one leg.

    Resolution order:
      1. Cached Yahoo snapshot at this (ticker, expiry, strike, kind) whose
         own asof_ts is within SNAPSHOT_FRESHNESS_SECONDS (24h) of the caller's
         asof_ts AND has a usable mid → return (mid, 'yahoo').
      2. Black-Scholes fallback using:
         - the snapshot's IV if a row exists with non-None IV (fresh OR stale
           — a stale IV is still a better hint than no hint)
         - else IV proxy over (ticker bars, VIX bars)
         Return (bs_price, 'bs').

    Share legs short-circuit: return (spot, 'bs') — shares have no chain.

    Grok review Tier 1 Finding B: stale snapshots fall back to BS to prevent
    weekend / market-closed re-runs from returning prices from days ago tagged
    as 'yahoo'. The forward runner (C.5) only calls with current-day asof_ts;
    the freshness window is a guardrail for unusual cases.
    """
    if leg.kind == "share":
        return (spot, "bs")

    snap_pair = _snapshot_at(
        conn, ticker=ticker, asof_ts=asof_ts,
        expiry=leg.expiry, strike=leg.strike, kind=leg.kind,
    )
    snap = None
    snap_age = None
    if snap_pair is not None:
        snap, snap_age_asof = snap_pair
        snap_age = asof_ts - snap_age_asof

    if snap is not None and snap_age <= SNAPSHOT_FRESHNESS_SECONDS:
        mid = snap.mid_price()
        if mid is not None:
            return (mid, "yahoo")

    if snap is not None and snap_age > SNAPSHOT_FRESHNESS_SECONDS:
        _log.info(
            "price_leg: snapshot for %s %s %s %s is stale (%ds old), falling back to BS",
            ticker, leg.expiry, leg.strike, leg.kind, snap_age,
        )

    # BS fallback — prefer snapshot IV if present (even stale), else IV proxy.
    if snap is not None and snap.iv is not None:
        iv = snap.iv
    else:
        underlying_bars = _load_bars(conn, ticker, asof_ts)
        vix_bars = _load_bars(conn, "VIX", asof_ts, limit=60)
        iv = _iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)

    bs = _price_leg_bs(leg=leg, spot=spot, iv=iv, today=today)
    return (bs, "bs")
