# Market Regime Agent — Design Spec

**Status:** Draft for approval
**Author:** Session 6 brainstorming (Dan Runion + Claude)
**Date:** 2026-04-10
**Depends on:** Bull-Bot v3 Stage 1 (complete)

---

## 1. Problem statement

The evolver's proposer (Opus) currently receives minimal market context when generating strategy proposals: a simple `'bull'|'bear'|'chop'` regime label derived from 60-day returns, a hardcoded IV rank of 50.0, and an empty ATM greeks dict. It has no awareness of the volatility environment, sector rotation, risk appetite, or which strategy families are favored in current conditions.

This means the proposer wastes iterations proposing strategies that are inappropriate for the market regime — long straddles in low-vol environments, premium selling when IV is cheap, or ignoring sector-specific opportunities.

A market regime agent solves this by computing quantitative market signals from data we already collect, synthesizing them into actionable strategy briefs via Sonnet, and injecting those briefs into the proposer's prompt. The evolver still validates everything through backtesting — the regime agent narrows the search space.

---

## 2. Goals and non-goals

### Goals
- Provide the proposer with rich, actionable market context so it proposes strategies appropriate to current conditions
- Compute real IV rank per ticker (replacing the hardcoded 50.0)
- Produce both market-wide and per-ticker strategy briefs daily
- Persist regime analysis for retrospective correlation with strategy performance
- Keep daily regime analysis cost under $0.05 for a 10-ticker universe

### Non-goals
- Web research / news / qualitative inputs (deferred to Layer 2, built on top of this foundation)
- Intraday regime updates (daily-timeframe strategies don't need sub-daily regime changes)
- Autonomous strategy selection (the regime agent advises, the proposer decides, the backtest validates)
- Earnings date sourcing from external APIs (use config or omit until Layer 2)

---

## 3. Success criteria

- Proposer prompt includes market-wide and per-ticker regime briefs on every evolver iteration
- IV rank is computed from actual IV surface data, not hardcoded
- `regime_briefs` table contains one market row and one per-ticker row per trading day
- Regime analysis cost is tracked in `cost_ledger` with `category='llm'` and `details` JSON containing `"source": "regime_agent"`
- Daily LLM cost for regime analysis is ≤ $0.05 for 10 tickers
- All existing tests continue to pass (backward compatible)
- Regime signal computation is deterministic and unit-testable

---

## 4. Architecture

### 4.1 Two-stage pipeline: quantitative signals + LLM synthesis

```
Daily Regime Refresh (once per day, before evolver loop)
│
├─ 1. Fetch regime data tickers (VIX, sector ETFs, TLT, HYG)
│     └─ Same fetch_daily_ohlc() pipeline → bars table
│
├─ 2. Compute market-wide signals (pure Python)
│     └─ regime_signals.compute_market_signals(conn) → MarketSignals
│
├─ 3. Synthesize market brief (Sonnet LLM call)
│     └─ regime_agent.synthesize_market_brief(signals) → str
│     └─ Cache in regime_briefs(scope='market', ts=today)
│
├─ 4. For each ticker in universe:
│     ├─ Compute per-ticker signals (pure Python)
│     │   └─ regime_signals.compute_ticker_signals(conn, ticker) → TickerSignals
│     ├─ Synthesize ticker brief (Sonnet LLM call)
│     │   └─ regime_agent.synthesize_ticker_brief(signals, market_brief) → str
│     └─ Cache in regime_briefs(scope=ticker, ts=today)
│
└─ 5. Evolver loop runs with enriched snapshots
      └─ StrategySnapshot now includes market_brief + ticker_brief
      └─ Proposer prompt includes === Market Regime Analysis === block
```

### 4.2 Cache-aware refresh

Both `refresh_market_brief()` and `refresh_ticker_brief()` check `regime_briefs` for an existing row with today's date before calling the LLM. Multiple evolver cycles per day reuse the cached brief. This bounds daily LLM cost regardless of how many times the scheduler loops.

---

## 5. Component inventory

### 5.1 New modules

| Module | Purpose |
|--------|---------|
| `bullbot/features/regime_signals.py` | Pure-function quantitative signal computation from bars and IV surface data |
| `bullbot/features/regime_agent.py` | LLM synthesis of signals into briefs, caching, cost tracking |

### 5.2 Modified modules

| Module | Change |
|--------|--------|
| `bullbot/config.py` | Add `REGIME_DATA_TICKERS`, `REGIME_SYNTHESIS_MODEL` |
| `bullbot/db/schema.sql` | Add `regime_briefs` table |
| `bullbot/strategies/base.py` | Add `market_brief` and `ticker_brief` fields to `StrategySnapshot` |
| `bullbot/engine/step.py` | Populate IV rank from IV surface; attach briefs to snapshot (empty strings when no brief exists, e.g., during backtesting) |
| `bullbot/evolver/proposer.py` | Add regime context block to `build_user_prompt()` |
| `bullbot/scheduler.py` | Call regime refresh before evolver loop |
| `bullbot/features/regime.py` | Existing module unchanged — new signals supplement, don't replace |

---

## 6. Data layer — new market data feeds

### 6.1 Regime data tickers

New config constant `REGIME_DATA_TICKERS` (data-only, not in trading universe):

```python
REGIME_DATA_TICKERS = [
    "VIX",    # Volatility index (if UW doesn't serve VIX, use ^VIX via CBOE or UVXY as proxy)
    "XLK",    # Technology
    "XLF",    # Financials
    "XLE",    # Energy
    "XLV",    # Healthcare
    "XLI",    # Industrials
    "XLC",    # Communication services
    "XLY",    # Consumer discretionary
    "XLP",    # Consumer staples
    "XLU",    # Utilities
    "XLRE",   # Real estate
    "XLB",    # Materials
    "TLT",    # Treasury bonds (rate/risk proxy)
    "HYG",    # High-yield credit (risk appetite proxy)
]
```

These are fetched via the existing `fetch_daily_ohlc()` → `bars` table pipeline. No new API endpoints or data sources.

**VIX handling:** VIX is an index, not an equity. If UW doesn't serve VIX bars or returns data with different semantics (e.g., no volume), fall back to UVXY as a proxy. The `bars` table requires `volume NOT NULL` (STRICT mode), so VIX rows must use `volume=0` if the source doesn't provide volume data.

**Cold-start backfill:** Percentile calculations need 252 trading days of history. On the very first run, the regime agent must trigger a one-time backfill of regime data tickers (same as trading universe backfill). If insufficient history exists, degrade gracefully: skip percentile signals and produce a brief from available signals only.

### 6.2 IV rank computation

Replace the hardcoded `iv_rank = 50.0` in `engine/step.py` with actual computation:

- Source: `iv_surface` table, ATM strikes at ~30 DTE
- Calculation: `(current_iv - 52wk_low) / (52wk_high - 52wk_low) * 100`
- Fallback: if insufficient IV history for a ticker, use VIX percentile as proxy

---

## 7. Quantitative regime signals

### 7.1 Market-wide signals

All computed from `bars` table data for regime data tickers.

```python
@dataclass(frozen=True)
class MarketSignals:
    # VIX
    vix_level: float              # Latest VIX close
    vix_percentile: float         # Current vs 252-day range (0-100)
    vix_term_slope: float         # 5d SMA vs 20d SMA (>1 = contango, <1 = backwardation)

    # Trend
    spy_trend: str                # 'up' | 'down' | 'flat' (price vs SMA50/SMA200)
    spy_momentum: float           # 20-day rate of change (%)

    # Breadth
    breadth_score: float          # % of 11 sectors above 50d SMA (0-100)
    sector_momentum: dict         # {sector_etf: 20d_return} ranked

    # Risk appetite
    risk_appetite: str            # 'risk_on' | 'neutral' | 'risk_off' (HYG/TLT ratio trend)

    # Volatility premium
    realized_vs_implied: float    # SPY 20d realized vol minus VIX (negative = vol premium)
```

### 7.2 Per-ticker signals

```python
@dataclass(frozen=True)
class TickerSignals:
    ticker: str
    iv_rank: float                # ATM 30d IV vs 52-week range (0-100)
    iv_percentile: float          # % of past-year days IV was lower (0-100)
    sector_relative: float        # Ticker 20d return minus sector ETF 20d return
    vol_regime: str               # 'low' | 'moderate' | 'high' (20d realized vol percentile)
    sector_etf: str               # Which sector ETF this ticker maps to
    # NOTE: earnings_proximity deferred to Layer 2 (no data source in v1)
```

### 7.3 Ticker-to-sector mapping

A config dict maps each universe ticker to its sector ETF:

```python
TICKER_SECTOR_MAP = {
    "SPY": None,    # Index, uses breadth_score instead
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
```

---

## 8. LLM synthesis

### 8.1 Market brief synthesis

**Model:** Sonnet 4.6 (`claude-sonnet-4-6`)
**Max tokens:** 300

**System prompt:**
```
You are a quantitative market regime analyst for an automated options trading system.
Given market signals, produce a concise regime assessment and strategy recommendations.
Output 3-5 sentences. Be specific about which options strategy families are favored
or disfavored in current conditions. Do not hedge — state your assessment directly.

The trading system can implement these registered strategies: {strategy_names}
Only recommend strategy types from this list.
```

**Note:** The synthesis prompt receives the registered strategy names from the registry so recommendations align with what the proposer can actually build.

**User prompt:** Formatted market signals (VIX, trend, breadth, sector rankings, risk appetite, vol premium).

**Example output:**
> Low-vol trending bull regime. VIX at 15 (12th percentile) with contango term structure — implied vol is cheap relative to history. 9 of 11 sectors above 50d SMA, led by XLK (+4.2%) and XLC (+3.1%). Risk-on confirmed by rising HYG/TLT. Favors: short put spreads on pullbacks, covered calls on high-IV-rank names. Disfavors: long straddles, debit spreads (premium is expensive relative to expected moves).

### 8.2 Ticker brief synthesis

**Model:** Sonnet 4.6
**Max tokens:** 200

**System prompt:**
```
You are a quantitative analyst advising an automated options strategy proposer.
Given market context and ticker-specific signals, recommend strategy approaches
for this specific ticker. Output 2-3 sentences. Be specific about strategy types
and why they suit this ticker's current conditions.
```

**User prompt:** Market brief + per-ticker signals.

**Example output:**
> AAPL IV rank at 72nd percentile — elevated vs sector (XLK avg 35th). In a low-vol bull market, this relative IV richness favors short premium. Consider credit put spreads at support or short strangles if range-bound; avoid long premium plays until IV mean-reverts.

### 8.3 Cost budget

Sonnet 4.6 pricing: $3 / MTok input, $15 / MTok output.

| Call | Input tokens | Output tokens | Cost/call | Daily (10 tickers) |
|------|-------------|---------------|-----------|---------------------|
| Market brief | ~400 | ~150 | ~$0.003 | $0.003 |
| Ticker brief | ~600 | ~100 | ~$0.003 | $0.030 |
| **Total** | | | | **~$0.033/day** |

Well under the $0.05/day ceiling.

---

## 9. Storage

### 9.1 New table: `regime_briefs`

```sql
CREATE TABLE regime_briefs (
    id              INTEGER PRIMARY KEY,
    scope           TEXT NOT NULL,       -- 'market' or ticker symbol (e.g. 'AAPL')
    ts              INTEGER NOT NULL,    -- trading day as midnight UTC epoch
    signals_json    TEXT NOT NULL,       -- raw quantitative signals (JSON)
    brief_text      TEXT NOT NULL,       -- LLM-synthesized brief
    model           TEXT NOT NULL,       -- model used (e.g. 'claude-sonnet-4-6')
    cost_usd        REAL NOT NULL,       -- LLM cost for this synthesis
    source          TEXT NOT NULL DEFAULT 'llm',  -- 'llm' or 'fallback' (template-only, no LLM)
    created_at      INTEGER NOT NULL,
    UNIQUE(scope, ts)
);
```

**`ts` convention:** Same as `bars` — the trading date expressed as midnight UTC epoch. Example: 2026-04-10 market day → `ts = 1744243200` (2026-04-10T00:00:00Z).

### 9.2 Cache behavior

- `get_brief(conn, scope, ts) -> Optional[str]` — returns cached `brief_text` or `None`
- On cache miss: compute signals → call LLM → insert row → return brief
- On cache hit: return immediately, no LLM call
- `UNIQUE(scope, ts)` enforces at-most-once generation per scope per day

---

## 10. Integration points

### 10.1 Scheduler changes

```python
# In scheduler.tick(), before the evolver loop:
def tick(self):
    self._check_kill_switch()
    regime_agent.refresh_market_brief(self.conn)          # NEW
    for ticker in config.UNIVERSE:
        regime_agent.refresh_ticker_brief(self.conn, ticker)  # NEW
        self._run_evolver_iteration(ticker)
```

### 10.2 StrategySnapshot extension

Two new fields added to the frozen dataclass with default empty strings so existing call sites and tests don't break:

```python
@dataclass(frozen=True)
class StrategySnapshot:
    ticker: str
    asof_ts: int
    spot: float
    bars_1d: list[Bar]
    indicators: dict[str, float]
    atm_greeks: dict[str, float]
    iv_rank: float          # NOW COMPUTED, was hardcoded 50.0
    regime: str
    chain: list[OptionContract]
    market_brief: str = ""  # NEW — empty during backtesting
    ticker_brief: str = ""  # NEW — empty during backtesting
```

During backtesting, `_build_snapshot()` passes empty strings for briefs (no regime brief exists for historical cursor dates). During live/discovery mode, briefs are populated from the `regime_briefs` cache.

### 10.3 Proposer prompt changes

New block added to `build_user_prompt()` between the market snapshot and evolver history:

```
=== Market Regime Analysis ===
{snapshot.market_brief}

=== Ticker Analysis ({snapshot.ticker}) ===
{snapshot.ticker_brief}
```

### 10.4 Also fixed

- IV rank computed from `iv_surface` table (replaces hardcoded 50.0)
- ATM greeks populated from `option_contracts` table at snapshot time

---

## 11. Error handling

- **Regime data fetch fails:** Log warning, skip regime refresh for today. Evolver runs without briefs (empty strings in snapshot). The proposer still works — it just gets less context.
- **Sonnet synthesis fails:** Retry once. On second failure, fall back to a template-formatted string of the raw signals (no LLM). Row in `regime_briefs` gets `source='fallback'`. Cost logged to `cost_ledger` with `category='llm'` and `details` JSON containing `"source": "regime_agent_fallback"`.
- **IV surface insufficient:** Fall back to VIX percentile for IV rank. Log at debug level. This is expected for tickers where options backfill hasn't been run yet.
- **Missing sector ETF data:** Omit sector-relative signal for that ticker. Brief synthesis still runs with available signals.

All failures are non-fatal. The evolver loop never blocks on regime analysis.

---

## 12. Testing strategy

### 12.1 Unit tests

- **`regime_signals.py`** — Feed known bar sequences, assert expected signal values. Tests for: VIX percentile math, breadth score counting, sector momentum ranking, IV rank computation, edge cases (insufficient data, zero-vol periods).
- **`regime_agent.py`** — Mock Anthropic client. Verify: prompt construction includes all signals, cache-hit path skips LLM, cost logged to `cost_ledger`, fallback path triggers on LLM failure.
- **IV rank** — Test against known IV surface data with known 52-week range. Verify percentile math and VIX fallback.

### 12.2 Integration tests

- Full refresh cycle: `refresh_market_brief()` + `refresh_ticker_brief()` → verify `regime_briefs` rows created, brief text non-empty, cost tracked.
- Cache dedup: call refresh twice same day → assert one LLM call, one row.
- Evolver iteration with regime context: verify brief text appears in proposer prompt.

### 12.3 Regression test update

- Add VIX + 2-3 sector ETF bar fixtures to the T29 regression fixture
- Verify evolver still produces deterministic results with regime context

---

## 13. Layer 2 extension point (future)

This design is built to accommodate Layer 2 (web research) later. The extension:

- A new `web_research.py` module fetches and summarizes financial news/analysis
- Its output feeds into `synthesize_market_brief()` as additional input alongside quantitative signals
- The `signals_json` column in `regime_briefs` grows to include web research summaries
- No architectural changes needed — just richer inputs to the same synthesis step

---

## 14. Config additions

```python
# Regime agent
REGIME_DATA_TICKERS = ["VIX", "XLK", "XLF", "XLE", "XLV", "XLI",
                        "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB",
                        "TLT", "HYG"]
REGIME_SYNTHESIS_MODEL = "claude-sonnet-4-6"
REGIME_MARKET_BRIEF_MAX_TOKENS = 300
REGIME_TICKER_BRIEF_MAX_TOKENS = 200
TICKER_SECTOR_MAP = {
    "SPY": None, "QQQ": "XLK", "IWM": None,
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK",
    "TSLA": "XLY", "AMD": "XLK", "META": "XLC", "GOOGL": "XLC",
}
```

---

## 15. Sign-off

This spec captures the Session 6 brainstorming output including:
- Four clarifying questions resolved (data sources, refresh frequency, persistence, per-ticker cadence)
- Three approaches proposed, Approach B (quantitative + Sonnet synthesis) selected
- Six design sections incrementally approved
- Layer 2 extension point identified for future web research integration
- Two self-review passes identified and resolved 10 issues:
  1. cost_ledger CHECK constraint compatibility (use category='llm' + details JSON)
  2. Backtest path handling (_build_snapshot briefs = empty strings for historical cursors)
  3. VIX data availability and bars.volume NOT NULL constraint
  4. Cold-start backfill requirement for 252-day percentile calculations
  5. StrategySnapshot frozen dataclass backward compatibility (default empty strings)
  6. regime_briefs source column for LLM vs fallback tracking
  7. Sonnet cost math corrected ($3/$15 per MTok, not Opus pricing)
  8. IV surface data dependency for new tickers documented
  9. ts convention clarified (midnight UTC epoch for trading date)
  10. Synthesis prompt receives registered strategy names for recommendation alignment

**Next step:** `superpowers:writing-plans` to produce the implementation plan.
