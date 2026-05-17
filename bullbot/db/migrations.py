"""Schema loader.

`apply_schema` is idempotent: safe to re-run on an existing DB. It applies
the fresh-DB schema via `CREATE TABLE IF NOT EXISTS` and then a small set
of column-level migrations for columns added after the initial schema.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def apply_schema(conn: sqlite3.Connection) -> None:
    sql = SCHEMA_PATH.read_text()
    conn.executescript(sql)
    _apply_column_migrations(conn)
    conn.execute("PRAGMA foreign_keys=ON")  # ensure FK enforcement persists
    conn.commit()


def _apply_column_migrations(conn: sqlite3.Connection) -> None:
    """Idempotently add columns that were introduced after the initial schema.

    Each block checks whether the column is present via `PRAGMA table_info`
    and only issues the ALTER if missing. Safe to re-run.
    """
    # positions.unrealized_pnl — added 2026-04-23 alongside the daily
    # mark-to-market refresh (see bullbot.engine.exit_manager).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    if "unrealized_pnl" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN unrealized_pnl REAL")

    # evolver_proposals.proposer_model — added 2026-04-27 for the Phase 2
    # Opus-vs-Sonnet A/B harness. NULL on legacy rows.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(evolver_proposals)")}
    if "proposer_model" not in cols:
        conn.execute("ALTER TABLE evolver_proposals ADD COLUMN proposer_model TEXT")

    # evolver_proposals.regime_label — added 2026-05-14 for strategy-search-engine
    # Phase A. Stores the market-regime label (e.g. 'trending', 'range') active
    # during this proposal's evaluation window.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(evolver_proposals)")}
    if "regime_label" not in cols:
        conn.execute("ALTER TABLE evolver_proposals ADD COLUMN regime_label TEXT")

    # evolver_proposals.score_a — added 2026-05-14 for strategy-search-engine
    # Phase A. Composite search score used to rank proposals across the sweep
    # leaderboard (higher is better).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(evolver_proposals)")}
    if "score_a" not in cols:
        conn.execute("ALTER TABLE evolver_proposals ADD COLUMN score_a REAL")

    # evolver_proposals.size_units — added 2026-05-14 for strategy-search-engine
    # Phase A. Position size in contract units at the time of proposal, for
    # normalising returns across different notional sizes.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(evolver_proposals)")}
    if "size_units" not in cols:
        conn.execute("ALTER TABLE evolver_proposals ADD COLUMN size_units INTEGER")

    # evolver_proposals.max_loss_per_trade — added 2026-05-14 for strategy-search-engine
    # Phase A. Maximum single-trade loss (in dollars) observed during backtesting;
    # used as a tail-risk gate in the sweep filter.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(evolver_proposals)")}
    if "max_loss_per_trade" not in cols:
        conn.execute("ALTER TABLE evolver_proposals ADD COLUMN max_loss_per_trade REAL")

    # ticker_state.best_cagr_oos — added 2026-05-15 to stop overloading best_pf_oos
    # with CAGR for growth-category tickers. Profit-factor and CAGR mean different
    # things; storing CAGR in a column named "pf_oos" was misleading the dashboard,
    # nightly briefs, and the research-health absurd-value detector.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(ticker_state)")}
    if "best_cagr_oos" not in cols:
        conn.execute("ALTER TABLE ticker_state ADD COLUMN best_cagr_oos REAL")

    # directional_signals — added 2026-05-15 for v2 decoupled architecture.
    # One row per (ticker, asof_ts) produced by the rules-based underlying
    # agent. `direction` is one of "bullish"/"bearish"/"chop"/"no_edge".
    # `confidence` is 0.0–1.0. `horizon_days` is the trade window the signal
    # is valid over. `rules_version` lets us A/B different rule packs.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS directional_signals (
            id              INTEGER PRIMARY KEY,
            ticker          TEXT    NOT NULL,
            asof_ts         INTEGER NOT NULL,
            direction       TEXT    NOT NULL,
            confidence      REAL    NOT NULL,
            horizon_days    INTEGER NOT NULL,
            rationale       TEXT,
            rules_version   TEXT    NOT NULL,
            created_at      INTEGER NOT NULL,
            UNIQUE (ticker, asof_ts, rules_version)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ds_ticker_ts ON directional_signals (ticker, asof_ts DESC)"
    )

    # v2_paper_trades — added 2026-05-15 for Phase B of the v2 architecture.
    # One row per open-and-close cycle of a paper-traded share position
    # entered from a v2 DirectionalSignal. `direction` is 'long' or 'short'.
    # `exit_*` are NULL until the trade closes. `signal_id` ties the entry
    # back to the signal row that triggered it (audit trail).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS v2_paper_trades (
            id              INTEGER PRIMARY KEY,
            ticker          TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            shares          REAL    NOT NULL,
            entry_price     REAL    NOT NULL,
            entry_ts        INTEGER NOT NULL,
            exit_price      REAL,
            exit_ts         INTEGER,
            pnl_realized    REAL,
            exit_reason     TEXT,
            signal_id       INTEGER REFERENCES directional_signals (id),
            created_at      INTEGER NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_v2pt_open ON v2_paper_trades (ticker, exit_ts)"
    )

    # Phase C.0 — Vehicle agent data model.
    # Five additive tables; v2_paper_trades remains untouched (Phase B back-compat).
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS v2_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            intent TEXT NOT NULL CHECK(intent IN ('trade', 'accumulate')),
            structure_kind TEXT NOT NULL,
            exit_plan_version INTEGER NOT NULL DEFAULT 1,
            profit_target_price REAL,
            stop_price REAL,
            time_stop_dte INTEGER,
            assignment_acceptable INTEGER,
            nearest_leg_expiry_dte INTEGER,
            exit_plan_extra_json TEXT,
            opened_ts INTEGER NOT NULL,
            closed_ts INTEGER,
            close_reason TEXT,
            linked_position_id INTEGER,
            rationale TEXT,
            FOREIGN KEY (linked_position_id) REFERENCES v2_positions(id)
        );

        CREATE TABLE IF NOT EXISTS v2_position_legs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('buy', 'sell')),
            kind TEXT NOT NULL CHECK(kind IN ('call', 'put', 'share')),
            strike REAL,
            expiry TEXT,
            qty INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            net_basis REAL,
            exit_price REAL,
            FOREIGN KEY (position_id) REFERENCES v2_positions(id)
        );

        CREATE TABLE IF NOT EXISTS v2_position_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL,
            linked_position_id INTEGER,
            event_kind TEXT NOT NULL CHECK(event_kind IN (
                'assigned', 'called_away', 'exercised', 'expired_worthless'
            )),
            occurred_ts INTEGER NOT NULL,
            source_leg_id INTEGER,
            original_credit_per_contract REAL,
            notes TEXT,
            FOREIGN KEY (position_id) REFERENCES v2_positions(id),
            FOREIGN KEY (linked_position_id) REFERENCES v2_positions(id),
            FOREIGN KEY (source_leg_id) REFERENCES v2_position_legs(id)
        );

        CREATE TABLE IF NOT EXISTS v2_position_mtm (
            position_id INTEGER NOT NULL,
            asof_ts INTEGER NOT NULL,
            mtm_value REAL NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('yahoo', 'bs', 'mixed')),
            PRIMARY KEY (position_id, asof_ts),
            FOREIGN KEY (position_id) REFERENCES v2_positions(id)
        );

        CREATE TABLE IF NOT EXISTS v2_chain_snapshots (
            ticker TEXT NOT NULL,
            asof_ts INTEGER NOT NULL,
            expiry TEXT NOT NULL,
            strike REAL NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('call', 'put')),
            bid REAL, ask REAL, last REAL, iv REAL, oi INTEGER,
            source TEXT NOT NULL CHECK(source IN ('yahoo', 'bs')),
            PRIMARY KEY (ticker, asof_ts, expiry, strike, kind)
        );
    """)

    # leaderboard view — added 2026-05-14 for strategy-search-engine
    # Phase C. Ranks proposals by score_a (annualized return on BP held),
    # gated by passed_gate=1 and trade_count >= 5 (statistical noise floor).
    # Joined to strategies so consumers don't have to look up class_name
    # separately. Idempotent via CREATE VIEW IF NOT EXISTS.
    conn.execute("""
        CREATE VIEW IF NOT EXISTS leaderboard AS
        SELECT
            ep.id              AS proposal_id,
            ep.ticker          AS ticker,
            ep.strategy_id     AS strategy_id,
            s.class_name       AS class_name,
            ep.regime_label    AS regime_label,
            ep.score_a         AS score_a,
            ep.size_units      AS size_units,
            ep.max_loss_per_trade AS max_loss_per_trade,
            ep.trade_count     AS trade_count,
            ep.pf_is           AS pf_is,
            ep.pf_oos          AS pf_oos,
            ep.proposer_model  AS proposer_model,
            ep.created_at      AS created_at,
            RANK() OVER (ORDER BY ep.score_a DESC) AS rank
        FROM evolver_proposals ep
        JOIN strategies s ON s.id = ep.strategy_id
        WHERE ep.passed_gate = 1
          AND ep.trade_count >= 5
          AND ep.score_a IS NOT NULL
        ORDER BY ep.score_a DESC
    """)
