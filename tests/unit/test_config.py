"""Config sanity: constants from spec §12 exist with correct values."""
from bullbot import config


def test_universe_contents():
    # Equity singles + indexes + promoted sector ETFs + HYG (credit) +
    # Dan-requested SATS/VCX (2026-05-13) +
    # Dan-requested MSTR/BSOL/IBIT (2026-05-14, crypto-adjacent equity/ETFs).
    # Sector ETFs (2026-04-22) added to broaden evolver search space; they
    # remain in REGIME_DATA_TICKERS for regime feature synthesis as well.
    assert set(config.UNIVERSE) == {
        "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META", "GOOGL",
        "SPY", "QQQ", "IWM",
        "XLK", "XLF", "XLE", "XLV", "XLI",
        "HYG",
        "SATS", "VCX",
        "MSTR", "BSOL", "IBIT",
    }
    assert len(config.UNIVERSE) == len(set(config.UNIVERSE))  # no dupes


def test_universe_has_category_and_sector_map_entries():
    # Every trading-candidate ticker must have an explicit category and
    # sector-map entry (defaults exist but explicitness prevents silent drift).
    for ticker in config.UNIVERSE:
        assert ticker in config.TICKER_CATEGORY, f"{ticker} missing from TICKER_CATEGORY"
        assert ticker in config.TICKER_SECTOR_MAP, f"{ticker} missing from TICKER_SECTOR_MAP"

def test_capital_and_timeline():
    assert config.INITIAL_CAPITAL_USD == 50_000
    assert config.TARGET_MONTHLY_PNL_USD == 10_000
    assert config.TARGET_DATE == "2026-07-10"

def test_edge_gate_thresholds():
    assert config.EDGE_PF_IS_MIN == 1.5
    assert config.EDGE_PF_OOS_MIN == 1.3
    assert config.EDGE_TRADE_COUNT_MIN == 5

def test_walkforward_config():
    assert config.WF_TRAIN_FRAC == 0.70
    assert config.WF_WINDOW_MONTHS == 24
    assert config.WF_STEP_DAYS == 30
    assert config.WF_MIN_FOLDS == 3
    assert config.WF_MAX_FOLDS == 8

def test_plateau_thresholds():
    assert config.PLATEAU_IMPROVEMENT_MIN == 0.10
    assert config.PLATEAU_COUNTER_MAX == 3
    assert config.ITERATION_CAP == 50
    assert config.HISTORY_BLOCK_SIZE == 15

def test_promotion_gate():
    assert config.PAPER_TRIAL_DAYS == 21
    assert config.PAPER_TRADE_COUNT_MIN == 10
    assert config.FAITHFULNESS_MIN_DAYS == 5
    assert config.FAITHFULNESS_DELTA_MAX == 0.30
    assert config.PAPER_DD_MULT_MAX == 1.5

def test_kill_switch_thresholds():
    assert config.KILL_DAILY_LOSS_USD == 1_500
    assert config.KILL_TOTAL_DD_USD == 5_000
    assert config.KILL_RESEARCH_RATTHOLE_USD == 1_000

def test_position_sizing():
    assert config.POSITION_RISK_FRAC == 0.02
    assert config.MAX_POSITIONS_PER_TICKER == 3
    assert config.MAX_POSITIONS_TOTAL == 10

def test_fill_model():
    assert config.COMMISSION_PER_CONTRACT_USD == 0.65
    assert config.SLIPPAGE_TICKS_PER_LEG == 1
    assert config.MIN_SPREAD_FRAC == 0.50

def test_llm_model():
    assert config.PROPOSER_MODEL == "claude-opus-4-6"
    assert config.PROPOSER_MODEL_FALLBACK == "claude-sonnet-4-6"
    assert config.PROPOSER_MAX_TOKENS == 2000

def test_scheduling():
    assert config.TICK_INTERVAL_MARKET_SEC == 60
    assert config.TICK_INTERVAL_OFFHOURS_SEC == 5
    assert config.MARKET_TIMEZONE == "America/New_York"

def test_api_keys_loaded_from_env(monkeypatch):
    assert hasattr(config, "UW_API_KEY")
    assert hasattr(config, "POLYGON_API_KEY")
    assert hasattr(config, "ANTHROPIC_API_KEY")

def test_paths_are_absolute():
    assert config.DB_PATH.is_absolute()
    assert config.REPORTS_DIR.is_absolute()
    assert config.LOGS_DIR.is_absolute()

def test_regime_config_constants_exist():
    from bullbot import config
    assert isinstance(config.REGIME_DATA_TICKERS, list)
    assert "VIX" in config.REGIME_DATA_TICKERS
    assert len(config.REGIME_DATA_TICKERS) == 14
    assert config.REGIME_SYNTHESIS_MODEL == "claude-sonnet-4-6"
    assert config.REGIME_MARKET_BRIEF_MAX_TOKENS == 300
    assert config.REGIME_TICKER_BRIEF_MAX_TOKENS == 200
    assert isinstance(config.TICKER_SECTOR_MAP, dict)
    assert config.TICKER_SECTOR_MAP["AAPL"] == "XLK"
    assert config.TICKER_SECTOR_MAP["SPY"] is None

def test_growth_config():
    assert config.TICKER_CATEGORY["TSLA"] == "growth"
    assert config.TICKER_CATEGORY["SPY"] == "income"
    assert config.GROWTH_FRAC_BULL == 0.40
    assert config.GROWTH_FRAC_CHOP == 0.20
    assert config.GROWTH_FRAC_BEAR == 0.10
    assert config.GROWTH_WF_WINDOW_MONTHS == 60
    assert config.GROWTH_WF_STEP_DAYS == 90
    assert config.GROWTH_EDGE_CAGR_MIN == 0.20
    assert config.GROWTH_EDGE_SORTINO_MIN == 1.0
    assert config.GROWTH_EDGE_MAX_DD_PCT == 0.35
    assert config.GROWTH_EDGE_TRADE_COUNT_MIN == 5


def test_health_brief_config():
    assert config.HEALTH_DEAD_PAPER_DAYS == 3
    assert config.HEALTH_MIN_BARS_FOR_WF == config.WF_WINDOW_MONTHS * 21
    assert config.HEALTH_PF_OOS_ABSURD_THRESHOLD == 1e10

def test_phase1_caching_config():
    assert config.PROPOSER_CACHE_ENABLED is True
    assert config.SKIP_BRIEFS_FOR_RETIRED is True


def test_phase2_ab_config():
    assert config.PROPOSER_MODEL_AB_ENABLED is True
    assert config.PROPOSER_MODEL_A == "claude-opus-4-6"
    assert config.PROPOSER_MODEL_B == "claude-sonnet-4-6"
    # Per-model pricing in USD per million tokens (input, output).
    assert config.PROPOSER_MODEL_PRICING == {
        "claude-opus-4-6":   (15.0, 75.0),
        "claude-sonnet-4-6": (3.0, 15.0),
    }
