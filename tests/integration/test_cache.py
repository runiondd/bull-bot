"""Cache read-through tests."""
from bullbot.data import cache
from bullbot.data.schemas import Bar


def test_get_daily_bars_caches_after_first_fetch(db_conn, fake_uw):
    from tests.conftest import FakeUWResponse
    fake_uw.register(
        "/api/stock/SPY/ohlc/1d",
        FakeUWResponse(body={
            "data": [
                {"candle_start_time": "2026-04-01T00:00:00Z", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": 100},
            ],
        }),
    )

    # First call: hits fetcher, writes to cache
    bars1 = cache.get_daily_bars(db_conn, fake_uw, "SPY", limit=10)
    assert len(bars1) == 1
    assert len(fake_uw.call_log) == 1

    # Second call: reads from cache only, no new fetch
    bars2 = cache.get_daily_bars(db_conn, fake_uw, "SPY", limit=10)
    assert len(bars2) == 1
    assert len(fake_uw.call_log) == 1   # still 1


def test_daily_bars_refresh_when_requesting_more_than_cached(db_conn, fake_uw):
    from tests.conftest import FakeUWResponse
    # Pre-seed cache with 2 bars
    db_conn.execute(
        "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
        "VALUES ('SPY','1d',1717200000,1,2,0.5,1.5,100)"
    )
    db_conn.execute(
        "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
        "VALUES ('SPY','1d',1717286400,1.5,2.5,1,2,200)"
    )

    fake_uw.register(
        "/api/stock/SPY/ohlc/1d",
        FakeUWResponse(body={
            "data": [
                {"candle_start_time": "2026-04-01T00:00:00Z", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": 100},
                {"candle_start_time": "2026-04-02T00:00:00Z", "open": "1.5", "high": "2.5", "low": "1", "close": "2", "volume": 200},
                {"candle_start_time": "2026-04-03T00:00:00Z", "open": "2", "high": "2.8", "low": "1.8", "close": "2.5", "volume": 150},
            ],
        }),
    )
    # Request more than cached → trigger fetch
    bars = cache.get_daily_bars(db_conn, fake_uw, "SPY", limit=3)
    assert len(bars) == 3
    assert len(fake_uw.call_log) == 1
