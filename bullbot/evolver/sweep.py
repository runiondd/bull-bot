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

from dataclasses import dataclass
from itertools import product
from typing import Any


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
