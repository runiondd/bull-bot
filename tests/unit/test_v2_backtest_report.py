"""Unit tests for bullbot.v2.backtest.report."""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

import pytest

from bullbot.v2.backtest import report
from bullbot.v2.backtest.runner import BacktestResult, BacktestTrade


def _trade(**overrides) -> BacktestTrade:
    defaults = dict(
        ticker="AAPL", structure_kind="long_call", intent="trade",
        opened_ts=int(datetime(2024, 1, 5, 23, 0).timestamp()),
        closed_ts=int(datetime(2024, 1, 12, 23, 0).timestamp()),
        close_reason="profit_target", realized_pnl=125.50,
        rationale="bullish breakout",
    )
    defaults.update(overrides)
    return BacktestTrade(**defaults)


def _result(trades=None, daily_mtm=None) -> BacktestResult:
    return BacktestResult(
        ticker="AAPL", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
        starting_nav=50_000.0, ending_nav=50_000.0,
        trades=trades or [], daily_mtm=daily_mtm or [],
    )


def test_write_trades_csv_writes_header_only_for_empty_trades(tmp_path):
    out = tmp_path / "trades.csv"
    report._write_trades_csv(_result(trades=[]), out_path=out)
    with out.open() as f:
        rows = list(csv.reader(f))
    assert len(rows) == 1
    assert rows[0] == [
        "ticker", "structure_kind", "intent", "opened_ts", "opened_date",
        "closed_ts", "closed_date", "close_reason", "realized_pnl", "rationale",
    ]


def test_write_trades_csv_writes_one_row_per_trade(tmp_path):
    out = tmp_path / "trades.csv"
    trades = [
        _trade(realized_pnl=100.0),
        _trade(structure_kind="csp", intent="accumulate",
               close_reason="expired_worthless", realized_pnl=200.0),
    ]
    report._write_trades_csv(_result(trades=trades), out_path=out)
    with out.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["structure_kind"] == "long_call"
    assert rows[0]["realized_pnl"] == "100.0"
    assert rows[1]["structure_kind"] == "csp"
    assert rows[1]["intent"] == "accumulate"


def test_write_trades_csv_includes_human_readable_dates(tmp_path):
    out = tmp_path / "trades.csv"
    report._write_trades_csv(_result(trades=[_trade()]), out_path=out)
    with out.open() as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["opened_date"] == "2024-01-05"
    assert rows[0]["closed_date"] == "2024-01-12"


def test_write_equity_curve_csv_writes_header_only_for_empty_mtm(tmp_path):
    out = tmp_path / "equity.csv"
    report._write_equity_curve_csv(_result(daily_mtm=[]), out_path=out)
    with out.open() as f:
        rows = list(csv.reader(f))
    assert rows == [["asof_ts", "asof_date", "nav"]]


def test_write_equity_curve_csv_writes_one_row_per_day(tmp_path):
    out = tmp_path / "equity.csv"
    daily_mtm = [
        (int(datetime(2024, 3, 13, 23).timestamp()), 50_000.0),
        (int(datetime(2024, 3, 14, 23).timestamp()), 50_125.50),
        (int(datetime(2024, 3, 15, 23).timestamp()), 49_800.0),
    ]
    report._write_equity_curve_csv(_result(daily_mtm=daily_mtm), out_path=out)
    with out.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    assert rows[0]["asof_date"] == "2024-03-13"
    assert rows[1]["nav"] == "50125.5"
    assert rows[2]["asof_date"] == "2024-03-15"


def test_write_equity_curve_csv_preserves_chronological_order(tmp_path):
    out = tmp_path / "equity.csv"
    daily_mtm = [
        (int(datetime(2024, 3, 13, 23).timestamp()), 50_000.0),
        (int(datetime(2024, 3, 14, 23).timestamp()), 50_500.0),
    ]
    report._write_equity_curve_csv(_result(daily_mtm=daily_mtm), out_path=out)
    with out.open() as f:
        rows = list(csv.DictReader(f))
    assert int(rows[0]["asof_ts"]) < int(rows[1]["asof_ts"])
