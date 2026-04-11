"""
Bull-Bot v3 configuration — single source of truth.

All operational constants live here. Spec §12 is the canonical reference.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "cache" / "bullbot.db"
REPORTS_DIR = ROOT_DIR / "reports"
LOGS_DIR = ROOT_DIR / "logs"
FIXTURES_DIR = ROOT_DIR / "tests" / "fixtures"

for _d in (DB_PATH.parent, REPORTS_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT_DIR / ".env")

UW_API_KEY = os.environ.get("UNUSUAL_WHALES_API_KEY", "")
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

UNIVERSE: list[str] = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT",
    "NVDA", "TSLA", "AMD", "META", "GOOGL",
]
UNIVERSE_RETIRED: list[str] = []

INITIAL_CAPITAL_USD = 50_000
TARGET_MONTHLY_PNL_USD = 10_000
TARGET_DATE = "2026-07-10"

EDGE_PF_IS_MIN = 1.5
EDGE_PF_OOS_MIN = 1.3
EDGE_TRADE_COUNT_MIN = 30

WF_TRAIN_FRAC = 0.70
WF_WINDOW_MONTHS = 24
WF_STEP_DAYS = 30
WF_MIN_FOLDS = 3
WF_MAX_FOLDS = 5

PLATEAU_IMPROVEMENT_MIN = 0.10
PLATEAU_COUNTER_MAX = 3
ITERATION_CAP = 50
HISTORY_BLOCK_SIZE = 15

PAPER_TRIAL_DAYS = 21
PAPER_TRADE_COUNT_MIN = 10
FAITHFULNESS_MIN_DAYS = 5
FAITHFULNESS_DELTA_MAX = 0.30
PAPER_DD_MULT_MAX = 1.5

KILL_DAILY_LOSS_USD = 1_500
KILL_TOTAL_DD_USD = 5_000
KILL_RESEARCH_RATTHOLE_USD = 1_000

POSITION_RISK_FRAC = 0.02
MAX_POSITIONS_PER_TICKER = 3
MAX_POSITIONS_TOTAL = 10

COMMISSION_PER_CONTRACT_USD = 0.65
SLIPPAGE_TICKS_PER_LEG = 1
MIN_SPREAD_FRAC = 0.50

REGIME_BULL_RETURN_MIN = 0.05
REGIME_BEAR_RETURN_MAX = -0.05
REGIME_BULL_VOL_MAX = 0.20

PROPOSER_MODEL = "claude-opus-4-6"
PROPOSER_MODEL_FALLBACK = "claude-sonnet-4-6"
PROPOSER_MAX_TOKENS = 2000
PROPOSER_BUDGET_CEILING_USD = 0.10

TICK_INTERVAL_MARKET_SEC = 60
TICK_INTERVAL_OFFHOURS_SEC = 5
MARKET_TIMEZONE = "America/New_York"

RISK_FREE_RATE = 0.045

# --- Regime agent ---

REGIME_DATA_TICKERS: list[str] = [
    "VIX",   # Volatility index (use UVXY as fallback if UW doesn't serve VIX)
    "XLK",   # Technology
    "XLF",   # Financials
    "XLE",   # Energy
    "XLV",   # Healthcare
    "XLI",   # Industrials
    "XLC",   # Communication services
    "XLY",   # Consumer discretionary
    "XLP",   # Consumer staples
    "XLU",   # Utilities
    "XLRE",  # Real estate
    "XLB",   # Materials
    "TLT",   # Treasury bonds (rate/risk proxy)
    "HYG",   # High-yield credit (risk appetite proxy)
]

REGIME_SYNTHESIS_MODEL = "claude-sonnet-4-6"
REGIME_MARKET_BRIEF_MAX_TOKENS = 300
REGIME_TICKER_BRIEF_MAX_TOKENS = 200

TICKER_SECTOR_MAP: dict[str, str | None] = {
    "SPY": None,    # Index — uses breadth_score instead
    "QQQ": "XLK",
    "IWM": None,    # Index
    "AAPL": "XLK",
    "MSFT": "XLK",
    "NVDA": "XLK",
    "TSLA": "XLY",
    "AMD": "XLK",
    "META": "XLC",
    "GOOGL": "XLC",
}

# Sector ETFs used for breadth calculation (all 11 GICS sectors)
SECTOR_ETFS: list[str] = [
    "XLK", "XLF", "XLE", "XLV", "XLI",
    "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB",
]
