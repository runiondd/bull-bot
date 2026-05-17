"""Unit tests for bullbot.v2.backtest.synth_chain."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from bullbot.v2.backtest import synth_chain


def _bar(close, high=None, low=None, ts=0):
    return SimpleNamespace(
        ts=ts, open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=1_000_000,
    )


def test_event_day_multiplier_returns_1_for_steady_bars():
    """No qualifying event in the last 5 bars -> multiplier = 1.0."""
    bars = [_bar(close=100.0 + i * 0.01) for i in range(30)]  # tiny drift
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == 1.0


def test_event_day_multiplier_returns_175_on_day_of_event():
    """A 5% spike on the most recent bar -> multiplier = 1.75 (event_age=0)."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    bars[-1] = _bar(close=105.0, high=106.0, low=99.0)  # 5% spike on last bar
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == pytest.approx(1.75, abs=0.01)


def test_event_day_multiplier_decays_linearly_back_to_1():
    """Event was 2 days ago: multiplier = 1.0 + 0.75 × (5 - 2)/5 = 1.45.
    Hold close=105 from bars[-3] onward so the reversion doesn't itself count."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    bars[-3] = _bar(close=105.0, high=106.0, low=99.0)  # spike 2 days ago
    bars[-2] = _bar(close=105.0, high=105.5, low=104.5)
    bars[-1] = _bar(close=105.0, high=105.5, low=104.5)
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == pytest.approx(1.0 + 0.75 * (5 - 2) / 5, abs=0.01)


def test_event_day_multiplier_returns_1_after_5_day_decay():
    """Event was 6 days ago and prices held — no revert event in lookback."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    bars[-6] = _bar(close=105.0, high=106.0, low=99.0)  # 5 days ago (outside lookback)
    # Hold the new level so no revert event lands inside lookback
    for i in range(5, 0, -1):
        bars[-i] = _bar(close=105.0, high=105.5, low=104.5)
    # Re-set bars[-6] (the loop above overwrote it; restore the spike)
    bars[-6] = _bar(close=105.0, high=106.0, low=99.0)
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == 1.0


def test_event_day_multiplier_uses_true_range_rule():
    """Big TR on otherwise-flat close: TR rule fires even when return < 3%."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    # day at idx -1: close back to 100 but high/low blown out
    bars[-1] = _bar(close=100.0, high=110.0, low=90.0)
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == pytest.approx(1.75, abs=0.01)


def test_event_day_multiplier_picks_most_recent_event_when_multiple():
    """Two events in lookback: the more recent one wins (highest multiplier).
    Hold close at each new level so reversions don't create phantom events."""
    bars = [_bar(close=100.0, high=100.5, low=99.5) for _ in range(30)]
    # Event 4 days ago: jump to 110
    bars[-5] = _bar(close=110.0, high=112.0, low=98.0)
    # Hold 110 until next event
    bars[-4] = _bar(close=110.0, high=110.5, low=109.5)
    bars[-3] = _bar(close=110.0, high=110.5, low=109.5)
    # Event 1 day ago: jump back to ~105 (4.5% drop from 110 -> qualifies as event)
    bars[-2] = _bar(close=105.0, high=106.0, low=99.0)
    # Hold 105
    bars[-1] = _bar(close=105.0, high=105.5, low=104.5)
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    # 1 day ago -> 1.0 + 0.75 * (5-1)/5 = 1.60
    assert mult == pytest.approx(1.0 + 0.75 * 4 / 5, abs=0.01)


def test_event_day_multiplier_returns_1_for_too_few_bars():
    """Need at least ATR_WINDOW + 1 = 15 bars for ATR computation."""
    bars = [_bar(close=100.0) for _ in range(10)]
    mult = synth_chain._event_day_iv_multiplier(bars=bars, lookback=5)
    assert mult == 1.0


def _alternating_bars(n=60, base=100.0, pct=0.01):
    """n bars alternating ±pct%. Produces non-zero realized vol."""
    return [_bar(close=base * (1 + pct * ((-1) ** i)),
                 high=base * (1 + pct * ((-1) ** i)) + 0.5,
                 low=base * (1 + pct * ((-1) ** i)) - 0.5)
            for i in range(n)]


def test_synth_iv_returns_proxy_when_no_event_in_window():
    """Steady alternating bars + flat VIX → multiplier = 1.0,
    so _synth_iv equals chains._iv_proxy."""
    from bullbot.v2 import chains
    underlying = _alternating_bars()
    vix = [_bar(close=18.0) for _ in range(60)]
    proxy = chains._iv_proxy(underlying_bars=underlying, vix_bars=vix)
    synth = synth_chain._synth_iv(underlying_bars=underlying, vix_bars=vix)
    assert synth == pytest.approx(proxy, abs=0.001)


def test_synth_iv_inflates_when_recent_event_present():
    """Event on last bar → synth = proxy × 1.75 (subject to chains' [0.05, 3.0] clamp)."""
    from bullbot.v2 import chains
    underlying = _alternating_bars()
    underlying[-1] = _bar(close=120.0, high=121.0, low=118.0)  # ~20% spike
    vix = [_bar(close=18.0) for _ in range(60)]
    proxy = chains._iv_proxy(underlying_bars=underlying, vix_bars=vix)
    synth = synth_chain._synth_iv(underlying_bars=underlying, vix_bars=vix)
    expected = min(3.0, proxy * 1.75)
    assert synth == pytest.approx(expected, abs=0.01)


def test_synth_iv_clamps_to_iv_proxy_max():
    """Pathological inputs: proxy at ceiling (3.0) × 1.75 must still clamp to 3.0."""
    from bullbot.v2 import chains
    # Underlying with massive realized vol to push proxy near top of range
    underlying = [_bar(close=100.0 * (1 + 0.15 * ((-1) ** i))) for i in range(60)]
    vix_bars = [_bar(close=10.0)] * 59 + [_bar(close=80.0)]  # 8x regime spike
    underlying[-1] = _bar(close=130.0, high=132.0, low=125.0)  # event today too
    synth = synth_chain._synth_iv(underlying_bars=underlying, vix_bars=vix_bars)
    assert synth == chains.IV_PROXY_MAX  # 3.0


def test_strikes_in_band_keeps_within_10pct_of_spot():
    spot = 100.0
    strikes = [85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0]
    out = synth_chain._strikes_in_band(strikes=strikes, spot=spot)
    # ±10% = [90, 110] inclusive
    assert out == [90.0, 95.0, 100.0, 105.0, 110.0]


def test_strikes_in_band_rejects_zero_or_negative_spot():
    assert synth_chain._strikes_in_band(strikes=[100.0], spot=0.0) == []
    assert synth_chain._strikes_in_band(strikes=[100.0], spot=-1.0) == []


def test_strikes_in_band_returns_empty_when_input_empty():
    assert synth_chain._strikes_in_band(strikes=[], spot=100.0) == []


def test_dtes_in_band_keeps_21_to_365():
    today = date(2026, 5, 17)
    expiries = [
        "2026-05-25",  # 8 DTE — too short
        "2026-06-19",  # 33 DTE — in band
        "2026-09-19",  # 125 DTE — in band
        "2027-05-21",  # 369 DTE — too long
    ]
    out = synth_chain._dtes_in_band(expiries=expiries, today=today)
    assert out == ["2026-06-19", "2026-09-19"]


def test_dtes_in_band_includes_boundary_values_inclusive():
    today = date(2026, 5, 17)
    # 21 DTE = today + 21 days = 2026-06-07
    # 365 DTE = today + 365 days = 2027-05-17
    expiries = ["2026-06-07", "2027-05-17"]
    out = synth_chain._dtes_in_band(expiries=expiries, today=today)
    assert out == ["2026-06-07", "2027-05-17"]


def test_dtes_in_band_handles_malformed_expiry_gracefully():
    today = date(2026, 5, 17)
    expiries = ["2026-06-19", "not-a-date", "2026-09-19"]
    out = synth_chain._dtes_in_band(expiries=expiries, today=today)
    assert out == ["2026-06-19", "2026-09-19"]


def test_synthesize_returns_chain_with_quotes_for_each_strike_x_expiry():
    """3 strikes × 2 expiries × 2 kinds (call + put) = 12 quotes."""
    underlying = _alternating_bars()
    vix = [_bar(close=18.0) for _ in range(60)]
    chain = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=underlying, vix_bars=vix,
        expiries=["2026-06-19", "2026-09-19"],
        strikes=[95.0, 100.0, 105.0],
    )
    assert chain.ticker == "AAPL"
    assert chain.asof_ts == 1_700_000_000
    assert len(chain.quotes) == 12  # 3 × 2 × 2


def test_synthesize_filters_strikes_outside_10pct_band():
    underlying = _alternating_bars()
    vix = [_bar(close=18.0) for _ in range(60)]
    chain = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=underlying, vix_bars=vix,
        expiries=["2026-06-19"],
        strikes=[80.0, 95.0, 100.0, 105.0, 120.0],  # 80 + 120 outside band
    )
    in_band_strikes = {q.strike for q in chain.quotes}
    assert in_band_strikes == {95.0, 100.0, 105.0}


def test_synthesize_filters_expiries_outside_21_365_dte():
    underlying = _alternating_bars()
    vix = [_bar(close=18.0) for _ in range(60)]
    chain = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=underlying, vix_bars=vix,
        expiries=["2026-05-25", "2026-06-19", "2027-09-19"],  # 8d, 33d, 489d
        strikes=[100.0],
    )
    in_band_expiries = {q.expiry for q in chain.quotes}
    assert in_band_expiries == {"2026-06-19"}


def test_synthesize_quotes_are_bs_priced_with_source_bs():
    """Each quote has bid=ask=last=BS_price and source='bs'."""
    underlying = _alternating_bars()
    vix = [_bar(close=18.0) for _ in range(60)]
    chain = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=underlying, vix_bars=vix,
        expiries=["2026-06-19"], strikes=[100.0],
    )
    for q in chain.quotes:
        assert q.source == "bs"
        assert q.bid == q.ask == q.last
        assert q.bid > 0  # ATM near-term option should have non-zero premium
        assert q.iv is not None


def test_synthesize_returns_empty_chain_when_all_strikes_filtered_out():
    underlying = _alternating_bars()
    vix = [_bar(close=18.0) for _ in range(60)]
    chain = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=underlying, vix_bars=vix,
        expiries=["2026-06-19"], strikes=[50.0, 200.0],  # both way outside band
    )
    assert chain.quotes == []


def test_synthesize_event_day_inflates_quote_iv_vs_steady_day():
    """Same setup with vs without event in the last 5 bars: IV should differ."""
    steady = _alternating_bars()
    spike = _alternating_bars()
    spike[-1] = _bar(close=120.0, high=121.0, low=119.0)
    vix = [_bar(close=18.0) for _ in range(60)]
    chain_steady = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=steady, vix_bars=vix,
        expiries=["2026-06-19"], strikes=[100.0],
    )
    chain_spike = synth_chain.synthesize(
        ticker="AAPL", asof_ts=1_700_000_000,
        today=date(2026, 5, 17), spot=100.0,
        underlying_bars=spike, vix_bars=vix,
        expiries=["2026-06-19"], strikes=[100.0],
    )
    iv_steady = chain_steady.quotes[0].iv
    iv_spike = chain_spike.quotes[0].iv
    assert iv_spike > iv_steady * 1.5  # bump fired
