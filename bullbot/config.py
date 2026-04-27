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
    # Equity single-names (mega-cap tech + semis)
    "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META", "GOOGL",
    # Broad indexes
    "SPY", "QQQ", "IWM",
    # Sector ETFs — promoted 2026-04-22 for search-space breadth
    "XLK", "XLF", "XLE", "XLV", "XLI",
    # Credit (bond ETF) — different regime driver than equities
    "HYG",
]
UNIVERSE_RETIRED: list[str] = []

INITIAL_CAPITAL_USD = 50_000       # Income account (taxable)
GROWTH_CAPITAL_USD = 215_000       # Growth account (tax-sheltered)
TARGET_MONTHLY_PNL_USD = 10_000
TARGET_DATE = "2026-07-10"

EDGE_PF_IS_MIN = 1.5
EDGE_PF_OOS_MIN = 1.3
EDGE_TRADE_COUNT_MIN = 5

# Ceiling for profit_factor. A fold with zero losing trades produces an
# IEEE +inf profit factor, which is mathematically "infinite edge" but
# statistically meaningless for small-sample OOS windows (e.g. 3-5 trades).
# The prior behaviour returned inf and required downstream code to branch
# on math.isinf; capping at a finite value lets us reason about pf
# uniformly. 10.0 is well above "believable" edge (industry "excellent"
# tops out ~3-5) so capping here doesn't mask real signals — anything
# above the cap was sample-size-artifact territory anyway.
PF_CEILING = 10.0

WF_TRAIN_FRAC = 0.70
WF_WINDOW_MONTHS = 24
WF_STEP_DAYS = 30
WF_MIN_FOLDS = 3
WF_MAX_FOLDS = 8

PLATEAU_IMPROVEMENT_MIN = 0.10
PLATEAU_COUNTER_MAX = 3
ITERATION_CAP = 50
HISTORY_BLOCK_SIZE = 15

# --- Growth strategy ---

TICKER_CATEGORY: dict[str, str] = {
    "SPY": "income",
    "QQQ": "income",
    "IWM": "income",
    "AAPL": "income",
    "MSFT": "income",
    "NVDA": "growth",
    "TSLA": "growth",
    "AMD": "income",
    "META": "income",
    "GOOGL": "income",
    # Sector ETFs + HYG — income (credit-style strategies, not growth LEAPS)
    "XLK": "income",
    "XLF": "income",
    "XLE": "income",
    "XLV": "income",
    "XLI": "income",
    "HYG": "income",
}

GROWTH_FRAC_BULL = 0.40
GROWTH_FRAC_CHOP = 0.20
GROWTH_FRAC_BEAR = 0.10

GROWTH_WF_WINDOW_MONTHS = 60
GROWTH_WF_STEP_DAYS = 90

GROWTH_EDGE_CAGR_MIN = 0.20
GROWTH_EDGE_SORTINO_MIN = 1.0
GROWTH_EDGE_MAX_DD_PCT = 0.35
GROWTH_EDGE_TRADE_COUNT_MIN = 5

# --- Research health brief ---

HEALTH_DEAD_PAPER_DAYS = 3
HEALTH_MIN_BARS_FOR_WF = WF_WINDOW_MONTHS * 21   # ~504 for 24mo walkforward window
HEALTH_PF_OOS_ABSURD_THRESHOLD = 1e10            # catches IEEE inf and absurdly-large pf_oos values

# --- Agentic throughput (Phase 1: caching + retired-ticker brief skip) ---

PROPOSER_CACHE_ENABLED = True       # mark static prompt blocks as ephemeral-cacheable
SKIP_BRIEFS_FOR_RETIRED = True      # don't generate regime briefs for no_edge / killed tickers

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
DEFAULT_PROFIT_TARGET_PCT = 0.50
DEFAULT_STOP_LOSS_MULT = 2.0
DEFAULT_MIN_DTE_CLOSE = 7

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
    # Sector ETFs map to None — "sector-relative" is meaningless for the
    # sector itself; regime_signals falls back to breadth_score.
    "XLK": None,
    "XLF": None,
    "XLE": None,
    "XLV": None,
    "XLI": None,
    # HYG is credit, not equity — no sector analog.
    "HYG": None,
}

# Sector ETFs used for breadth calculation (all 11 GICS sectors)
SECTOR_ETFS: list[str] = [
    "XLK", "XLF", "XLE", "XLV", "XLI",
    "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB",
]
