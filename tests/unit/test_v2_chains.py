"""Unit tests for bullbot.v2.chains — Yahoo + BS pricing layer."""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from bullbot.db.migrations import apply_schema
from bullbot.v2 import chains
from bullbot.v2.positions import OptionLeg


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_schema(c)
    return c


def test_chainquote_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be one of"):
        chains.ChainQuote(
            expiry="2026-06-19", strike=100.0, kind="future",
            bid=1.0, ask=1.2, last=1.1, iv=0.30, oi=100, source="yahoo",
        )


def test_chainquote_rejects_unknown_source():
    with pytest.raises(ValueError, match="source must be one of"):
        chains.ChainQuote(
            expiry="2026-06-19", strike=100.0, kind="call",
            bid=1.0, ask=1.2, last=1.1, iv=0.30, oi=100, source="polygon",
        )


def test_chainquote_mid_price_returns_bid_ask_midpoint():
    q = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=1.00, ask=1.20, last=None, iv=0.30, oi=100, source="yahoo",
    )
    assert q.mid_price() == pytest.approx(1.10)


def test_chainquote_mid_price_falls_back_to_last_when_bid_or_ask_missing():
    q = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=None, ask=None, last=1.15, iv=0.30, oi=100, source="yahoo",
    )
    assert q.mid_price() == 1.15


def test_chainquote_mid_price_returns_none_when_no_prices_available():
    q = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=None, ask=None, last=None, iv=0.30, oi=100, source="yahoo",
    )
    assert q.mid_price() is None


def test_chain_empty_quotes_is_valid():
    c = chains.Chain(ticker="AAPL", asof_ts=1_700_000_000, quotes=[])
    assert c.ticker == "AAPL"
    assert c.quotes == []


def test_chain_find_quote_returns_matching_strike_and_kind():
    q1 = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="call",
        bid=1.0, ask=1.2, last=1.1, iv=0.30, oi=100, source="yahoo",
    )
    q2 = chains.ChainQuote(
        expiry="2026-06-19", strike=100.0, kind="put",
        bid=0.8, ask=1.0, last=0.9, iv=0.32, oi=80, source="yahoo",
    )
    c = chains.Chain(ticker="AAPL", asof_ts=1_700_000_000, quotes=[q1, q2])
    assert c.find_quote(expiry="2026-06-19", strike=100.0, kind="call") is q1
    assert c.find_quote(expiry="2026-06-19", strike=100.0, kind="put") is q2
    assert c.find_quote(expiry="2026-06-19", strike=105.0, kind="call") is None


from types import SimpleNamespace


def _bars(closes: list[float]) -> list[SimpleNamespace]:
    """Build a list of bar-shaped namespaces from a sequence of closes.
    Mirrors the shape that bullbot.v2.runner._load_bars produces."""
    return [
        SimpleNamespace(ts=1_700_000_000 + i * 86400,
                        open=c, high=c, low=c, close=c, volume=1_000_000.0)
        for i, c in enumerate(closes)
    ]


def test_iv_proxy_returns_realized_vol_when_regime_multiplier_is_one():
    """VIX flat at its 60-day median → multiplier = 1.0, IV proxy ≈ realized vol."""
    underlying_bars = _bars([100.0 * (1 + 0.01 * ((-1) ** i)) for i in range(60)])
    vix_bars = _bars([18.0] * 60)
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    # underlying alternates ±1% so realized vol ~ 16% annualized; should land
    # near that, well above the 0.05 floor.
    assert 0.05 < iv < 0.50


def _alternating_bars():
    """60 bars alternating ±1% — produces realized_vol ≈ 0.158 (annualized)."""
    return _bars([100.0 * (1 + 0.01 * ((-1) ** i)) for i in range(60)])


def test_iv_proxy_scales_up_when_vix_above_baseline():
    """Today's VIX = 30, 60-day median VIX = 15 → multiplier = 2.0,
    IV proxy = realized_vol * 2.0 (subject to the [0.05, 3.0] clamp)."""
    underlying_bars = _alternating_bars()  # rv ≈ 0.158
    flat_vix = chains._iv_proxy(underlying_bars=underlying_bars,
                                vix_bars=_bars([15.0] * 60))
    vix_bars = _bars([15.0] * 59 + [30.0])
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    # 2.0× regime multiplier vs the flat-VIX baseline
    assert iv == pytest.approx(2.0 * flat_vix, abs=0.01)


def test_iv_proxy_scales_down_when_vix_below_baseline():
    underlying_bars = _alternating_bars()
    flat_vix = chains._iv_proxy(underlying_bars=underlying_bars,
                                vix_bars=_bars([20.0] * 60))
    vix_bars = _bars([20.0] * 59 + [10.0])
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    # 0.5× regime multiplier vs the flat-VIX baseline
    assert iv == pytest.approx(0.5 * flat_vix, abs=0.01)


def test_iv_proxy_clamps_to_upper_bound_on_pathological_vix_spike():
    underlying_bars = _alternating_bars()
    vix_bars = _bars([10.0] * 59 + [200.0])  # 20× spike (impossible but test the clamp)
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    assert iv == 3.0


def test_iv_proxy_falls_back_to_default_when_underlying_bars_too_few():
    underlying_bars = _bars([100.0] * 5)  # < 31 bars → realized_vol returns its 0.30 default
    vix_bars = _bars([18.0] * 60)
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    assert iv == pytest.approx(0.30, abs=0.01)


def test_iv_proxy_returns_floor_when_underlying_bars_are_flat():
    """Flat closes → realized_vol = 0 → iv = 0 → clamped to IV_PROXY_MIN.
    Documents the actual behavior (not the original test's assumption that
    flat triggers the 0.30 default — that default only fires with too few bars)."""
    underlying_bars = _bars([100.0] * 60)
    vix_bars = _bars([18.0] * 60)
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    assert iv == chains.IV_PROXY_MIN


def test_iv_proxy_falls_back_to_default_when_vix_bars_too_few():
    underlying_bars = _alternating_bars()
    vix_bars = _bars([18.0] * 5)  # < 60 → can't compute regime multiplier
    iv = chains._iv_proxy(underlying_bars=underlying_bars, vix_bars=vix_bars)
    # Multiplier defaults to 1.0; result is the realized vol of the alternating pattern.
    assert 0.05 < iv < 1.0


def _call_leg(strike: float, expiry: str = "2026-12-18", qty: int = 1) -> OptionLeg:
    return OptionLeg(
        action="buy", kind="call", strike=strike,
        expiry=expiry, qty=qty, entry_price=0.0,
    )


def _put_leg(strike: float, expiry: str = "2026-12-18", qty: int = 1) -> OptionLeg:
    return OptionLeg(
        action="buy", kind="put", strike=strike,
        expiry=expiry, qty=qty, entry_price=0.0,
    )


def test_price_leg_bs_atm_call_with_30pct_iv_and_one_year_dte():
    """ATM call, S=K=100, T=1yr, IV=0.30, r=0.045
    -> textbook BS price ≈ 13.99 (per share)."""
    leg = _call_leg(strike=100.0, expiry="2027-05-17")
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=100.0, iv=0.30, today=today)
    assert price == pytest.approx(13.99, abs=0.10)


def test_price_leg_bs_atm_put_with_30pct_iv_and_one_year_dte():
    """ATM put, S=K=100, T=1yr, IV=0.30, r=0.045
    -> textbook BS price ≈ 9.59 (per share, via put-call parity:
    C - P = S - K*exp(-r*T) = 100 - 95.60 = 4.40, so P = 13.99 - 4.40 = 9.59)."""
    leg = _put_leg(strike=100.0, expiry="2027-05-17")
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=100.0, iv=0.30, today=today)
    assert price == pytest.approx(9.59, abs=0.10)


def test_price_leg_bs_itm_call_intrinsic_floor_on_expiry_day():
    """Call deep ITM on expiry day: BS returns max(spot - strike, 0)."""
    leg = _call_leg(strike=90.0, expiry="2026-05-17")
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=100.0, iv=0.30, today=today)
    assert price == pytest.approx(10.0)


def test_price_leg_bs_otm_call_intrinsic_floor_on_expiry_day():
    leg = _call_leg(strike=110.0, expiry="2026-05-17")
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=100.0, iv=0.30, today=today)
    assert price == 0.0


def test_price_leg_bs_otm_put_intrinsic_floor_on_expiry_day():
    leg = _put_leg(strike=90.0, expiry="2026-05-17")
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=100.0, iv=0.30, today=today)
    assert price == 0.0


def test_price_leg_bs_share_leg_returns_spot():
    """Share legs have no time value, no strike — BS doesn't apply.
    The helper returns spot so the caller can sum leg values uniformly."""
    leg = OptionLeg(
        action="buy", kind="share", strike=None, expiry=None,
        qty=100, entry_price=100.0,
    )
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=99.50, iv=0.30, today=today)
    assert price == 99.50


def test_price_leg_bs_negative_dte_returns_intrinsic():
    """Expiry already passed — BS returns intrinsic value."""
    leg = _call_leg(strike=100.0, expiry="2026-04-01")
    today = date(2026, 5, 17)
    price = chains._price_leg_bs(leg=leg, spot=105.0, iv=0.30, today=today)
    assert price == pytest.approx(5.0)


import pandas as pd


class _FakeYFTicker:
    """Mimics yfinance.Ticker minimally — just the two surface attributes
    fetch_chain uses: .options (list[str] expiries) and .option_chain(expiry)
    (returns a namespace with .calls / .puts DataFrames)."""

    def __init__(self, options_by_expiry: dict[str, tuple]):
        # options_by_expiry: {"2026-06-19": (calls_df, puts_df), ...}
        self._chains = options_by_expiry
        self.options = list(options_by_expiry.keys())

    def option_chain(self, expiry: str):
        calls_df, puts_df = self._chains[expiry]
        return SimpleNamespace(calls=calls_df, puts=puts_df)


def _make_calls_df():
    return pd.DataFrame([
        {"strike": 95.0, "bid": 6.10, "ask": 6.30, "lastPrice": 6.20,
         "impliedVolatility": 0.32, "openInterest": 420},
        {"strike": 100.0, "bid": 3.20, "ask": 3.40, "lastPrice": 3.30,
         "impliedVolatility": 0.30, "openInterest": 1850},
        {"strike": 105.0, "bid": 1.40, "ask": 1.55, "lastPrice": 1.47,
         "impliedVolatility": 0.29, "openInterest": 730},
    ])


def _make_puts_df():
    return pd.DataFrame([
        {"strike": 95.0, "bid": 0.80, "ask": 0.95, "lastPrice": 0.87,
         "impliedVolatility": 0.34, "openInterest": 510},
        {"strike": 100.0, "bid": 2.60, "ask": 2.80, "lastPrice": 2.70,
         "impliedVolatility": 0.31, "openInterest": 1240},
    ])


def test_fetch_chain_parses_yahoo_response_into_chain_quotes(conn):
    fake_ticker = _FakeYFTicker({
        "2026-06-19": (_make_calls_df(), _make_puts_df()),
    })
    fake_client = lambda symbol: fake_ticker  # noqa: E731

    result = chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000, client=fake_client,
    )
    assert result is not None
    assert result.ticker == "AAPL"
    assert result.asof_ts == 1_700_000_000
    # 3 calls + 2 puts = 5 quotes
    assert len(result.quotes) == 5

    call_at_100 = result.find_quote(expiry="2026-06-19", strike=100.0, kind="call")
    assert call_at_100 is not None
    assert call_at_100.bid == 3.20
    assert call_at_100.ask == 3.40
    assert call_at_100.last == 3.30
    assert call_at_100.iv == pytest.approx(0.30)
    assert call_at_100.oi == 1850
    assert call_at_100.source == "yahoo"


def test_fetch_chain_persists_quotes_to_v2_chain_snapshots(conn):
    fake_ticker = _FakeYFTicker({
        "2026-06-19": (_make_calls_df(), _make_puts_df()),
    })
    chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        client=lambda symbol: fake_ticker,
    )
    rows = conn.execute(
        "SELECT * FROM v2_chain_snapshots WHERE ticker='AAPL' ORDER BY expiry, strike, kind"
    ).fetchall()
    assert len(rows) == 5
    call_rows = [r for r in rows if r["kind"] == "call"]
    assert {r["strike"] for r in call_rows} == {95.0, 100.0, 105.0}
    assert all(r["source"] == "yahoo" for r in rows)


def test_fetch_chain_multi_expiry_returns_all_quotes(conn):
    fake_ticker = _FakeYFTicker({
        "2026-06-19": (_make_calls_df(), _make_puts_df()),
        "2026-07-17": (_make_calls_df(), _make_puts_df()),
    })
    result = chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        client=lambda symbol: fake_ticker,
    )
    assert len(result.quotes) == 10
    expiries = {q.expiry for q in result.quotes}
    assert expiries == {"2026-06-19", "2026-07-17"}


def test_fetch_chain_idempotent_on_re_fetch_same_asof(conn):
    """Re-fetching same (ticker, asof) overwrites prior rows — does not
    accumulate duplicates."""
    fake_ticker = _FakeYFTicker({
        "2026-06-19": (_make_calls_df(), _make_puts_df()),
    })
    chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        client=lambda symbol: fake_ticker,
    )
    chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        client=lambda symbol: fake_ticker,
    )
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM v2_chain_snapshots WHERE ticker='AAPL'"
    ).fetchone()["n"]
    assert n == 5


def test_fetch_chain_handles_nan_iv_and_zero_oi_as_none(conn):
    """yfinance sometimes returns NaN for impliedVolatility and 0 for
    openInterest on illiquid strikes. NaN → None, 0 OI → 0 (not None)."""
    calls = pd.DataFrame([
        {"strike": 100.0, "bid": 1.0, "ask": 1.2, "lastPrice": 1.1,
         "impliedVolatility": float("nan"), "openInterest": 0},
    ])
    puts = pd.DataFrame([])
    fake_ticker = _FakeYFTicker({"2026-06-19": (calls, puts)})
    result = chains.fetch_chain(
        conn=conn, ticker="XYZ", asof_ts=1_700_000_000,
        client=lambda symbol: fake_ticker,
    )
    q = result.find_quote(expiry="2026-06-19", strike=100.0, kind="call")
    assert q.iv is None
    assert q.oi == 0


def test_fetch_chain_returns_none_when_ticker_has_no_options(conn):
    """yfinance Ticker.options returns [] for tickers with no options chain."""
    fake_ticker = _FakeYFTicker({})  # no expiries
    result = chains.fetch_chain(
        conn=conn, ticker="XYZ", asof_ts=1_700_000_000,
        client=lambda symbol: fake_ticker,
    )
    assert result is None
    n = conn.execute("SELECT COUNT(*) AS n FROM v2_chain_snapshots").fetchone()["n"]
    assert n == 0


def test_fetch_chain_returns_none_when_yfinance_raises_on_construct(conn):
    """Network timeout or 5xx during yfinance.Ticker(symbol) — returns None
    and does not persist anything."""
    def raising_client(symbol):
        raise ConnectionError("simulated yahoo timeout")
    result = chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000, client=raising_client,
    )
    assert result is None
    n = conn.execute("SELECT COUNT(*) AS n FROM v2_chain_snapshots").fetchone()["n"]
    assert n == 0


def test_fetch_chain_returns_none_when_option_chain_call_raises(conn):
    """yfinance occasionally returns expiries but then raises on the
    follow-up option_chain(expiry) call. Same outcome: None, no persist."""

    class RaisingTicker:
        options = ["2026-06-19"]
        def option_chain(self, expiry):
            raise ValueError("simulated yahoo chain parse error")

    result = chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        client=lambda symbol: RaisingTicker(),
    )
    assert result is None
    n = conn.execute("SELECT COUNT(*) AS n FROM v2_chain_snapshots").fetchone()["n"]
    assert n == 0


def test_fetch_chain_partial_failure_persists_nothing(conn):
    """If the first expiry succeeds but the second raises, the entire fetch
    is treated as failed — no half-written chain in the DB."""

    class PartiallyFailingTicker:
        options = ["2026-06-19", "2026-07-17"]
        def option_chain(self, expiry):
            if expiry == "2026-06-19":
                return SimpleNamespace(calls=_make_calls_df(), puts=_make_puts_df())
            raise RuntimeError("simulated mid-fetch error")

    result = chains.fetch_chain(
        conn=conn, ticker="AAPL", asof_ts=1_700_000_000,
        client=lambda symbol: PartiallyFailingTicker(),
    )
    assert result is None
    n = conn.execute("SELECT COUNT(*) AS n FROM v2_chain_snapshots").fetchone()["n"]
    assert n == 0
