"""Config sanity: constants from spec §12 exist with correct values."""
from bullbot import config


def test_universe_is_ten_tickers():
    assert config.UNIVERSE == [
        "SPY", "QQQ", "IWM", "AAPL", "MSFT",
        "NVDA", "TSLA", "AMD", "META", "GOOGL",
    ]

def test_capital_and_timeline():
    assert config.INITIAL_CAPITAL_USD == 50_000
    assert config.TARGET_MONTHLY_PNL_USD == 10_000
    assert config.TARGET_DATE == "2026-07-10"

def test_edge_gate_thresholds():
    assert config.EDGE_PF_IS_MIN == 1.5
    assert config.EDGE_PF_OOS_MIN == 1.3
    assert config.EDGE_TRADE_COUNT_MIN == 30

def test_walkforward_config():
    assert config.WF_TRAIN_FRAC == 0.70
    assert config.WF_WINDOW_MONTHS == 24
    assert config.WF_STEP_DAYS == 30
    assert config.WF_MIN_FOLDS == 3
    assert config.WF_MAX_FOLDS == 5

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
