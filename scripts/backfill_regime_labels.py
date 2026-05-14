"""Backfill ``regime_label`` on historical ``evolver_proposals`` rows.

The strategy-search leaderboard buckets proposals by a coarse market-regime
label of the form ``"{direction}/{vol_regime}/{iv_band}"`` (e.g. ``"up/low/mid"``).
For rows generated before the label column existed (added 2026-05-14 in
``bullbot.db.migrations``), this script reconstructs the label from the
same-day ``regime_briefs`` rows and updates ``evolver_proposals`` in place.

Join key:
    same trading day (``date(ts,'unixepoch') == date(created_at,'unixepoch')``)
    + ticker match between ``evolver_proposals.ticker`` and per-ticker
    ``regime_briefs.scope``.

Label components:
    direction  — ``signals_json.spy_trend`` from the same day's
                 ``regime_briefs`` row where ``scope='market'``. Every
                 ticker on a given trading day shares the same direction.
                 Coarse but consistent with existing data.
    vol_regime — ``signals_json.vol_regime`` ∈ {"low","moderate","high"}
                 from the per-ticker brief.
    iv_band    — bucket of ``signals_json.iv_rank`` (float) using the
                 module-level cutoffs below: ``"low" | "mid" | "high"``.

Rows where no matching ticker brief OR no matching market brief exists
for that trading day stay ``regime_label IS NULL`` — we don't fabricate
direction or IV state we never observed.

Run with:
    python scripts/backfill_regime_labels.py
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bullbot import config


# ---------------------------------------------------------------------------
# IV-rank band cutoffs.
#
# Calibration attempted 2026-05-14 against cache/bullbot.db:
#   SELECT json_extract(signals_json, '$.iv_rank') FROM regime_briefs
#     WHERE scope != 'market';
#   -> n=27, all values == 50.0 (p33 == p67 == 50.0).
#
# The empirical distribution is degenerate because the live
# `compute_ticker_signals` in bullbot/features/regime_signals.py falls back
# to iv_rank=50.0 whenever IV history is unavailable (it is, today —
# bullbot has no IV history wiring yet, see lines 197-202 of that file).
#
# 33 / 67 are therefore picked as honest tercile defaults — the same
# split `regime_signals.compute_ticker_signals` uses for `vol_regime`
# (lines 231-236), so the three buckets remain conceptually parallel.
# Revisit these once real IV history lands and a re-calibration produces
# a non-degenerate empirical distribution.
IV_BAND_LOW_MAX = 33   # iv_rank <  33 -> "low"
IV_BAND_MID_MAX = 67   # iv_rank <  67 -> "mid",  >=67 -> "high"


def backfill(conn: sqlite3.Connection) -> int:
    """Populate ``regime_label`` on every NULL ``evolver_proposals`` row
    that has a matching same-day per-ticker brief AND same-day market brief.

    Returns the number of rows actually labelled (``cursor.rowcount``).
    Rows with no matching pair of briefs are skipped — their label stays
    NULL.
    """
    # The inner SELECT computes the label for the proposal's (ticker, day).
    # We restrict the outer UPDATE to rows where that same (ticker, day) pair
    # actually has BOTH a per-ticker brief AND a same-day market brief, so
    # the returned rowcount reflects rows actually labelled. Without the
    # EXISTS clause SQLite would still "update" rows whose subquery returns
    # NULL (a no-op write of NULL onto NULL), inflating rowcount past the
    # rows we really touched.
    sql = """
        UPDATE evolver_proposals
        SET regime_label = (
            SELECT
                json_extract(mb.signals_json, '$.spy_trend')
                || '/' ||
                json_extract(tb.signals_json, '$.vol_regime')
                || '/' ||
                CASE
                    WHEN CAST(json_extract(tb.signals_json, '$.iv_rank') AS REAL) < ?
                        THEN 'low'
                    WHEN CAST(json_extract(tb.signals_json, '$.iv_rank') AS REAL) < ?
                        THEN 'mid'
                    ELSE 'high'
                END
            FROM regime_briefs tb
            JOIN regime_briefs mb
              ON mb.scope = 'market'
             AND date(mb.ts, 'unixepoch') = date(tb.ts, 'unixepoch')
            WHERE tb.scope = evolver_proposals.ticker
              AND date(tb.ts, 'unixepoch')
                  = date(evolver_proposals.created_at, 'unixepoch')
            LIMIT 1
        )
        WHERE regime_label IS NULL
          AND EXISTS (
            SELECT 1
            FROM regime_briefs tb
            JOIN regime_briefs mb
              ON mb.scope = 'market'
             AND date(mb.ts, 'unixepoch') = date(tb.ts, 'unixepoch')
            WHERE tb.scope = evolver_proposals.ticker
              AND date(tb.ts, 'unixepoch')
                  = date(evolver_proposals.created_at, 'unixepoch')
          )
    """
    cur = conn.execute(sql, (IV_BAND_LOW_MAX, IV_BAND_MID_MAX))
    conn.commit()
    return cur.rowcount


log = logging.getLogger("backfill_regime_labels")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    conn = sqlite3.connect(str(config.DB_PATH))
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM evolver_proposals WHERE regime_label IS NULL"
        ).fetchone()[0]
        touched = backfill(conn)
        after = conn.execute(
            "SELECT COUNT(*) FROM evolver_proposals WHERE regime_label IS NULL"
        ).fetchone()[0]
        log.info(
            "backfill complete: before=%d nulls, touched=%d, after=%d nulls "
            "(rows still NULL had no same-day market+ticker brief pair)",
            before, touched, after,
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
