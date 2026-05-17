"""Backtest report writer for v2 Phase C.

Public entry: write_report(result, out_dir) -> dict[str, Path]
Consumes a BacktestResult from bullbot.v2.backtest.runner and writes
three CSVs (trades, equity curve, vehicle attribution) into out_dir.
Returns a mapping of report-name -> file path written.

Per spec §4.9, regime_attribution.csv + validation_summary.txt + PNG
equity curve + SPY benchmark overlay are deferred (see plan §"What
this defers").
"""
from __future__ import annotations

import csv
from datetime import datetime as _datetime
from pathlib import Path

from bullbot.v2.backtest.runner import BacktestResult

_TRADES_HEADER = [
    "ticker", "structure_kind", "intent", "opened_ts", "opened_date",
    "closed_ts", "closed_date", "close_reason", "realized_pnl", "rationale",
]


def _ts_to_date_str(ts: int) -> str:
    """Local-TZ date string for an epoch second. Matches the runner's
    23:00-local asof_ts convention (see backtest.runner.backtest)."""
    return _datetime.fromtimestamp(ts).date().isoformat()


def _write_trades_csv(result: BacktestResult, *, out_path: Path) -> None:
    """Per-trade ledger CSV. Header always written; one row per closed trade."""
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_TRADES_HEADER)
        for t in result.trades:
            w.writerow([
                t.ticker, t.structure_kind, t.intent,
                t.opened_ts, _ts_to_date_str(t.opened_ts),
                t.closed_ts, _ts_to_date_str(t.closed_ts),
                t.close_reason, t.realized_pnl, t.rationale,
            ])


_EQUITY_HEADER = ["asof_ts", "asof_date", "nav"]


def _write_equity_curve_csv(result: BacktestResult, *, out_path: Path) -> None:
    """Daily NAV snapshots CSV. Header always written; one row per daily_mtm entry."""
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_EQUITY_HEADER)
        for asof_ts, nav in result.daily_mtm:
            w.writerow([asof_ts, _ts_to_date_str(asof_ts), nav])
