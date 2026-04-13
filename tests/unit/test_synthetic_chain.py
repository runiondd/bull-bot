import math
import pytest
from bullbot.data.schemas import Bar


def _make_bars(closes: list[float], base_ts: int = 86400 * 100) -> list[Bar]:
    return [
        Bar(ticker="TSLA", timeframe="1d", ts=base_ts + 86400 * i,
            open=c, high=c + 1, low=c - 1, close=c, volume=1000000, source="yahoo")
        for i, c in enumerate(closes)
    ]


def test_realized_vol_constant_prices():
    from bullbot.data.synthetic_chain import realized_vol
    bars = _make_bars([100.0] * 31)
    vol = realized_vol(bars)
    assert vol == 0.0


def test_realized_vol_trending():
    from bullbot.data.synthetic_chain import realized_vol
    closes = [100.0 * (1.001 ** i) for i in range(31)]
    bars = _make_bars(closes)
    vol = realized_vol(bars)
    assert 0.0 < vol < 0.10


def test_realized_vol_fallback_short_bars():
    from bullbot.data.synthetic_chain import realized_vol
    bars = _make_bars([100.0] * 10)
    vol = realized_vol(bars)
    assert vol == 0.30


def test_bs_call_atm():
    from bullbot.data.synthetic_chain import bs_price
    price = bs_price(spot=100.0, strike=100.0, t_years=1.0, vol=0.30, r=0.045, kind="C")
    assert 12.0 < price < 15.0


def test_bs_put_atm():
    from bullbot.data.synthetic_chain import bs_price
    price = bs_price(spot=100.0, strike=100.0, t_years=1.0, vol=0.30, r=0.045, kind="P")
    assert 7.0 < price < 11.0


def test_bs_call_deep_itm():
    from bullbot.data.synthetic_chain import bs_price
    price = bs_price(spot=100.0, strike=50.0, t_years=1.0, vol=0.30, r=0.045, kind="C")
    assert price > 48.0


def test_bs_put_deep_otm():
    from bullbot.data.synthetic_chain import bs_price
    price = bs_price(spot=100.0, strike=50.0, t_years=1.0, vol=0.30, r=0.045, kind="P")
    assert price < 1.0


def test_bs_zero_time():
    from bullbot.data.synthetic_chain import bs_price
    call_itm = bs_price(spot=100.0, strike=90.0, t_years=0.0, vol=0.30, r=0.045, kind="C")
    assert abs(call_itm - 10.0) < 0.01
    put_itm = bs_price(spot=100.0, strike=110.0, t_years=0.0, vol=0.30, r=0.045, kind="P")
    assert abs(put_itm - 10.0) < 0.01
