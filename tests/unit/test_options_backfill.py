"""Option symbol enumeration tests."""
from datetime import date

from bullbot.data import options_backfill


def test_format_osi_symbol():
    sym = options_backfill.format_osi_symbol(
        ticker="SPY", expiry=date(2024, 6, 21), strike=540.0, kind="P"
    )
    assert sym == "SPY240621P00540000"


def test_format_osi_symbol_fractional_strike():
    sym = options_backfill.format_osi_symbol(
        ticker="SPY", expiry=date(2024, 6, 21), strike=540.5, kind="C"
    )
    assert sym == "SPY240621C00540500"


def test_enumerate_expiries_includes_fridays():
    expiries = options_backfill.enumerate_expiries(
        start=date(2024, 6, 1), end=date(2024, 6, 30)
    )
    fridays = [d for d in expiries if d.weekday() == 4]
    assert date(2024, 6, 7) in fridays
    assert date(2024, 6, 14) in fridays
    assert date(2024, 6, 21) in fridays
    assert date(2024, 6, 28) in fridays


def test_enumerate_strikes_around_spot():
    strikes = options_backfill.enumerate_strikes_around_spot(
        spot=540.0, range_fraction=0.20, step=1.0
    )
    assert min(strikes) >= 432.0  # spot * 0.8
    assert max(strikes) <= 648.0  # spot * 1.2
    assert 540.0 in strikes
    assert len(strikes) > 50


def test_build_candidate_symbols_count_sanity():
    symbols = options_backfill.build_candidate_symbols(
        ticker="SPY",
        spot=540.0,
        backfill_start=date(2024, 1, 1),
        backfill_end=date(2024, 1, 31),
        strike_range_fraction=0.10,
        strike_step=5.0,
    )
    assert 50 < len(symbols) < 500
    assert all(s.startswith("SPY") for s in symbols)
