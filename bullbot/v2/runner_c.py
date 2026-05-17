"""v2 Phase C daily forward-mode dispatcher.

Sibling to bullbot.v2.runner (Phase A signal loop). Walks config.UNIVERSE
once per day, runs the full Phase C agent pipeline (signal → S/R → earnings
→ exits-on-held → vehicle.pick on flat → validate → open → MtM), persists
results, and writes one v2_position_mtm row per open position.

Per spec §4.2.
"""
from __future__ import annotations

import logging
import sqlite3

_log = logging.getLogger(__name__)


def _write_position_mtm(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    asof_ts: int,
    mtm_value: float,
    source: str,
) -> None:
    """Idempotent write to v2_position_mtm. PK is (position_id, asof_ts);
    INSERT OR REPLACE so re-running the daily MtM step overwrites cleanly."""
    conn.execute(
        "INSERT OR REPLACE INTO v2_position_mtm "
        "(position_id, asof_ts, mtm_value, source) VALUES (?, ?, ?, ?)",
        (position_id, asof_ts, mtm_value, source),
    )
    conn.commit()
