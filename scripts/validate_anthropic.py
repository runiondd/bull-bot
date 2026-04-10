"""
T0.3 — Anthropic API validation.

Calibrates Bull-Bot's bootstrap cost estimate by measuring real per-call
latency, token counts, and dollar cost for the two model tiers we plan to
use:

  * Sonnet 4.6 (claude-sonnet-4-6) — research + decision agents
  * Haiku  4.5 (claude-haiku-4-5-20251001) — cheap routing / triage agent

Each model gets 5 round-trips with a minimal research-agent prompt that
exercises:
  - structured-JSON output
  - moderate input length (analogous to a real signal-bundle prompt)
  - low temperature (deterministic)

Outputs
-------
* reports/phase0_anthropic.md
* reports/phase0_anthropic.json (full per-call dump)

Usage
-----
    source .venv/bin/activate
    python scripts/validate_anthropic.py [--n 5] [--max-tokens 600]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import ANTHROPIC_API_KEY, REPORTS_DIR  # noqa: E402
from utils.logging import get_logger, set_log_context  # noqa: E402

log = get_logger("validate_anthropic")
REPORT_PATH = REPORTS_DIR / "phase0_anthropic.md"


# ---------------------------------------------------------------------------
# Pricing — published Anthropic list prices, $ per 1M tokens.
# Update if Anthropic changes pricing. Last verified manually 2026-04.
# ---------------------------------------------------------------------------
PRICING_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.00,
        "output": 5.00,
        "cache_write": 1.25,
        "cache_read": 0.10,
    },
    "claude-opus-4-6": {
        "input": 15.00,
        "output": 75.00,
        "cache_write": 18.75,
        "cache_read": 1.50,
    },
}


# ---------------------------------------------------------------------------
# Prompt — representative of what a real research-agent call looks like.
# Roughly mimics the size/shape of a "summarize this signal bundle and emit a
# TradeProposal" call, kept short enough that 5 round-trips per model costs
# pennies.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a quantitative research analyst for a paper-trading system called "
    "Bull-Bot. You analyze multi-timeframe technical signals and emit strict "
    "JSON. Never include prose outside the JSON object. Never speculate beyond "
    "what the data supports. Conviction is on a 0-100 scale; below 60 means do "
    "not trade."
)

USER_PROMPT = """Analyze this multi-timeframe signal bundle for TSLA and respond with a single JSON object.

INPUT BUNDLE
============
ticker: TSLA
asof: 2026-04-09T20:00:00Z
last_price: 344.94
session: post-market

Daily (1d, last 20 bars):
  trend: up — 50DMA above 200DMA, slope +1.2%/day
  rsi_14: 62 (rising from 48 over 5 days)
  macd: bullish cross 3 days ago, histogram expanding
  atr_14: 9.10
  key_levels: support 332.5, resistance 351.2

4-hour (4h, last 30 bars):
  trend: up — making higher highs and higher lows since 2026-04-02
  rsi_14: 68 (approaching overbought)
  vwap_anchor: price 1.3% above 5-day anchored VWAP
  volume_profile: above-average volume on green candles

1-hour (1h, last 50 bars):
  pattern: pullback to rising 20EMA
  rsi_14: 55
  recent_breakout: failed retest of 348 — pulled back, holding above 343

Options flow snapshot (last 30 min):
  call_premium: $4.2M  put_premium: $1.1M  ratio: 3.8:1
  largest_strike: 350C 2026-04-17 (premium $1.1M)
  iv_rank_30d: 38

GEX context:
  zero_gamma: 340.5
  largest_call_wall: 355
  largest_put_wall: 330

REQUIRED JSON SCHEMA
====================
{
  "ticker": "TSLA",
  "stance": "long" | "short" | "neutral",
  "conviction": <integer 0-100>,
  "primary_thesis": "<one sentence>",
  "key_supporting_signals": ["<short bullet>", "<short bullet>", "<short bullet>"],
  "key_risks": ["<short bullet>", "<short bullet>"],
  "suggested_entry": <number or null>,
  "suggested_stop": <number or null>,
  "suggested_target": <number or null>,
  "rr": <number or null>
}

Return only the JSON object — no preamble, no code fences."""


# ---------------------------------------------------------------------------
# Per-call result
# ---------------------------------------------------------------------------
@dataclass
class CallResult:
    model: str
    call_index: int
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_usd: float
    response_text_preview: str
    json_valid: bool
    error: str | None = None
    stop_reason: str | None = None


def cost_for_call(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_creation: int,
) -> float:
    pricing = PRICING_USD_PER_MTOK.get(model)
    if not pricing:
        return 0.0
    return (
        input_tokens * pricing["input"]
        + output_tokens * pricing["output"]
        + cache_creation * pricing["cache_write"]
        + cache_read * pricing["cache_read"]
    ) / 1_000_000


def call_once(
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
    call_index: int,
) -> CallResult:
    set_log_context(model=model, call=call_index)
    t0 = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": USER_PROMPT}],
        )
    except anthropic.APIError as e:
        latency_ms = (time.monotonic() - t0) * 1000
        log.error("API error: %s", e)
        return CallResult(
            model=model,
            call_index=call_index,
            latency_ms=round(latency_ms, 1),
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd=0.0,
            response_text_preview="",
            json_valid=False,
            error=f"{type(e).__name__}: {e}",
        )
    finally:
        set_log_context(model=None, call=None)

    latency_ms = (time.monotonic() - t0) * 1000
    usage = response.usage
    input_t = getattr(usage, "input_tokens", 0) or 0
    output_t = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0

    text = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text += block.text

    json_valid = False
    try:
        json.loads(text.strip())
        json_valid = True
    except Exception:
        json_valid = False

    return CallResult(
        model=model,
        call_index=call_index,
        latency_ms=round(latency_ms, 1),
        input_tokens=input_t,
        output_tokens=output_t,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        cost_usd=cost_for_call(model, input_t, output_t, cache_read, cache_creation),
        response_text_preview=text[:240],
        json_valid=json_valid,
        stop_reason=getattr(response, "stop_reason", None),
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
@dataclass
class ModelStats:
    model: str
    n_calls: int
    n_success: int
    n_json_valid: int
    p50_latency_ms: float
    p90_latency_ms: float
    p99_latency_ms: float
    mean_latency_ms: float
    mean_input_tokens: float
    mean_output_tokens: float
    mean_cost_usd: float
    total_cost_usd: float
    errors: list[str] = field(default_factory=list)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * pct
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize(model: str, calls: list[CallResult]) -> ModelStats:
    successes = [c for c in calls if c.error is None]
    latencies = [c.latency_ms for c in successes]
    return ModelStats(
        model=model,
        n_calls=len(calls),
        n_success=len(successes),
        n_json_valid=sum(1 for c in calls if c.json_valid),
        p50_latency_ms=round(percentile(latencies, 0.50), 1),
        p90_latency_ms=round(percentile(latencies, 0.90), 1),
        p99_latency_ms=round(percentile(latencies, 0.99), 1),
        mean_latency_ms=round(statistics.fmean(latencies), 1) if latencies else 0.0,
        mean_input_tokens=(
            round(statistics.fmean(c.input_tokens for c in successes), 1)
            if successes
            else 0.0
        ),
        mean_output_tokens=(
            round(statistics.fmean(c.output_tokens for c in successes), 1)
            if successes
            else 0.0
        ),
        mean_cost_usd=(
            round(statistics.fmean(c.cost_usd for c in successes), 6)
            if successes
            else 0.0
        ),
        total_cost_usd=round(sum(c.cost_usd for c in calls), 6),
        errors=[c.error for c in calls if c.error],
    )


# ---------------------------------------------------------------------------
# Cost projection (Bull-Bot scenarios)
# ---------------------------------------------------------------------------
@dataclass
class ScenarioProjection:
    name: str
    description: str
    sonnet_calls: int
    haiku_calls: int
    cost_usd: float


def project_scenarios(stats_by_model: dict[str, ModelStats]) -> list[ScenarioProjection]:
    """Project Bull-Bot operating cost using ARCHITECTURE.md §6.5's call model.

    The architecture is already aggressively optimized — this projector now
    matches it instead of inventing its own. Sources of truth:

      - §5.2 Research agents: 15m/1h/4h/1d use Haiku, 1w uses Sonnet.
      - §5.3 Decision agent: 5 Sonnet calls/day total (one snapshot per
        cursor tick processes all eligible tickers — gated by deterministic
        confluence score per Appendix A.1).
      - §6.5 Tiered bootstrap: Tier 1 (1d Haiku) + Tier 2 (1w Sonnet) +
        decision sample run upfront. Tier 3 intraday (4h/1h/15m research) is
        DEFERRED — the cache fills organically from live operation in ~3
        months. CHEAP-mode walk-forwards then cost near-zero.

    Call counts come straight from §6.5's full-bootstrap table (27 tickers,
    1260 trading days = 5 years).
    """
    sonnet = stats_by_model.get("claude-sonnet-4-6")
    haiku = stats_by_model.get("claude-haiku-4-5-20251001")
    sc = sonnet.mean_cost_usd if sonnet else 0.0
    hc = haiku.mean_cost_usd if haiku else 0.0

    # ----- Architecture call counts (§6.5 cost table) -----
    ARCH_BOOTSTRAP_CALLS: dict[str, tuple[str, int]] = {
        "research_1w_sonnet": ("sonnet", 14_040),  # 520 weeks × 27 tickers
        "research_1d_haiku":  ("haiku", 34_020),   # 1,260 days × 27 tickers
        "research_4h_haiku":  ("haiku", 121_500),
        "research_1h_haiku":  ("haiku", 94_500),
        "research_15m_haiku": ("haiku", 175_500),
        "decision_sonnet":    ("sonnet", 6_300),   # 5/day × 1,260 days
    }

    def cost_for(model: str, n: int) -> float:
        return n * (sc if model == "sonnet" else hc)

    # ----- Per-segment costs -----
    seg_cost: dict[str, float] = {
        k: cost_for(model, n) for k, (model, n) in ARCH_BOOTSTRAP_CALLS.items()
    }

    tier1_cost = seg_cost["research_1d_haiku"]
    tier2_cost = seg_cost["research_1w_sonnet"]
    tier3_cost = (
        seg_cost["research_4h_haiku"]
        + seg_cost["research_1h_haiku"]
        + seg_cost["research_15m_haiku"]
    )
    decision_cost_full = seg_cost["decision_sonnet"]

    approved_bootstrap = tier1_cost + tier2_cost + decision_cost_full
    full_bootstrap = sum(seg_cost.values())

    # ----- Daily live ops (steady state, post-bootstrap) -----
    # Per the architecture's per-day cadence:
    #   Research 1d: 27 tickers × 1 Haiku call/day
    #   Research 1w: 27 tickers × 1 Sonnet call/week (1/7 amortized per day)
    #   Decision:    5 Sonnet calls/day total
    #   Tier 3 intraday research: ALSO running live (cache fills as you go).
    #     Per day = the §6.5 totals divided by 1,260 backtest days.
    n_tickers_live = 27
    daily_research_1d_haiku = n_tickers_live  # 27/day
    daily_research_1w_sonnet = n_tickers_live / 7  # ~3.86/day amortized
    daily_decision_sonnet = 5
    daily_research_4h_haiku = ARCH_BOOTSTRAP_CALLS["research_4h_haiku"][1] / 1260
    daily_research_1h_haiku = ARCH_BOOTSTRAP_CALLS["research_1h_haiku"][1] / 1260
    daily_research_15m_haiku = ARCH_BOOTSTRAP_CALLS["research_15m_haiku"][1] / 1260

    daily_haiku_calls_intraday = (
        daily_research_4h_haiku
        + daily_research_1h_haiku
        + daily_research_15m_haiku
    )
    daily_haiku_calls_total = daily_research_1d_haiku + daily_haiku_calls_intraday
    daily_sonnet_calls_total = daily_research_1w_sonnet + daily_decision_sonnet

    daily_cost_lite = (  # tier 1+2 only (no intraday research)
        daily_research_1d_haiku * hc
        + daily_research_1w_sonnet * sc
        + daily_decision_sonnet * sc
    )
    daily_cost_full = (  # all tiers running live
        daily_haiku_calls_total * hc + daily_sonnet_calls_total * sc
    )

    # ----- Walk-forward proposal validation cost (CHEAP mode) -----
    # Per §6.5: numeric proposals in CHEAP mode = near zero. Prompt-changing
    # proposals trigger targeted FULL replays at $20-50 each. We model the
    # average proposal cost as 1/30 of a full bootstrap replay window.
    avg_proposal_cost = full_bootstrap / 30  # rough — re-do 1 month equivalent

    return [
        ScenarioProjection(
            name="Per-call sample (1 sonnet research + 1 sonnet decision)",
            description=f"avg cost from {sonnet.n_success if sonnet else 0} samples",
            sonnet_calls=2,
            haiku_calls=0,
            cost_usd=round(2 * sc, 6),
        ),
        ScenarioProjection(
            name="Tier 1 — 1d research bootstrap (Haiku)",
            description=(
                f"{ARCH_BOOTSTRAP_CALLS['research_1d_haiku'][1]:,} Haiku calls "
                f"= 1,260 trading days × 27 tickers"
            ),
            sonnet_calls=0,
            haiku_calls=ARCH_BOOTSTRAP_CALLS["research_1d_haiku"][1],
            cost_usd=round(tier1_cost, 2),
        ),
        ScenarioProjection(
            name="Tier 2 — 1w research bootstrap (Sonnet)",
            description=(
                f"{ARCH_BOOTSTRAP_CALLS['research_1w_sonnet'][1]:,} Sonnet calls "
                f"= 520 weeks × 27 tickers"
            ),
            sonnet_calls=ARCH_BOOTSTRAP_CALLS["research_1w_sonnet"][1],
            haiku_calls=0,
            cost_usd=round(tier2_cost, 2),
        ),
        ScenarioProjection(
            name="Decision agent bootstrap sample (Sonnet)",
            description=f"{ARCH_BOOTSTRAP_CALLS['decision_sonnet'][1]:,} Sonnet calls = 5/day × 1,260 days",
            sonnet_calls=ARCH_BOOTSTRAP_CALLS["decision_sonnet"][1],
            haiku_calls=0,
            cost_usd=round(decision_cost_full, 2),
        ),
        ScenarioProjection(
            name="✅ APPROVED BOOTSTRAP (Tier 1 + Tier 2 + decision)",
            description="The one-time spend authorized in §6.5; intraday research deferred",
            sonnet_calls=ARCH_BOOTSTRAP_CALLS["research_1w_sonnet"][1]
            + ARCH_BOOTSTRAP_CALLS["decision_sonnet"][1],
            haiku_calls=ARCH_BOOTSTRAP_CALLS["research_1d_haiku"][1],
            cost_usd=round(approved_bootstrap, 2),
        ),
        ScenarioProjection(
            name="Tier 3 — Intraday research (DEFERRED, fills from live)",
            description=(
                f"{ARCH_BOOTSTRAP_CALLS['research_4h_haiku'][1] + ARCH_BOOTSTRAP_CALLS['research_1h_haiku'][1] + ARCH_BOOTSTRAP_CALLS['research_15m_haiku'][1]:,} Haiku calls if run upfront"
            ),
            sonnet_calls=0,
            haiku_calls=(
                ARCH_BOOTSTRAP_CALLS["research_4h_haiku"][1]
                + ARCH_BOOTSTRAP_CALLS["research_1h_haiku"][1]
                + ARCH_BOOTSTRAP_CALLS["research_15m_haiku"][1]
            ),
            cost_usd=round(tier3_cost, 2),
        ),
        ScenarioProjection(
            name="Full bootstrap (all tiers — for reference, NOT approved)",
            description="Worst-case if Tier 3 were also paid upfront",
            sonnet_calls=ARCH_BOOTSTRAP_CALLS["research_1w_sonnet"][1]
            + ARCH_BOOTSTRAP_CALLS["decision_sonnet"][1],
            haiku_calls=ARCH_BOOTSTRAP_CALLS["research_1d_haiku"][1]
            + ARCH_BOOTSTRAP_CALLS["research_4h_haiku"][1]
            + ARCH_BOOTSTRAP_CALLS["research_1h_haiku"][1]
            + ARCH_BOOTSTRAP_CALLS["research_15m_haiku"][1],
            cost_usd=round(full_bootstrap, 2),
        ),
        ScenarioProjection(
            name="Daily live ops (Tier 1+2 only — first ~3 months)",
            description=(
                f"~{daily_research_1d_haiku:.0f} Haiku/day (1d research) + "
                f"~{daily_research_1w_sonnet:.1f} Sonnet/day (1w research, amortized) + "
                f"{daily_decision_sonnet} Sonnet/day (decision)"
            ),
            sonnet_calls=int(round(daily_sonnet_calls_total)),
            haiku_calls=int(round(daily_research_1d_haiku)),
            cost_usd=round(daily_cost_lite, 4),
        ),
        ScenarioProjection(
            name="Daily live ops (steady state — all tiers)",
            description=(
                f"~{daily_haiku_calls_total:.0f} Haiku + "
                f"~{daily_sonnet_calls_total:.1f} Sonnet calls/day (incl. intraday research)"
            ),
            sonnet_calls=int(round(daily_sonnet_calls_total)),
            haiku_calls=int(round(daily_haiku_calls_total)),
            cost_usd=round(daily_cost_full, 4),
        ),
        ScenarioProjection(
            name="Annual live ops (steady state, ~252 trading days)",
            description="Daily steady-state × 252",
            sonnet_calls=int(round(daily_sonnet_calls_total * 252)),
            haiku_calls=int(round(daily_haiku_calls_total * 252)),
            cost_usd=round(daily_cost_full * 252, 2),
        ),
        ScenarioProjection(
            name="Avg evolver proposal (CHEAP mode + targeted FULL replay)",
            description="Numeric proposals near-zero; prompt-changing proposals re-replay ~1 month worth",
            sonnet_calls=0,
            haiku_calls=0,
            cost_usd=round(avg_proposal_cost, 2),
        ),
    ]


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------
def write_report(
    stats_by_model: dict[str, ModelStats],
    calls: list[CallResult],
    scenarios: list[ScenarioProjection],
    n_per_model: int,
) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    overall_ok = all(s.n_success == s.n_calls for s in stats_by_model.values())
    header_status = "✅ PASS" if overall_ok else "❌ PARTIAL"

    lines: list[str] = []
    lines.append("# Phase 0 — Anthropic API Validation")
    lines.append("")
    lines.append(f"**Generated:** {now}  ")
    lines.append(f"**Overall:** {header_status}  ")
    lines.append(f"**Calls per model:** {n_per_model}  ")
    lines.append(f"**Total API calls:** {len(calls)}  ")
    lines.append("")

    # Model summary
    lines.append("## Per-Model Summary")
    lines.append("")
    lines.append(
        "| Model | Success | JSON Valid | p50 (ms) | p90 (ms) | p99 (ms) | "
        "Mean In Tokens | Mean Out Tokens | Mean $/call | Total $ |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for model, s in stats_by_model.items():
        lines.append(
            f"| `{model}` | {s.n_success}/{s.n_calls} | {s.n_json_valid}/{s.n_calls} | "
            f"{s.p50_latency_ms} | {s.p90_latency_ms} | {s.p99_latency_ms} | "
            f"{s.mean_input_tokens} | {s.mean_output_tokens} | "
            f"${s.mean_cost_usd:.6f} | ${s.total_cost_usd:.4f} |"
        )
    lines.append("")

    # Pricing assumptions
    lines.append("## Pricing Assumptions ($ per 1M tokens)")
    lines.append("")
    lines.append("| Model | Input | Output | Cache Write | Cache Read |")
    lines.append("|---|---|---|---|---|")
    for model in stats_by_model.keys():
        p = PRICING_USD_PER_MTOK.get(model, {})
        lines.append(
            f"| `{model}` | ${p.get('input',0):.2f} | ${p.get('output',0):.2f} | "
            f"${p.get('cache_write',0):.2f} | ${p.get('cache_read',0):.2f} |"
        )
    lines.append("")
    lines.append(
        "> _Update `PRICING_USD_PER_MTOK` in the script if Anthropic changes rates._"
    )
    lines.append("")

    # Cost projection
    lines.append("## Cost Projection — Bull-Bot Scenarios")
    lines.append("")
    lines.append("| Scenario | Sonnet Calls | Haiku Calls | Estimated Cost |")
    lines.append("|---|---|---|---|")
    for sc in scenarios:
        lines.append(
            f"| {sc.name} | {sc.sonnet_calls:,} | {sc.haiku_calls:,} | "
            f"${sc.cost_usd:,.2f} |"
        )
    lines.append("")
    lines.append("> _Source of truth: ARCHITECTURE.md §5.2, §5.3, §6.5._")
    lines.append("> ")
    lines.append(
        "> - **Research model mix:** 15m / 1h / 4h / 1d use Haiku; 1w uses Sonnet (§5.2)"
    )
    lines.append(
        "> - **Decision agent:** 5 Sonnet calls/day total — single snapshot per cursor "
        "tick processes all eligible tickers, gated by deterministic confluence score "
        "(§5.3 + Appendix A.1)"
    )
    lines.append(
        "> - **Tier 1+2 bootstrap is the approved one-time spend.** Tier 3 intraday "
        "research is deferred — the cache fills organically from ~3 months of live "
        "operation (§6.5)"
    )
    lines.append(
        "> - **Subsequent walk-forwards run in CHEAP mode** at near-zero cost using "
        "cached LLM outputs; only prompt changes trigger targeted FULL replays"
    )
    lines.append(
        "> - **Production will add prompt caching** (~10-20% input savings on this "
        "prompt size) — projections below are uncached"
    )
    lines.append("")

    # Per-call detail
    lines.append("## Per-Call Detail")
    lines.append("")
    lines.append(
        "| # | Model | Latency (ms) | In Tokens | Out Tokens | Cost | JSON | Stop |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for c in calls:
        json_mark = "✅" if c.json_valid else "❌"
        lines.append(
            f"| {c.call_index} | `{c.model.split('-')[1]}` | {c.latency_ms} | "
            f"{c.input_tokens} | {c.output_tokens} | ${c.cost_usd:.6f} | "
            f"{json_mark} | {c.stop_reason or '—'} |"
        )
    lines.append("")

    # Sample response
    lines.append("## Sample Response (first successful sonnet call)")
    lines.append("")
    sonnet_sample = next(
        (c for c in calls if "sonnet" in c.model and c.error is None), None
    )
    if sonnet_sample:
        lines.append("```")
        lines.append(sonnet_sample.response_text_preview)
        lines.append("```")
    else:
        lines.append("_No successful sonnet calls._")
    lines.append("")

    # Errors (if any)
    errors = [c for c in calls if c.error]
    if errors:
        lines.append("## Errors")
        lines.append("")
        for c in errors:
            lines.append(f"- call {c.call_index} `{c.model}`: `{c.error}`")
        lines.append("")

    lines.append("---")
    lines.append("_Generated by `scripts/validate_anthropic.py`. Re-run anytime; the report is overwritten._")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    log.info("wrote report to %s", REPORT_PATH)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 0 Anthropic API validation")
    parser.add_argument("--n", type=int, default=5, help="Calls per model (default 5).")
    parser.add_argument(
        "--max-tokens", type=int, default=600, help="max_tokens for each call (default 600)."
    )
    parser.add_argument(
        "--models",
        type=str,
        default="claude-sonnet-4-6,claude-haiku-4-5-20251001",
        help="Comma-separated model IDs to test.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        import logging as _logging
        _logging.getLogger().setLevel(_logging.DEBUG)

    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY is empty — populate .env first.")
        return 2

    set_log_context(script="validate_anthropic", run_id="phase0")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    log.info("starting Anthropic validation: models=%s n=%d", models, args.n)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    all_calls: list[CallResult] = []
    stats_by_model: dict[str, ModelStats] = {}

    for model in models:
        if model not in PRICING_USD_PER_MTOK:
            log.warning(
                "no pricing entry for %s — cost will be reported as $0. Add it to "
                "PRICING_USD_PER_MTOK.",
                model,
            )
        log.info("calling %s × %d", model, args.n)
        model_calls: list[CallResult] = []
        for i in range(1, args.n + 1):
            log.info("%s call %d/%d", model, i, args.n)
            result = call_once(client, model, args.max_tokens, i)
            model_calls.append(result)
            all_calls.append(result)
            log.info(
                "  -> %s tokens=%d/%d cost=$%.6f json=%s",
                f"{result.latency_ms:.0f}ms",
                result.input_tokens,
                result.output_tokens,
                result.cost_usd,
                "✓" if result.json_valid else "✗",
            )
        stats_by_model[model] = summarize(model, model_calls)

    scenarios = project_scenarios(stats_by_model)
    write_report(stats_by_model, all_calls, scenarios, args.n)

    debug_path = REPORT_PATH.with_suffix(".json")
    debug_path.write_text(
        json.dumps(
            {
                "generated": datetime.now(timezone.utc).isoformat(),
                "models": models,
                "n_per_model": args.n,
                "max_tokens": args.max_tokens,
                "stats": {k: asdict(v) for k, v in stats_by_model.items()},
                "calls": [asdict(c) for c in all_calls],
                "scenarios": [asdict(s) for s in scenarios],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    log.info("---- summary ----")
    for model, s in stats_by_model.items():
        log.info(
            "%s  success=%d/%d json=%d/%d p50=%.0fms p99=%.0fms mean$=%.6f total$=%.4f",
            model,
            s.n_success,
            s.n_calls,
            s.n_json_valid,
            s.n_calls,
            s.p50_latency_ms,
            s.p99_latency_ms,
            s.mean_cost_usd,
            s.total_cost_usd,
        )
    log.info("report: %s", REPORT_PATH)
    log.info("debug json: %s", debug_path)

    overall_ok = all(s.n_success == s.n_calls for s in stats_by_model.values())
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
