"""Fetcher tests — use FakeUWClient from conftest, no real HTTP."""
import json
from pathlib import Path

import pytest

from bullbot.data import fetchers
from bullbot.data.schemas import Bar, OptionContract
from tests.conftest import FakeUWResponse


FIXTURES = Path(__file__).parent.parent / "fixtures" / "uw_responses"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_fetch_daily_ohlc_returns_bars(fake_uw):
    fake_uw.register(
        "/api/stock/SPY/ohlc/1d",
        FakeUWResponse(body=_load_fixture("spy_daily.json")),
    )
    bars = fetchers.fetch_daily_ohlc(fake_uw, "SPY", limit=100)
    assert len(bars) > 0
    assert all(isinstance(b, Bar) for b in bars)
    assert all(b.ticker == "SPY" for b in bars)
    assert all(b.timeframe == "1d" for b in bars)


def test_fetch_daily_ohlc_rejects_empty(fake_uw):
    fake_uw.register("/api/stock/SPY/ohlc/1d", FakeUWResponse(body={"data": []}))
    with pytest.raises(fetchers.DataFetchError):
        fetchers.fetch_daily_ohlc(fake_uw, "SPY", limit=100)


def test_fetch_option_historic_returns_contracts(fake_uw):
    fake_uw.register(
        "/api/option-contract/SPY260417P00666000/historic",
        FakeUWResponse(body=_load_fixture("option_historic.json")),
    )
    contracts = fetchers.fetch_option_historic(fake_uw, "SPY260417P00666000")
    assert len(contracts) > 0
    assert all(isinstance(c, OptionContract) for c in contracts)
    assert all(c.ticker == "SPY" for c in contracts)


def test_fetch_option_historic_returns_empty_on_404(fake_uw):
    fake_uw.register(
        "/api/option-contract/SPYBOGUS/historic",
        FakeUWResponse(status=404, body={"error": "not found"}),
    )
    result = fetchers.fetch_option_historic(fake_uw, "SPYBOGUS")
    assert result == []


def test_fetch_chains_snapshot_returns_symbol_list(fake_uw):
    fake_uw.register(
        "/api/stock/SPY/option-chains",
        FakeUWResponse(body=_load_fixture("spy_chains_snapshot.json")),
    )
    symbols = fetchers.fetch_chains_snapshot(fake_uw, "SPY", date="2026-04-06")
    assert isinstance(symbols, list)
    assert all(isinstance(s, str) for s in symbols)
