# Trading Research Agents — Simulated Multi-Timeframe System

A multi-agent simulated trading bot. Agents research across five timeframes, a decision agent compiles the research into paper trades, a performance agent tracks results, and a strategy evolver updates the rules over time. The goal is to let it run for 1–2 months and measure how accurate the recommendations become.

## Important: this is a paper trading simulator

**No real money moves.** The system opens simulated trades in a local ledger, marks them to market daily, and records P&L. You watch it, critique it, and give it feedback through Claude. All real trading decisions remain yours. None of this is financial advice.

## The four agents

### 1. Research Agents (per timeframe)
Run hourly. For each ticker, analyze price action, momentum, volume, and options flow on five timeframes:

- **Weekly** — regime, major S/R, multi-week trend
- **Daily** — swing trend, daily pivots, earnings/catalyst proximity
- **4-hour** — intraday trend, retest setups
- **1-hour** — entry zones, momentum divergences
- **15-minute** — execution timing, near-term momentum

Output per timeframe: direction (long/short/neutral), strength (0–100), key levels, and a 1-line rationale. Also scores a **confluence** number: how many timeframes agree and how strongly.

Data sources: Polygon.io (bars, options chains, greeks) + Unusual Whales (options flow, dark pool, GEX, IV ranks).

### 2. Decision Agent
Runs twice daily (market open + midday) and after material events. Reads the latest research outputs for every ticker, ranks opportunities by confluence × risk-reward × conviction, and opens paper trades against the simulated ledger. Respects portfolio rules: max concurrent positions, max per-sector exposure, max per-trade risk.

Supports both sides of the market using two mechanisms:
- **Direct shorts** via inverse ETFs (TSLQ, SDS, etc.)
- **Long options** (long puts for short bias, long calls for long bias)
- **Premium collection** (credit spreads, CCs, CSPs) when IV rank is elevated

### 3. Performance Analyzer
Runs every night after close. Marks open paper positions to market, computes realized + unrealized P&L, classifies trades by strategy / timeframe / ticker / conviction, and appends a daily performance row. Weekly it produces an attribution report: which confluence patterns actually worked, which agents' signals had edge, and what the hit rate is by conviction bucket.

### 4. Strategy Evolver
Runs weekly. Reads the performance attribution report, proposes changes to the strategy config (e.g., "bump minimum confluence score from 60 to 70" or "down-weight 15-min signals — they produced 35% hit rate on their own"), and writes a new versioned strategy file. You review and approve via Claude; the decision agent uses the latest approved version on the next run.

## System diagram

```
   ┌──────────────────────────────────────────────────────┐
   │              Polygon.io + Unusual Whales             │
   └──────────┬───────────────────────────────────────────┘
              │
              ▼
   ┌──────────────────────┐       ┌───────────────────────┐
   │   Research Agents    │──────▶│   Signals Database    │
   │  (5 timeframes x N)  │       │  (latest + history)   │
   └──────────────────────┘       └─────────┬─────────────┘
                                            │
                                            ▼
                                ┌───────────────────────┐
                                │    Decision Agent     │
                                │  (reads strategy cfg) │
                                └─────────┬─────────────┘
                                          │ opens/closes
                                          ▼
                                ┌───────────────────────┐
                                │  Paper Trade Ledger   │
                                │    (SQLite + log)     │
                                └─────────┬─────────────┘
                                          │
                                          ▼
                                ┌───────────────────────┐
                                │ Performance Analyzer  │
                                │  (daily + weekly)     │
                                └─────────┬─────────────┘
                                          │
                                          ▼
                                ┌───────────────────────┐
                                │   Strategy Evolver    │
                                │  (writes new version) │
                                └───────────────────────┘
```

## Run cadence

| Agent                | Schedule                                     |
|----------------------|----------------------------------------------|
| Research (15m/1H/4H) | Every hour during US market hours            |
| Research (daily)     | Daily after close (5:00 PM ET)                |
| Research (weekly)    | Friday after close                           |
| Decision agent       | 9:45 AM ET + 12:30 PM ET + after fresh signals |
| Performance analyzer | Daily at 6:00 PM ET                           |
| Strategy evolver     | Weekly Sunday 7:00 PM ET                      |

Claude runs these via scheduled tasks on your machine. No servers to manage.

## Ticker list

Long/short pairs and single names from your initial list (edit in `config.py`):

- **Equities:** TSLA/TSLQ, NVDA, SPY/SDS, MSTR, META, INTC, AMD, MSFT, DECK, HIMS, AMZN, ALAB, ASST, BE, MRVL
- **Crypto-adjacent:** IBIT (Bitcoin), BSOL (Solana), CLSK, SATS
- **Commodities:** SLV/SILJ, GLD, USOIL, CPER, REMX

## Directory layout

```
trading-research-agents/
├── README.md
├── requirements.txt
├── .env.template              # copy to .env
├── config.py                  # tickers, timeframes, risk rules, paths
├── strategy_config.json       # versioned strategy parameters (evolved over time)
├── clients/
│   ├── polygon_client.py      # multi-timeframe bars, options chains
│   └── uw_client.py           # Unusual Whales flow, GEX, dark pool
├── data/
│   └── cache.py               # SQLite cache for bars + options
├── analysis/
│   ├── indicators.py          # EMA, RSI, MACD, ATR, volume profile
│   └── confluence.py          # multi-timeframe confluence scoring
├── paper_trading/
│   ├── ledger.py              # SQLite-backed paper ledger
│   └── portfolio.py           # risk rules, position sizing, limits
├── agents/
│   ├── research_agent.md      # research agent prompt
│   ├── decision_agent.md      # decision agent prompt
│   ├── performance_agent.md   # performance analyzer prompt
│   └── evolver_agent.md       # strategy evolver prompt
├── scripts/
│   ├── run_research.py        # entry point — research pass
│   ├── run_decision.py        # entry point — decision pass
│   ├── run_performance.py     # entry point — nightly performance
│   ├── run_evolver.py         # entry point — weekly evolver
│   └── seed_db.py             # initialize the SQLite DB
├── reports/                   # markdown reports written by agents
├── cache/                     # SQLite DBs + parquet bar data
└── logs/                      # agent run logs
```

## How you interact with it

Everything goes through Claude:

- *"What did research find for TSLA today?"* → reads latest research signals for TSLA across all 5 timeframes
- *"What paper trades are open?"* → shows the ledger
- *"What was yesterday's P&L?"* → reads performance report
- *"Which tickers have the strongest confluence right now?"* → top-of-book from the signals DB
- *"The evolver proposed bumping the confluence threshold — show me the diff and approve."* → reviews the pending strategy version
- *"Add RIVN to the watchlist."* → edits `config.py`

## Setup (one-time)

1. Copy `.env.template` to `.env` and fill in Polygon + Unusual Whales keys
2. Tell Claude "set up the trading simulator" — it installs deps, initializes the SQLite DB, and creates the scheduled tasks
3. Approve the scheduled tasks when prompted

## Honest constraints

- **Paper fills are approximations.** The simulator fills at bar close for the relevant timeframe (conservative). Real slippage will be different.
- **Options paper fills use mid-price** from the chain at decision time. Real fills sit closer to the ask when buying.
- **Data cost/rate limits matter.** Fetching 15-min bars for 25+ tickers hourly adds up. The cache layer deduplicates aggressively.
- **Simulated ≠ predictive.** A month of paper trading doesn't validate a strategy statistically. Two months minimum to start forming an opinion, six months to trust it.
- **The strategy evolver is conservative.** It proposes changes, never applies them automatically without your review.
