"""
LLM regime synthesis + caching.

Synthesizes concise market and per-ticker regime briefs using Claude,
stores results in the regime_briefs table, and tracks API costs in
cost_ledger. Returns a cached brief on repeated calls for the same
scope + timestamp.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict

from bullbot import config
from bullbot.features.regime_signals import MarketSignals, TickerSignals
from bullbot.risk import cost_ledger
from bullbot.strategies import registry

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_MARKET_SYSTEM_PROMPT = """You are a quantitative regime analyst for an options trading system.
The registered strategies available are: {strategy_names}.

Given quantitative market signals, produce a 3–5 sentence regime assessment that:
1. Describes the current market environment (volatility, trend, breadth, risk appetite).
2. Explains what regime this represents (e.g. risk-on bull, elevated-vol defensive, etc.).
3. Recommends which of the registered strategies are most appropriate NOW and why.
Only recommend strategies from the registered list. Be concise and actionable."""

_TICKER_SYSTEM_PROMPT = """You are a quantitative regime analyst for an options trading system.
The registered strategies available are: {strategy_names}.

Given per-ticker regime signals and market context, produce a 2–3 sentence assessment that:
1. Characterizes the ticker's vol and relative-strength regime.
2. Recommends which registered strategies are appropriate for this specific ticker NOW.
Only recommend strategies from the registered list. Be concise and actionable."""

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_market_signals(signals: MarketSignals) -> str:
    top_sectors = sorted(
        signals.sector_momentum.items(), key=lambda x: x[1], reverse=True
    )[:3]
    sector_str = ", ".join(f"{etf}: {mom:+.1f}%" for etf, mom in top_sectors)
    return (
        f"VIX: {signals.vix_level:.1f} (percentile {signals.vix_percentile:.0f}th, "
        f"term slope {signals.vix_term_slope:.3f})\n"
        f"SPY trend: {signals.spy_trend}, 20d momentum: {signals.spy_momentum:+.2f}%\n"
        f"Breadth (% sectors above 50d SMA): {signals.breadth_score:.1f}%\n"
        f"Top sector momentum: {sector_str}\n"
        f"Risk appetite (HYG/TLT): {signals.risk_appetite}\n"
        f"Realized vs implied vol (RV-VIX): {signals.realized_vs_implied:+.2f}"
    )


def _format_ticker_signals(signals: TickerSignals, market_brief: str) -> str:
    sector_info = signals.sector_etf if signals.sector_etf else "N/A"
    return (
        f"Market context:\n{market_brief}\n\n"
        f"Ticker: {signals.ticker}\n"
        f"IV Rank: {signals.iv_rank:.1f}, IV Percentile: {signals.iv_percentile:.1f}\n"
        f"Sector ETF: {sector_info}, Sector relative return: {signals.sector_relative:+.2f}%\n"
        f"Vol regime: {signals.vol_regime}"
    )


def _fallback_market_brief(signals: MarketSignals) -> str:
    return (
        f"Market regime (auto-generated): VIX at {signals.vix_level:.1f} "
        f"({signals.vix_percentile:.0f}th percentile), SPY trend {signals.spy_trend}, "
        f"breadth {signals.breadth_score:.0f}%, risk appetite {signals.risk_appetite}. "
        f"Realized-vs-implied spread: {signals.realized_vs_implied:+.1f}. "
        f"LLM synthesis unavailable; using template fallback."
    )


def _fallback_ticker_brief(signals: TickerSignals) -> str:
    return (
        f"{signals.ticker} regime (auto-generated): IV rank {signals.iv_rank:.0f}, "
        f"IV percentile {signals.iv_percentile:.0f}, vol regime {signals.vol_regime}, "
        f"sector relative {signals.sector_relative:+.2f}%. "
        f"LLM synthesis unavailable; using template fallback."
    )


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


def _calc_cost(usage) -> float:
    """Compute cost from a usage object with input_tokens / output_tokens."""
    cost = (usage.input_tokens * 3.0 + usage.output_tokens * 15.0) / 1_000_000
    return max(cost, 0.001)


# ---------------------------------------------------------------------------
# Synthesis functions
# ---------------------------------------------------------------------------


def synthesize_market_brief(client, signals: MarketSignals) -> tuple[str, float]:
    """Call the LLM to synthesize a market regime brief.

    Returns (brief_text, cost_usd). Retries once on failure, then falls
    back to a template string with cost=0.0.
    """
    strategy_names = ", ".join(registry.list_all_names())
    system_prompt = _MARKET_SYSTEM_PROMPT.format(strategy_names=strategy_names)
    user_content = _format_market_signals(signals)

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=config.REGIME_SYNTHESIS_MODEL,
                max_tokens=config.REGIME_MARKET_BRIEF_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            brief = response.content[0].text
            cost = _calc_cost(response.usage)
            return brief, cost
        except Exception as exc:
            if attempt == 0:
                log.warning("synthesize_market_brief attempt 1 failed: %s — retrying", exc)
            else:
                log.error("synthesize_market_brief failed after retry: %s — using fallback", exc)

    return _fallback_market_brief(signals), 0.0


def synthesize_ticker_brief(
    client, signals: TickerSignals, market_brief: str
) -> tuple[str, float]:
    """Call the LLM to synthesize a ticker regime brief.

    Returns (brief_text, cost_usd). Retries once on failure, then falls
    back to a template string with cost=0.0.
    """
    strategy_names = ", ".join(registry.list_all_names())
    system_prompt = _TICKER_SYSTEM_PROMPT.format(strategy_names=strategy_names)
    user_content = _format_ticker_signals(signals, market_brief)

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=config.REGIME_SYNTHESIS_MODEL,
                max_tokens=config.REGIME_TICKER_BRIEF_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            brief = response.content[0].text
            cost = _calc_cost(response.usage)
            return brief, cost
        except Exception as exc:
            if attempt == 0:
                log.warning("synthesize_ticker_brief attempt 1 failed: %s — retrying", exc)
            else:
                log.error("synthesize_ticker_brief failed after retry: %s — using fallback", exc)

    return _fallback_ticker_brief(signals), 0.0


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------


def get_brief(conn, scope: str, ts: int) -> str | None:
    """Return cached brief text for (scope, ts), or None if not cached."""
    row = conn.execute(
        "SELECT brief_text FROM regime_briefs WHERE scope=? AND ts=?",
        (scope, ts),
    ).fetchone()
    return row["brief_text"] if row else None


def refresh_market_brief(conn, client, signals: MarketSignals, ts: int) -> str:
    """Return a market regime brief, hitting the cache or calling the LLM.

    On cache miss: synthesizes via LLM, inserts the result into
    regime_briefs, and logs the cost to cost_ledger.
    """
    cached = get_brief(conn, "market", ts)
    if cached is not None:
        log.debug("regime_agent: market brief cache hit for ts=%d", ts)
        return cached

    brief, cost = synthesize_market_brief(client, signals)
    source = "llm" if cost > 0.0 else "fallback"
    created_at = int(time.time())

    conn.execute(
        """INSERT OR REPLACE INTO regime_briefs
           (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "market",
            ts,
            json.dumps(asdict(signals)),
            brief,
            config.REGIME_SYNTHESIS_MODEL,
            cost,
            source,
            created_at,
        ),
    )

    cost_ledger.append(
        conn,
        ts=ts,
        category="llm",
        ticker=None,
        amount_usd=cost,
        details={"source": "regime_agent", "scope": "market"},
    )
    log.info("regime_agent: market brief synthesized (source=%s, cost=$%.5f)", source, cost)
    return brief


def refresh_ticker_brief(
    conn, client, signals: TickerSignals, market_brief: str, ts: int
) -> str:
    """Return a ticker regime brief, hitting the cache or calling the LLM.

    On cache miss: synthesizes via LLM, inserts the result into
    regime_briefs, and logs the cost to cost_ledger.
    """
    scope = signals.ticker
    cached = get_brief(conn, scope, ts)
    if cached is not None:
        log.debug("regime_agent: ticker brief cache hit for %s ts=%d", scope, ts)
        return cached

    brief, cost = synthesize_ticker_brief(client, signals, market_brief)
    source = "llm" if cost > 0.0 else "fallback"
    created_at = int(time.time())

    conn.execute(
        """INSERT OR REPLACE INTO regime_briefs
           (scope, ts, signals_json, brief_text, model, cost_usd, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            scope,
            ts,
            json.dumps(asdict(signals)),
            brief,
            config.REGIME_SYNTHESIS_MODEL,
            cost,
            source,
            created_at,
        ),
    )

    cost_ledger.append(
        conn,
        ts=ts,
        category="llm",
        ticker=scope,
        amount_usd=cost,
        details={"source": "regime_agent", "scope": scope},
    )
    log.info(
        "regime_agent: ticker brief synthesized for %s (source=%s, cost=$%.5f)",
        scope, source, cost,
    )
    return brief
