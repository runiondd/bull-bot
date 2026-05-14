"""Engine C — Grid Baseline (weekly Sundays).

The strategy-search "honesty check": no LLM, just a hardcoded
class x ticker x parameter-cell grid fed straight through Engine B
(``bullbot.evolver.sweep.sweep``). Every row this writes to
``evolver_proposals`` is tagged ``proposer_model='grid:baseline'`` and
``regime_label='grid:baseline'`` so downstream leaderboard / mentor-brief
queries can include or exclude the control group cleanly.

If, over a rolling 4-week window, the LLM-proposed search doesn't beat
this grid baseline on gate-pass rate, the mentor brief escalates a flag
for a human decision.

Usage::

    python scripts/grid_baseline.py                # full UNIVERSE, full grid
    python scripts/grid_baseline.py --tickers META SPY  # ad-hoc subset

The script is designed to be invoked weekly (Sundays, off-market) by a
cron-style scheduler. Cron registration is *not* this script's job — it
is plain `python scripts/grid_baseline.py`.

Exit codes:
  * 0 — script ran to completion (per-pair sweep failures are logged but
    do not change the exit code; Engine B records them in
    ``sweep_failures`` for later inspection).
  * 1 — hard infrastructure failure (DB open, etc.).
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bullbot import config
from bullbot.db import connection as db_connection
from bullbot.evolver.sweep import StrategySpec, sweep


log = logging.getLogger("grid_baseline")


# Total cell count = sum over classes of product(len(v) for v in ranges.values())
# Current grid:
#   PutCreditSpread:   4 short_delta x 2 width x 3 dte x 3 iv_rank_min x 1 pt x 1 sl  = 72
#   CallCreditSpread:  4 short_delta x 2 width x 3 dte x 3 iv_rank_min x 1 pt x 1 sl  = 72
#   IronCondor:        3 wing_delta  x 2 wing_width x 3 dte x 3 iv_rank_min x 1 x 1   = 54
# Per ticker: 198 cells.  UNIVERSE = 20 -> ~3960 cells / week.
# Below the spec's ~9.6k ceiling, leaves headroom to add a 4th class later.
GRID: dict[str, dict] = {
    "PutCreditSpread": {
        "max_loss_per_trade": 350.0,
        "ranges": {
            "short_delta": [0.15, 0.20, 0.25, 0.30],
            "width": [5, 10],
            "dte": [21, 30, 45],
            "iv_rank_min": [10, 20, 30],
            "profit_target_pct": [0.50],
            "stop_loss_mult": [2.0],
        },
    },
    "CallCreditSpread": {
        "max_loss_per_trade": 350.0,
        "ranges": {
            "short_delta": [0.15, 0.20, 0.25, 0.30],
            "width": [5, 10],
            "dte": [21, 30, 45],
            "iv_rank_min": [10, 20, 30],
            "profit_target_pct": [0.50],
            "stop_loss_mult": [2.0],
        },
    },
    "IronCondor": {
        "max_loss_per_trade": 400.0,
        "ranges": {
            "wing_delta": [0.15, 0.20, 0.25],
            "wing_width": [5, 10],
            "dte": [21, 30, 45],
            "iv_rank_min": [20, 30, 40],
            "profit_target_pct": [0.50],
            "stop_loss_mult": [2.0],
        },
    },
}


# Engine C is regime-agnostic; the literal "grid:baseline" tag lets us
# filter the control group out (or in) with a simple WHERE clause.
PROPOSER_MODEL_TAG = "grid:baseline"
REGIME_LABEL_TAG = "grid:baseline"

# Dan's combined account size (income + growth). Matches the value used in the
# E.3 fixture and other end-to-end paths that need a portfolio_value > 0.
PORTFOLIO_VALUE = config.INITIAL_CAPITAL_USD + config.GROWTH_CAPITAL_USD


def run_grid(
    conn,
    *,
    grid: dict[str, dict] = GRID,
    universe: list[str] | None = None,
    today: date | None = None,
) -> int:
    """Run every (class, ticker) pair in `grid` x `universe` through
    Engine B's ``sweep``. Per-pair exceptions are isolated and logged;
    they do not abort subsequent pairs.

    Returns the sum of ``sweep`` return values (i.e. total successful
    proposals written to ``evolver_proposals``). On a per-pair failure
    that proposal count is 0 (the cells never made it to sweep at all).
    """
    if universe is None:
        universe = list(config.UNIVERSE)
    if today is None:
        today = date.today()

    run_id = f"grid:baseline:{today.isoformat()}"

    total_written = 0
    n_pairs = len(grid) * len(universe)
    pair_idx = 0
    for class_name, class_grid in grid.items():
        spec = StrategySpec(
            class_name=class_name,
            ranges=class_grid["ranges"],
            max_loss_per_trade=class_grid["max_loss_per_trade"],
        )
        for ticker in universe:
            pair_idx += 1
            log.info(
                "grid pair %d/%d: %s x %s",
                pair_idx, n_pairs, class_name, ticker,
            )
            try:
                written = sweep(
                    conn,
                    ticker=ticker,
                    spec=spec,
                    regime_label=REGIME_LABEL_TAG,
                    portfolio_value=PORTFOLIO_VALUE,
                    run_id=run_id,
                    proposer_model=PROPOSER_MODEL_TAG,
                )
            except Exception:
                # Per-pair isolation. Engine B's run_cell already records
                # per-cell failures into sweep_failures; this catches the
                # rarer case where sweep itself blows up (e.g. spec build
                # error, registry mismatch) before any cell runs.
                log.exception(
                    "grid pair failed: class=%s ticker=%s",
                    class_name, ticker,
                )
                continue
            log.info(
                "grid pair %d/%d done: %s x %s -> %d proposals",
                pair_idx, n_pairs, class_name, ticker, written,
            )
            total_written += written

    return total_written


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tickers",
        nargs="*",
        default=None,
        help="optional subset of tickers (default: bullbot.config.UNIVERSE)",
    )
    args = parser.parse_args(argv)

    universe = args.tickers if args.tickers else list(config.UNIVERSE)

    conn = db_connection.open_persistent_connection(config.DB_PATH)
    try:
        log.info(
            "grid baseline start: %d classes x %d tickers",
            len(GRID), len(universe),
        )
        total = run_grid(conn, grid=GRID, universe=universe)
        log.info("grid baseline complete: %d total proposals written", total)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
