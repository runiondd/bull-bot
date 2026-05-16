# Bull-Bot v2 Phase C — Context for External Reviewer

This document gives an outside reviewer (Grok, a human consultant, or another model) the project background needed to read the Phase C design document critically.

## 1. What is Bull-Bot

Bull-Bot is a personal automated trading research project. It is built and operated by one person (Dan), runs on a single Mac mini ("pasture") via launchd, paper-trades a fixed universe of US equity tickers, and maintains a SQLite database (`cache/bullbot.db`) as its single source of truth.

The bot is a learning project, not a commercial product. There is no broker integration. All trades are simulated against Yahoo Finance bar and chain data. The goal is to develop trading judgment that could later be deployed with real capital, and to learn AI engineering by building agentic systems against a domain Dan cares about (markets).

Dan is a Product Manager by background, not a backend engineer. The bot is asked to communicate state in plain language ("we made $X today on AAPL"), and to make autonomous strategy/parameter decisions itself rather than asking the operator to pick deltas, DTEs, vehicles, or sizing — these are explicitly the bot's job to discover.

## 2. Architectural history

Bull-Bot has gone through two generations:

**v1 (deprecated as of 2026-05-16):** A multi-strategy proposer-evolver system using LLM-generated strategy candidates, walk-forward backtests on synthetic chains, and a paper-trade dispatcher. v1 had a continuous daemon (`run_continuous.py`), a grid baseline (`grid_baseline.py`), a leaderboard, and an A/B harness for LLM model comparison. It produced a lot of code but had fundamental issues: strategy decisions were not consistently traceable from signal to trade, the paper dispatcher had a bug that prevented promoted tickers from firing trades, and the architecture mixed "research" (find good strategies) with "execution" (trade them) in ways that made debugging painful. v1 was retired today.

**v2 (current):** A clean-slate decoupled architecture. Three independent agents, each with a single responsibility:
- **Underlying agent (Phase A — SHIPPED):** rules-based directional signal per UNIVERSE ticker, emits `DirectionalSignal(direction: bullish|bearish|chop|no_edge, confidence: 0-1)` daily.
- **Vehicle agent (Phase C — THIS DESIGN):** given a signal + rich context, picks the options structure (or shares, or pass) to express the view, with explicit entry/exit plan.
- **Trader/dispatcher (Phase B — SHIPPED; Phase C extends):** opens, mark-to-markets, and closes positions based on agent decisions and deterministic exit rules.

Phase A signal logic is deterministic: 50/200 SMA cross, slope, distance, ATR-based confidence. No LLM in the signal layer. Phase B currently dispatches share-only paper trades with a 10% stop and signal-flip exit.

Future planned phases:
- **Phase D:** LLM annotation layer on top of the rules-based signal (Haiku bumps confidence on news catalysts, knocks it down on regime ambiguity). May also expand to LLM-driven exit decisions on held positions.
- **Phase E (likely):** Risk envelope — per-direction caps, per-sector caps, daily drawdown circuit-breaker, dynamic budget allocation across tickers.

## 3. Tech stack

- Python 3.11+ (pasture runs 3.11 in `.venv`; system python is 3.9 and too old).
- SQLite (one file: `cache/bullbot.db`).
- `yfinance` for bars and option chains.
- Anthropic SDK for LLM calls (currently Sonnet for proposer agent, will use Haiku for vehicle agent).
- `pandas` / `numpy` / `scipy` for numerics. No PyTorch, no ML beyond regression.
- Dashboard is a single static HTML file regenerated daily by a launchd job, served by a second launchd job on port 8080.
- launchd jobs: `com.bullbot.daily` (the daily run) and `com.bullbot.dashboard` (HTTP server).

No Kubernetes, no Docker, no cloud. One Mac mini. Bull-Bot's deploys are `git pull && launchctl reload`.

## 4. Operating constraints

- Bot is paper-only. No real money at risk in Phase C. (Real money may follow later if the bot earns trust.)
- Daily run takes ~5 minutes on the Mac mini for the current ~20-ticker universe.
- Yahoo Finance is the only data source. It is unreliable in well-known ways: stale chains, missing strikes, intermittent timeouts, inconsistent IV values, no greeks. The design must tolerate Yahoo failures gracefully.
- Pasture has no static IP; runs on residential Starlink at `192.168.1.220`. Dashboard is reachable on LAN only.
- LLM cost is real but small. Current monthly Anthropic spend is under $5/month. Phase C's ~$0.03/day forward + ~$5/backtest-run is in budget.
- Test suite: 400+ unit + integration tests run on pasture. They are expected to pass before any merge.

## 5. Dan's stated preferences (relevant to design review)

These are not opinions the reviewer needs to share, but they are constraints the design must respect:

1. **Bot picks parameters, not Dan.** When the bot needs a vehicle/DTE/delta/strike/sizing decision, the bot decides. Asking Dan to choose between options is treated as the bot failing at its job.
2. **Risk caps are temporary, not permanent.** Current per-trade and per-ticker caps will expand as the bot earns trust. The design must read caps dynamically from config rather than hardcoding values into trading logic.
3. **MSTR + IBIT long-term thesis.** Dan plans to use LEAPS heavily on MSTR and IBIT as a Bitcoin proxy play around Q4 2026. The vehicle agent must support deep-ITM LEAPS as a first-class instrument, not a corner case.
4. **Backtest before any new trading logic.** Forward-only shipping was acceptable for Phase A (first signal) and Phase B (first dispatcher) because there was nothing to compare against. Phase C introduces real trading logic with established failure modes (theta bleed, IV crush, gamma blowup), and must be backtested before forward-paper.
5. **Plain-language status.** What dollars moved, what trades happened. Filesystem details and column names belong in an appendix or on request.

## 6. What's in the repo today (relevant to Phase C)

Already shipped:
- `bullbot/v2/signals.py` — `DirectionalSignal` dataclass + persistence helpers.
- `bullbot/v2/underlying.py` — rules-based signal generator.
- `bullbot/v2/trades.py` — `Trade` dataclass + open/close/query helpers (Phase B).
- `bullbot/v2/trader.py` — share-only paper dispatcher (Phase B).
- `v2_paper_trades` table — to be migrated into `v2_positions` + `v2_position_legs` for Phase C.
- Phase A daily runner wired into launchd.
- Dashboard V2 Signals tab showing today's signals, open position, realized P&L.
- 10% underlying stop-loss logic.

Not yet built (this is Phase C):
- Anything related to options pricing, multi-leg structures, IV handling, chain fetching.
- `levels.py`, `chains.py`, `vehicle.py`, `risk.py`, `exits.py`, `earnings.py`, `positions.py`, `backtest/`.
- Vehicle-agent dashboard surfaces.

## 7. Project entry points (where to look for current code/state)

- `bullbot/v2/` — Phase A + B source.
- `cache/bullbot.db` — live SQLite database, ground truth.
- `docs/superpowers/plans/2026-05-15-bullbot-v2-phase-a-underlying-signal.md` — Phase A implementation plan (shipped).
- `docs/superpowers/specs/` — design docs for prior phases.
- `.mentor/STATE.md`, `.mentor/BACKLOG.md` — current narrative + open work (note: these may be in flux during the v1→v2 transition).

## 8. What this design intentionally does NOT do

(Listed here because a careful reviewer might flag these as gaps, but they are deliberate scope cuts to keep Phase C shippable.)

- Does not optimize for fill quality beyond Yahoo mid-price. Real broker fill modeling is a separate phase.
- Does not implement continuous (intraday) decision-making. Phase C is end-of-day only, one decision per ticker per day.
- Does not model dividend ex-dates' impact on early assignment of short calls.
- Does not handle stock splits or ticker changes (Yahoo's data typically already adjusts for these in the bar history; chain history is more fragile).
- Does not support multi-account or multi-strategy parallel paper trading.
- Does not implement per-direction or per-sector portfolio caps; only three caps total (per-trade max loss, per-ticker concentration, total positions count).
- Does not have LLM-driven exit decisions. Exits are deterministic per the exit plan stored at entry.
