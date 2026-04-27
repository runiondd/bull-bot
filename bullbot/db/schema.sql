-- Bull-Bot SQLite schema
-- All tables use STRICT mode for explicit type enforcement.
-- Foreign keys must be enabled at connection time (PRAGMA foreign_keys=ON).

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ---------------------------------------------------------------------------
-- bars: OHLCV price data per ticker + timeframe
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bars (
    id          INTEGER PRIMARY KEY,
    ticker      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    ts          INTEGER NOT NULL,   -- unix epoch seconds (bar open time)
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    UNIQUE (ticker, timeframe, ts)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_bars_ticker_tf_ts ON bars (ticker, timeframe, ts DESC);

-- ---------------------------------------------------------------------------
-- option_contracts: options chain snapshots
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS option_contracts (
    id              INTEGER PRIMARY KEY,
    ticker          TEXT    NOT NULL,
    expiry          TEXT    NOT NULL,   -- ISO date YYYY-MM-DD
    strike          REAL    NOT NULL,
    kind            TEXT    NOT NULL CHECK (kind IN ('call', 'put')),
    ts              INTEGER NOT NULL,   -- snapshot unix epoch
    bid             REAL    NOT NULL,
    ask             REAL    NOT NULL,
    iv              REAL,
    delta           REAL,
    gamma           REAL,
    theta           REAL,
    vega            REAL,
    open_interest   INTEGER,
    volume          INTEGER,
    UNIQUE (ticker, expiry, strike, kind, ts)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_oc_ticker_expiry ON option_contracts (ticker, expiry, ts DESC);

-- ---------------------------------------------------------------------------
-- iv_surface: aggregated IV surface snapshots
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS iv_surface (
    id          INTEGER PRIMARY KEY,
    ticker      TEXT    NOT NULL,
    ts          INTEGER NOT NULL,
    expiry      TEXT    NOT NULL,
    strike      REAL    NOT NULL,
    iv          REAL    NOT NULL,
    UNIQUE (ticker, ts, expiry, strike)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_iv_surface_ticker_ts ON iv_surface (ticker, ts DESC);

-- ---------------------------------------------------------------------------
-- strategies: versioned strategy configurations
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategies (
    id              INTEGER PRIMARY KEY,
    class_name      TEXT    NOT NULL,
    class_version   INTEGER NOT NULL,
    params          TEXT    NOT NULL,   -- JSON blob
    params_hash     TEXT    NOT NULL,
    parent_id       INTEGER REFERENCES strategies (id),
    created_at      INTEGER NOT NULL,
    UNIQUE (class_name, class_version, params_hash)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_strategies_class ON strategies (class_name, class_version);

-- ---------------------------------------------------------------------------
-- evolver_proposals: LLM-generated strategy proposals per ticker + iteration
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evolver_proposals (
    id                  INTEGER PRIMARY KEY,
    ticker              TEXT    NOT NULL,
    iteration           INTEGER NOT NULL,
    strategy_id         INTEGER NOT NULL REFERENCES strategies (id),
    rationale           TEXT,
    llm_cost_usd        REAL    NOT NULL,
    pf_is               REAL,
    pf_oos              REAL,
    sharpe_is           REAL,
    max_dd_pct          REAL,
    trade_count         INTEGER,
    regime_breakdown    TEXT,    -- JSON blob
    passed_gate         INTEGER NOT NULL CHECK (passed_gate IN (0, 1)),
    created_at          INTEGER NOT NULL,
    UNIQUE (ticker, iteration, strategy_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_ep_ticker_iter ON evolver_proposals (ticker, iteration DESC);

-- ---------------------------------------------------------------------------
-- ticker_state: current lifecycle phase per ticker
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticker_state (
    id                  INTEGER PRIMARY KEY,
    ticker              TEXT    NOT NULL UNIQUE,
    phase               TEXT    NOT NULL CHECK (phase IN ('discovering', 'paper_trial', 'live', 'no_edge', 'killed')),
    iteration_count     INTEGER NOT NULL DEFAULT 0,
    plateau_counter     INTEGER NOT NULL DEFAULT 0,
    best_strategy_id    INTEGER REFERENCES strategies (id),
    best_pf_is          REAL,
    best_pf_oos         REAL,
    cumulative_llm_usd  REAL    NOT NULL DEFAULT 0.0,
    paper_started_at    INTEGER,
    paper_trade_count   INTEGER NOT NULL DEFAULT 0,
    live_started_at     INTEGER,
    verdict_at          INTEGER,
    retired             INTEGER NOT NULL DEFAULT 0,
    updated_at          INTEGER NOT NULL
) STRICT;

-- ---------------------------------------------------------------------------
-- orders: all paper orders (submitted, filled, cancelled)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY,
    run_id          TEXT    NOT NULL DEFAULT 'live',
    ticker          TEXT    NOT NULL,
    strategy_id     INTEGER REFERENCES strategies (id),
    intent          TEXT    NOT NULL CHECK (intent IN ('open', 'close', 'hedge')),
    legs            TEXT,           -- JSON array of leg objects
    status          TEXT    NOT NULL CHECK (status IN ('pending', 'filled', 'cancelled', 'rejected')),
    commission      REAL    NOT NULL DEFAULT 0.0,
    pnl_realized    REAL,
    placed_at       INTEGER NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_orders_run_id ON orders (run_id, ticker, placed_at DESC);

-- ---------------------------------------------------------------------------
-- positions: open and closed paper positions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY,
    run_id          TEXT    NOT NULL DEFAULT 'live',
    ticker          TEXT    NOT NULL,
    strategy_id     INTEGER REFERENCES strategies (id),
    legs            TEXT,           -- JSON array of leg objects
    contracts       INTEGER NOT NULL DEFAULT 1,
    open_price      REAL    NOT NULL,
    close_price     REAL,
    mark_to_mkt     REAL    NOT NULL DEFAULT 0.0,
    exit_rules      TEXT,           -- JSON: {"profit_target_pct": 0.5, ...}
    opened_at       INTEGER NOT NULL,
    closed_at       INTEGER,
    pnl_realized    REAL,
    -- Current unrealized P&L vs entry. NULL before first exit_manager visit;
    -- 0 at entry (see engine/step.py insert), current unrealized during hold,
    -- 0 on close (see engine/exit_manager.py + engine/step.py close path).
    unrealized_pnl  REAL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_positions_run_id ON positions (run_id, ticker, opened_at DESC);

-- ---------------------------------------------------------------------------
-- cost_ledger: append-only record of all LLM + API costs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cost_ledger (
    id          INTEGER PRIMARY KEY,
    ts          INTEGER NOT NULL,
    category    TEXT    NOT NULL CHECK (category IN ('llm', 'data_uw', 'data_polygon', 'commission', 'other')),
    ticker      TEXT,
    amount_usd  REAL    NOT NULL,
    details     TEXT    -- JSON blob
) STRICT;

CREATE INDEX IF NOT EXISTS idx_cost_ledger_ts ON cost_ledger (ts DESC);
CREATE INDEX IF NOT EXISTS idx_cost_ledger_category ON cost_ledger (category);

-- ---------------------------------------------------------------------------
-- equity_snapshots: daily snapshot of account equity for the dashboard equity curve
-- Written at the end of scheduler.tick() per (ts) UNIQUE constraint;
-- one row per UTC midnight day.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id              INTEGER PRIMARY KEY,
    ts              INTEGER NOT NULL UNIQUE,  -- unix midnight UTC of snapshot day
    total_equity    REAL    NOT NULL,
    income_equity   REAL    NOT NULL,
    growth_equity   REAL    NOT NULL,
    realized_pnl    REAL    NOT NULL,
    unrealized_pnl  REAL    NOT NULL,
    created_at      INTEGER NOT NULL DEFAULT (cast(strftime('%s','now') as integer))
) STRICT;

CREATE INDEX IF NOT EXISTS idx_equity_snapshots_ts ON equity_snapshots (ts DESC);

-- ---------------------------------------------------------------------------
-- kill_state: singleton row (id must be 1) for kill-switch state
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kill_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    active          INTEGER NOT NULL CHECK (active IN (0, 1)),
    reason          TEXT,
    trigger_rule    TEXT,
    tripped_at      INTEGER
) STRICT;

-- ---------------------------------------------------------------------------
-- faithfulness_checks: records of LLM output validation
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS faithfulness_checks (
    id              INTEGER PRIMARY KEY,
    ticker          TEXT    NOT NULL,
    checked_at      INTEGER NOT NULL,
    window_days     INTEGER NOT NULL,
    paper_pf        REAL    NOT NULL,
    backtest_pf     REAL    NOT NULL,
    delta_pct       REAL    NOT NULL,
    passed          INTEGER NOT NULL CHECK (passed IN (0, 1))
) STRICT;

CREATE INDEX IF NOT EXISTS idx_fc_ticker ON faithfulness_checks (ticker, checked_at DESC);

-- ---------------------------------------------------------------------------
-- regime_briefs: cached market and per-ticker regime analysis
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS regime_briefs (
    id              INTEGER PRIMARY KEY,
    scope           TEXT NOT NULL,       -- 'market' or ticker symbol (e.g. 'AAPL')
    ts              INTEGER NOT NULL,    -- trading day as midnight UTC epoch
    signals_json    TEXT NOT NULL,       -- raw quantitative signals (JSON)
    brief_text      TEXT NOT NULL,       -- LLM-synthesized brief
    model           TEXT NOT NULL,       -- model used (e.g. 'claude-sonnet-4-6')
    cost_usd        REAL NOT NULL,       -- LLM cost for this synthesis
    source          TEXT NOT NULL DEFAULT 'llm',  -- 'llm' or 'fallback'
    created_at      INTEGER NOT NULL,
    UNIQUE(scope, ts)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_regime_briefs_scope_ts ON regime_briefs (scope, ts DESC);

-- ---------------------------------------------------------------------------
-- iteration_failures: records of failed iteration attempts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS iteration_failures (
    id          INTEGER PRIMARY KEY,
    ts          INTEGER NOT NULL,
    ticker      TEXT,
    phase       TEXT    NOT NULL,
    exc_type    TEXT    NOT NULL,
    exc_message TEXT    NOT NULL,
    traceback   TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_if_ts ON iteration_failures (ts DESC);

-- ---------------------------------------------------------------------------
-- long_inventory: tracked long positions (shares + calls) for overlay strategies
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS long_inventory (
    id              INTEGER PRIMARY KEY,
    account         TEXT    NOT NULL,
    ticker          TEXT    NOT NULL,
    kind            TEXT    NOT NULL,
    strike          REAL,
    expiry          TEXT,
    quantity        REAL    NOT NULL,
    cost_basis_per  REAL,
    added_at        INTEGER NOT NULL,
    removed_at      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_long_inv_ticker ON long_inventory (ticker, removed_at);
