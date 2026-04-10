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
    created_at      INTEGER NOT NULL,
    UNIQUE (class_name, class_version, params_hash)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_strategies_class ON strategies (class_name, class_version);

-- ---------------------------------------------------------------------------
-- evolver_proposals: LLM-generated strategy proposals per ticker + iteration
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evolver_proposals (
    id              INTEGER PRIMARY KEY,
    ticker          TEXT    NOT NULL,
    iteration       INTEGER NOT NULL,
    strategy_id     INTEGER NOT NULL REFERENCES strategies (id),
    rationale       TEXT,
    llm_cost_usd    REAL    NOT NULL,
    passed_gate     INTEGER NOT NULL CHECK (passed_gate IN (0, 1)),
    created_at      INTEGER NOT NULL,
    UNIQUE (ticker, iteration, strategy_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_ep_ticker_iter ON evolver_proposals (ticker, iteration DESC);

-- ---------------------------------------------------------------------------
-- ticker_state: current lifecycle phase per ticker
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticker_state (
    id          INTEGER PRIMARY KEY,
    ticker      TEXT    NOT NULL UNIQUE,
    phase       TEXT    NOT NULL CHECK (phase IN ('idle', 'researching', 'deciding', 'trading', 'paused', 'error')),
    updated_at  INTEGER NOT NULL
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
    opened_at       INTEGER NOT NULL,
    closed_at       INTEGER,
    pnl_realized    REAL
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
-- kill_state: singleton row (id must be 1) for kill-switch state
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kill_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    active      INTEGER NOT NULL CHECK (active IN (0, 1)),
    reason      TEXT,
    activated_at INTEGER
) STRICT;

-- ---------------------------------------------------------------------------
-- faithfulness_checks: records of LLM output validation
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS faithfulness_checks (
    id              INTEGER PRIMARY KEY,
    ts              INTEGER NOT NULL,
    agent           TEXT    NOT NULL,
    ticker          TEXT,
    passed          INTEGER NOT NULL CHECK (passed IN (0, 1)),
    score           REAL,
    details         TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_fc_ts ON faithfulness_checks (ts DESC);

-- ---------------------------------------------------------------------------
-- iteration_failures: records of failed iteration attempts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS iteration_failures (
    id          INTEGER PRIMARY KEY,
    ts          INTEGER NOT NULL,
    ticker      TEXT,
    stage       TEXT    NOT NULL,
    error_type  TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    traceback   TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_if_ts ON iteration_failures (ts DESC);
