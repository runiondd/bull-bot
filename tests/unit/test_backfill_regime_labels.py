"""Tests for scripts.backfill_regime_labels.

The backfill joins each `evolver_proposals` row to the same trading-day
per-ticker `regime_briefs` row (and same-day market brief for direction)
and writes a label string of the form ``"{spy_trend}/{vol_regime}/{iv_band}"``
into ``evolver_proposals.regime_label``. Rows where either brief is
missing for that day stay NULL.
"""

import json
import sqlite3

from bullbot.db.migrations import apply_schema
from scripts.backfill_regime_labels import (
    IV_BAND_LOW_MAX,
    IV_BAND_MID_MAX,
    backfill,
)


# 2026-05-13 UTC midnight epoch — used as a stable trading-day timestamp
# for all fixtures below. Both proposal.created_at and the briefs' ts
# are set to this value so the date(ts,'unixepoch') join key matches.
_TRADING_DAY_TS = 1747094400


def _seed_strategy(conn: sqlite3.Connection, strategy_id: int = 1) -> None:
    """Insert a minimal valid strategies row so the FK from
    evolver_proposals.strategy_id resolves."""
    conn.execute(
        "INSERT INTO strategies "
        "(id, class_name, class_version, params, params_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (strategy_id, "BullCallSpread", 1, "{}", "h", _TRADING_DAY_TS),
    )


def _seed_proposal(
    conn: sqlite3.Connection,
    *,
    proposal_id: int = 1,
    ticker: str = "META",
    strategy_id: int = 1,
    created_at: int = _TRADING_DAY_TS,
) -> None:
    conn.execute(
        "INSERT INTO evolver_proposals "
        "(id, ticker, iteration, strategy_id, llm_cost_usd, "
        " passed_gate, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (proposal_id, ticker, 1, strategy_id, 0.0, 0, created_at),
    )


def _seed_market_brief(
    conn: sqlite3.Connection,
    *,
    ts: int = _TRADING_DAY_TS,
    spy_trend: str = "up",
) -> None:
    payload = {"spy_trend": spy_trend, "vix_level": 18.0}
    conn.execute(
        "INSERT INTO regime_briefs "
        "(scope, ts, signals_json, brief_text, model, cost_usd, "
        " source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("market", ts, json.dumps(payload), "", "test", 0.0, "test", ts),
    )


def _seed_ticker_brief(
    conn: sqlite3.Connection,
    *,
    ticker: str = "META",
    ts: int = _TRADING_DAY_TS,
    vol_regime: str = "low",
    iv_rank: float = 10.0,
) -> None:
    payload = {
        "ticker": ticker,
        "iv_rank": iv_rank,
        "iv_percentile": iv_rank,
        "sector_relative": 0.0,
        "vol_regime": vol_regime,
        "sector_etf": None,
    }
    conn.execute(
        "INSERT INTO regime_briefs "
        "(scope, ts, signals_json, brief_text, model, cost_usd, "
        " source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ticker, ts, json.dumps(payload), "", "test", 0.0, "test", ts),
    )


def _fresh_conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "test.db")
    apply_schema(conn)
    return conn


def test_backfill_labels_proposal_from_matching_briefs(tmp_path):
    """Happy path: one proposal + matching market + ticker brief on the
    same trading day → one labelled row, label = 'up/low/low'."""
    conn = _fresh_conn(tmp_path)
    _seed_strategy(conn)
    _seed_proposal(conn)
    _seed_market_brief(conn, spy_trend="up")
    # iv_rank=10 sits safely below IV_BAND_LOW_MAX so it buckets as "low"
    _seed_ticker_brief(conn, vol_regime="low", iv_rank=10.0)
    assert 10.0 < IV_BAND_LOW_MAX, "fixture iv_rank must be inside low band"
    conn.commit()

    n = backfill(conn)

    assert n == 1
    label = conn.execute(
        "SELECT regime_label FROM evolver_proposals WHERE id=1"
    ).fetchone()[0]
    assert label == "up/low/low"


def test_backfill_leaves_label_null_when_no_market_brief(tmp_path):
    """No market brief for the day → label stays NULL, rowcount is 0."""
    conn = _fresh_conn(tmp_path)
    _seed_strategy(conn)
    _seed_proposal(conn)
    # Seed only the per-ticker brief, no market brief.
    _seed_ticker_brief(conn, vol_regime="low", iv_rank=10.0)
    conn.commit()

    n = backfill(conn)

    assert n == 0
    label = conn.execute(
        "SELECT regime_label FROM evolver_proposals WHERE id=1"
    ).fetchone()[0]
    assert label is None


def test_backfill_is_idempotent(tmp_path):
    """Running backfill twice on the same data labels each row once;
    the second call must report 0 rows touched because the WHERE clause
    filters out already-labelled rows."""
    conn = _fresh_conn(tmp_path)
    _seed_strategy(conn)
    _seed_proposal(conn)
    _seed_market_brief(conn, spy_trend="up")
    _seed_ticker_brief(conn, vol_regime="low", iv_rank=10.0)
    conn.commit()

    first = backfill(conn)
    second = backfill(conn)

    assert first == 1
    assert second == 0
    label = conn.execute(
        "SELECT regime_label FROM evolver_proposals WHERE id=1"
    ).fetchone()[0]
    assert label == "up/low/low"
