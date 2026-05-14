"""Engine B — parameter-sweep machinery.

The proposer (Engine A) returns a `StrategySpec` with parameter *ranges*;
`expand_spec` walks the cartesian product to produce a list of `Cell`s,
each a concrete parameter combination ready to feed through walk_forward.
The expansion is capped at `n_cells_max` to keep sweeps bounded — a typical
proposer spec yields ~50–200 cells; the cap (default 200) prevents a
runaway combinatorial explosion if a proposer emits an over-wide grid.

Cell iteration order is deterministic: keys are sorted alphabetically,
then `itertools.product` walks the values in input order. Replays of the
same spec produce the same cell list.
"""
from __future__ import annotations

import json
import sqlite3
import time
import traceback as _traceback
from dataclasses import dataclass
from itertools import product
from types import SimpleNamespace
from typing import Any

from bullbot.backtest import walkforward
from bullbot.leaderboard.scoring import compute_score_a
from bullbot.risk.sizing import size_strategy
from bullbot.strategies import registry


@dataclass(frozen=True)
class StrategySpec:
    class_name: str
    ranges: dict[str, list]
    max_loss_per_trade: float
    stop_loss_pct: float | None = None


@dataclass(frozen=True)
class Cell:
    class_name: str
    params: dict[str, Any]


def expand_spec(spec: StrategySpec, n_cells_max: int = 200) -> list[Cell]:
    """Return up to `n_cells_max` cells from the cartesian product of
    `spec.ranges`. Keys are sorted alphabetically for deterministic order.
    Returns an empty list when `n_cells_max < 1`."""
    if n_cells_max < 1:
        return []
    keys = sorted(spec.ranges.keys())
    cells: list[Cell] = []
    for combo in product(*(spec.ranges[k] for k in keys)):
        params = dict(zip(keys, combo))
        cells.append(Cell(class_name=spec.class_name, params=params))
        if len(cells) >= n_cells_max:
            break
    return cells


def run_cell(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    cell: Cell,
    spec: StrategySpec,
    regime_label: str,
    portfolio_value: float,
    run_id: str,
    proposer_model: str,
    iteration: int,
) -> int:
    """Run one cell through walkforward + sizer + scorer, persist the
    result row in evolver_proposals, return the new proposal_id.

    Steps:
    1. Materialize the strategy via registry.
    2. Find-or-insert the strategies row (idempotent by class/version/params_hash).
    3. Run walk-forward to get BacktestMetrics.
    4. Size the position via size_strategy.
    5. Compute score_a.
    6. INSERT into evolver_proposals.
    7. Return the new row's id (lastrowid).

    Notes:
    - ``run_id`` is accepted for API compatibility with the upcoming B.3
      dispatcher but is not persisted — evolver_proposals has no run_id column.
    - Equity strategies (``spec.stop_loss_pct is not None``) are out of scope;
      a NotImplementedError is raised if one is detected.
    - ``iteration`` is a caller-supplied counter (typically read from
      ``ticker_state.iteration_count``) that distinguishes the same
      ``(ticker, strategy)`` evaluated multiple times across sweeps. The UNIQUE
      constraint on ``evolver_proposals(ticker, iteration, strategy_id)``
      requires each sweep call for the same strategy to use a distinct
      iteration value.
    """
    if spec.stop_loss_pct is not None:
        raise NotImplementedError(
            "equity sizing requires spot lookup — handled in follow-up"
        )

    # 1. Materialize strategy
    strategy_obj = registry.materialize(cell.class_name, cell.params)

    # 2. Find-or-insert strategies row
    cls = registry.get_class(cell.class_name)
    class_version = cls.CLASS_VERSION
    canonical_params = registry.canonicalize_params(cell.params)
    p_hash = registry.params_hash(cell.params)

    existing = conn.execute(
        "SELECT id FROM strategies WHERE class_name=? AND class_version=? AND params_hash=?",
        (cell.class_name, class_version, p_hash),
    ).fetchone()

    now_ts = int(time.time())

    if existing is not None:
        strategy_id: int = existing[0]
    else:
        cur = conn.execute(
            "INSERT INTO strategies (class_name, class_version, params, params_hash, parent_id, created_at) "
            "VALUES (?, ?, ?, ?, NULL, ?)",
            (cell.class_name, class_version, canonical_params, p_hash, now_ts),
        )
        strategy_id = cur.lastrowid  # type: ignore[assignment]

    # 3. Walk-forward
    metrics = walkforward.run_walkforward(
        conn=conn,
        strategy=strategy_obj,
        strategy_id=strategy_id,
        ticker=ticker,
    )

    # 4. Size the position (options path only — equity raises above)
    sizing_input = SimpleNamespace(
        class_name=cell.class_name,
        max_loss_per_contract=spec.max_loss_per_trade,
        is_equity=False,
    )
    size = size_strategy(sizing_input, portfolio_value, max_loss_pct=0.02)

    # 5. Score
    score_a = compute_score_a(metrics.realized_pnl, metrics.max_bp_held, metrics.days_held)

    # 6. Persist
    passed_gate = 1 if size.passes_gate else 0
    regime_breakdown_json = (
        metrics.regime_breakdown
        if isinstance(metrics.regime_breakdown, str)
        else json.dumps(metrics.regime_breakdown)
    )

    cur = conn.execute(
        "INSERT INTO evolver_proposals "
        "(ticker, iteration, strategy_id, rationale, llm_cost_usd, "
        " pf_is, pf_oos, sharpe_is, max_dd_pct, trade_count, regime_breakdown, "
        " passed_gate, created_at, proposer_model, regime_label, score_a, "
        " size_units, max_loss_per_trade) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticker,
            iteration,
            strategy_id,
            None,           # rationale — LLM-generated; not applicable for sweeps
            0.0,            # llm_cost_usd — no LLM call in sweep path
            metrics.pf_is,
            metrics.pf_oos,
            metrics.sharpe_is,
            metrics.max_dd_pct,
            metrics.trade_count,
            regime_breakdown_json,
            passed_gate,
            now_ts,
            proposer_model,
            regime_label,
            score_a,
            size.size_units,
            spec.max_loss_per_trade,
        ),
    )

    # 7. Return new proposal_id
    return cur.lastrowid  # type: ignore[return-value]


def _record_sweep_failure(
    conn: sqlite3.Connection,
    ticker: str,
    cell: Cell,
    exc: BaseException,
) -> None:
    """Insert one row into sweep_failures for a cell that raised an exception."""
    conn.execute(
        "INSERT INTO sweep_failures "
        "(ts, ticker, class_name, cell_params_json, "
        " exc_type, exc_message, traceback) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            int(time.time()),
            ticker,
            cell.class_name,
            json.dumps(cell.params),
            type(exc).__name__,
            str(exc),
            _traceback.format_exc(),
        ),
    )


def sweep(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    spec: StrategySpec,
    regime_label: str,
    portfolio_value: float,
    run_id: str,
    proposer_model: str,
    n_cells_max: int = 200,
    n_jobs: int = -1,
) -> int:
    """Run every cell of `spec` through walkforward in sequence.

    For each cell:
        - On success: writes one row to `evolver_proposals` via `run_cell`.
        - On exception: writes one row to `sweep_failures` with the trace.

    Returns the count of successful proposals. `n_jobs` is accepted for API
    compatibility with the future parallel implementation (which requires
    per-worker SQLite connections and is tracked as a follow-up); currently
    sweeps run sequentially and `n_jobs` is ignored.

    The `iteration` value passed to `run_cell` is the cell's position in
    `expand_spec`'s output (0-indexed). This satisfies the UNIQUE constraint
    on `evolver_proposals(ticker, iteration, strategy_id)` since every cell
    in one sweep has a distinct iteration.
    """
    cells = expand_spec(spec, n_cells_max=n_cells_max)
    successes = 0
    for i, cell in enumerate(cells):
        try:
            run_cell(
                conn,
                ticker=ticker,
                cell=cell,
                spec=spec,
                regime_label=regime_label,
                portfolio_value=portfolio_value,
                run_id=run_id,
                proposer_model=proposer_model,
                iteration=i,
            )
            successes += 1
        except Exception as exc:
            _record_sweep_failure(conn, ticker, cell, exc)
    conn.commit()
    return successes
