"""
Central configuration for the trading research agents system.

This is the only file you should edit to change tickers, timeframes, or risk rules.
All modules import from here.
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# ---------- Paths ----------
ROOT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = ROOT_DIR / "cache"
REPORTS_DIR = ROOT_DIR / "reports"
LOGS_DIR = ROOT_DIR / "logs"
STRATEGY_CONFIG_PATH = ROOT_DIR / "strategy_config.json"
DB_PATH = CACHE_DIR / "trading.db"

for _d in (CACHE_DIR, REPORTS_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------- API keys (loaded from .env) ----------
load_dotenv(ROOT_DIR / ".env")

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")
UNUSUAL_WHALES_API_KEY = os.getenv("UNUSUAL_WHALES_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
NOTIFY_WEBHOOK_URL = os.getenv("NOTIFY_WEBHOOK_URL", "")
TIMEZONE = os.getenv("TIMEZONE", "America/New_York")

# ---------- Ticker universe ----------
# Each entry: (symbol, long_or_short_etf_pair, asset_class, notes)
# For tickers without an inverse ETF, we'll short via long puts or credit call spreads.
TICKERS = [
    # (symbol, inverse_symbol or None, asset_class, notes)
    ("TSLA", "TSLQ", "equity", "Tesla + 1x inverse ETF"),
    ("NVDA", None,   "equity", "Nvidia — no inverse ETF, short via options"),
    ("SPY",  "SDS",  "index",  "S&P 500 + 2x inverse"),
    ("MSTR", None,   "equity", "MicroStrategy — BTC proxy"),
    ("META", None,   "equity", "Meta"),
    ("INTC", None,   "equity", "Intel"),
    ("AMD",  None,   "equity", "AMD"),
    ("MSFT", None,   "equity", "Microsoft"),
    ("DECK", None,   "equity", "Deckers"),
    ("HIMS", None,   "equity", "Hims & Hers"),
    ("AMZN", None,   "equity", "Amazon"),
    ("ALAB", None,   "equity", "Astera Labs"),
    ("ASST", None,   "equity", "Asset Entities"),
    ("BE",   None,   "equity", "Bloom Energy"),
    ("MRVL", None,   "equity", "Marvell"),
    # Crypto-adjacent
    ("IBIT", None,   "crypto_etf", "Spot Bitcoin ETF (iShares)"),
    ("BSOL", None,   "crypto_etf", "Spot Solana ETF (Bitwise)"),
    ("CLSK", None,   "equity", "CleanSpark — BTC miner"),
    ("SATS", None,   "equity", "EchoStar / Satellite (verify ticker if BTC-adjacent)"),
    # Commodities / materials
    ("SLV",  None,   "commodity_etf", "Silver ETF"),
    ("SILJ", None,   "commodity_etf", "Junior silver miners"),
    ("GLD",  None,   "commodity_etf", "Gold ETF"),
    ("USOIL", None,  "commodity",     "WTI crude — may need CL futures or USO as proxy"),
    ("CPER", None,   "commodity_etf", "Copper ETF"),
    ("REMX", None,   "commodity_etf", "Rare earth metals"),
]

# Fast-lookup set
TICKER_SYMBOLS = sorted({t[0] for t in TICKERS} | {t[1] for t in TICKERS if t[1]})

# ---------- Timeframes ----------
# Polygon bar aggregation: (multiplier, timespan)
TIMEFRAMES = {
    "15m": (15, "minute"),
    "1h":  (1,  "hour"),
    "4h":  (4,  "hour"),
    "1d":  (1,  "day"),
    "1w":  (1,  "week"),
}

# How many historical bars to fetch per timeframe when running fresh analysis.
# Enough context for indicators + confluence but not so many you burn API calls.
LOOKBACK_BARS = {
    "15m": 200,   # ~2 trading days
    "1h":  200,   # ~5 trading weeks
    "4h":  200,   # ~7 trading months
    "1d":  300,   # ~1.2 years
    "1w":  156,   # ~3 years
}

# ---------- Risk and portfolio rules ----------
RISK_RULES = {
    "starting_capital": 100_000.0,          # paper account
    "max_concurrent_positions": 10,
    "max_per_ticker_positions": 2,
    "max_per_trade_risk_pct": 0.015,        # 1.5% of equity risked per trade
    "max_sector_exposure_pct": 0.35,        # 35% max in any one sector
    "max_gross_exposure_pct": 1.5,          # 150% gross (longs + shorts)
    "min_confluence_score": 60,             # 0-100 threshold to open a trade
    "min_risk_reward": 1.8,                 # minimum R:R on entry
    "default_stop_atr_mult": 1.5,           # stop = entry ± 1.5 * ATR(daily)
    "default_target_atr_mult": 3.0,         # target = entry ± 3.0 * ATR(daily)
}

# ---------- Strategy families ----------
# These are the trade templates the decision agent picks from based on setup and IV rank.
STRATEGY_FAMILIES = [
    "long_equity",            # buy shares / inverse ETF for direction
    "long_call",              # long call for bullish, short-dated
    "long_put",               # long put for bearish, short-dated
    "put_credit_spread",      # bullish premium collection
    "call_credit_spread",     # bearish premium collection
    "covered_call",           # wheel — if holding shares
    "cash_secured_put",       # wheel — want shares at discount
]

# ---------- Sector map (approximate — used for exposure limits) ----------
SECTOR_MAP = {
    "TSLA": "auto", "NVDA": "semis", "SPY": "index", "SDS": "index",
    "MSTR": "crypto", "META": "tech", "INTC": "semis", "AMD": "semis",
    "MSFT": "tech", "DECK": "consumer", "HIMS": "healthcare", "AMZN": "consumer",
    "ALAB": "semis", "ASST": "crypto", "BE": "energy", "MRVL": "semis",
    "IBIT": "crypto", "BSOL": "crypto", "CLSK": "crypto", "SATS": "comms",
    "SLV": "metals", "SILJ": "metals", "GLD": "metals",
    "USOIL": "energy", "CPER": "metals", "REMX": "metals",
    "TSLQ": "auto",
}
