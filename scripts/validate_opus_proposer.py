"""
Phase 0a — validate Claude Opus 4.6 as the Bull-Bot v3 evolver proposer model.

Runs 5 calls against `claude-opus-4-6` with a representative v3 evolver
proposer prompt. Measures:
  - JSON validity of each response (must be 5/5 to lock Opus)
  - Mean / p50 / p90 latency
  - Mean / total cost (cross-referenced against published Opus pricing)
  - Token counts (input + output)

The prompt is a realistic v3 evolver iteration: feature snapshot for SPY +
history of past proposals with backtest verdicts + a request for the next
proposal in strict JSON. This is the ONLY LLM call site in the v3 design,
so this probe is the whole thing.

Outputs:
  * reports/phase0a_opus_proposer.md
  * reports/phase0a_opus_proposer.json
"""

from __future__ import annotations

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

log = get_logger("validate_opus_proposer")

MODEL = "claude-opus-4-6"
N_CALLS = 5
MAX_TOKENS = 2000
REPORT_MD = REPORTS_DIR / "phase0a_opus_proposer.md"
REPORT_JSON = REPORTS_DIR / "phase0a_opus_proposer.json"

# Opus 4.6 published pricing per 1M tokens (last verified 2026-04)
PRICING_INPUT_USD_PER_MTOK = 15.00
PRICING_OUTPUT_USD_PER_MTOK = 75.00

SYSTEM_PROMPT = """You are the strategy proposer inside Bull-Bot v3, an automated options-strategy discovery system. Your job is to propose the next options strategy to backtest for a specific ticker, given:

1. A feature snapshot of the ticker at the current moment (OHLC, technical indicators, ATM greeks, IV rank)
2. A history of past proposals for this ticker with their backtest verdicts
3. The edge gate: PF_is >= 1.5 AND PF_oos >= 1.3 AND n_trades >= 30 on anchored 70/30 walk-forward

You MUST emit a single JSON object matching this exact schema:
{
  "class_name": "PutCreditSpread" | "CallCreditSpread" | "IronCondor" | "CashSecuredPut" | "LongCall" | "LongPut",
  "params": <object with strategy-specific parameters>,
  "rationale": "<one to three sentences explaining why this proposal addresses what past proposals got wrong>"
}

Rules:
- Learn from past failures. If a PutCreditSpread with dte=14, delta=0.25 failed, proposing the same shape is wasteful.
- Favor structurally different proposals when the last 3 all failed for similar reasons.
- Your rationale must reference at least one past proposal by iteration number when history is non-empty.
- Output ONLY the JSON object. No prose, no markdown fences, no preamble."""

USER_PROMPT = """TICKER: SPY
ASOF: 2026-04-09T20:00:00Z

=== FEATURE SNAPSHOT ===

Daily (1d, last 60 bars):
  close: 582.14  open: 580.25  high: 584.30  low: 579.10
  sma_20: 578.45  sma_50: 571.22  sma_200: 545.80
  ema_20: 579.80
  rsi_14: 58.4 (rising from 52 over 5 days)
  atr_14: 4.82
  bb_upper_20: 588.1  bb_lower_20: 568.8  bb_width: 3.3%

ATM greeks (expiry 2026-04-17, 8 DTE):
  atm_call_delta: 0.52  atm_call_theta: -0.31  atm_call_vega: 0.44
  atm_put_delta: -0.48  atm_put_theta: -0.29  atm_put_vega: 0.44
  atm_call_iv: 0.143  atm_put_iv: 0.146

IV context (SPY, 30d lookback):
  iv_rank: 34
  iv_percentile: 38
  implied_move_7d: +/- 1.8%

Regime: bull (rolling_60d_return=+6.2%, rolling_30d_vol=0.14)

=== PAST PROPOSAL HISTORY (most recent first) ===

iter=4  PutCreditSpread{dte=14, short_delta=0.30, width=5, iv_rank_min=50}
        PF_is=1.12 PF_oos=0.88 n_trades=44 regime={bull:1.2, bear:0.7, chop:0.9}
        passed_gate=NO (OOS gate fail)
        rationale: "Low-IV regime, try wider delta to capture more premium"

iter=3  IronCondor{dte=21, wing_delta=0.15, wing_width=5, iv_rank_min=60}
        PF_is=0.94 PF_oos=0.81 n_trades=38 regime={bull:0.8, bear:0.9, chop:1.1}
        passed_gate=NO (IS gate fail)
        rationale: "High IV rank requirement wasn't met often enough; too few trades"

iter=2  PutCreditSpread{dte=14, short_delta=0.25, width=5, iv_rank_min=50}
        PF_is=1.18 PF_oos=0.96 n_trades=46 regime={bull:1.3, bear:0.7, chop:0.9}
        passed_gate=NO (both gates fail, borderline OOS)
        rationale: "Standard credit spread shape as baseline"

iter=1  IronCondor{dte=30, wing_delta=0.20, wing_width=10, iv_rank_min=40}
        PF_is=1.05 PF_oos=0.72 n_trades=28 regime={bull:0.6, bear:1.0, chop:1.4}
        passed_gate=NO (n_trades below 30, OOS degradation)
        rationale: "Seed proposal, wide wings for safety"

=== TASK ===

Propose iteration 5. Current best_pf_oos is 0.96 from iter=2. Plateau counter is 2 (no improvement for 2 iterations). Regime is bull with moderate IV rank. Emit JSON only.
"""


@dataclass
class CallResult:
    call_index: int
    ok: bool
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    json_valid: bool
    response_preview: str
    error: str | None = None


def call_once(client: anthropic.Anthropic, call_index: int) -> CallResult:
    set_log_context(call=call_index)
    t0 = time.monotonic()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": USER_PROMPT}],
        )
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        log.error("API error: %s", e)
        return CallResult(
            call_index=call_index,
            ok=False,
            latency_ms=round(latency, 1),
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            json_valid=False,
            response_preview="",
            error=f"{type(e).__name__}: {e}",
        )
    finally:
        set_log_context(call=None)

    latency = (time.monotonic() - t0) * 1000
    usage = response.usage
    input_t = getattr(usage, "input_tokens", 0) or 0
    output_t = getattr(usage, "output_tokens", 0) or 0

    text = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text += block.text

    json_valid = False
    parsed: Any = None
    try:
        parsed = json.loads(text.strip())
        json_valid = True
    except Exception:
        json_valid = False

    cost = (
        input_t * PRICING_INPUT_USD_PER_MTOK
        + output_t * PRICING_OUTPUT_USD_PER_MTOK
    ) / 1_000_000

    # Structural validation of the parsed JSON
    if json_valid and isinstance(parsed, dict):
        required = {"class_name", "params", "rationale"}
        if not required.issubset(parsed.keys()):
            json_valid = False

    return CallResult(
        call_index=call_index,
        ok=True,
        latency_ms=round(latency, 1),
        input_tokens=input_t,
        output_tokens=output_t,
        cost_usd=cost,
        json_valid=json_valid,
        response_preview=text[:400],
    )


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


def write_reports(results: list[CallResult]) -> None:
    successes = [r for r in results if r.ok]
    latencies = [r.latency_ms for r in successes]
    json_valid_count = sum(1 for r in results if r.json_valid)
    total_cost = sum(r.cost_usd for r in results)
    mean_input = (
        statistics.fmean(r.input_tokens for r in successes) if successes else 0.0
    )
    mean_output = (
        statistics.fmean(r.output_tokens for r in successes) if successes else 0.0
    )
    mean_cost = statistics.fmean(r.cost_usd for r in successes) if successes else 0.0

    pass_overall = (
        len(successes) == len(results)
        and json_valid_count == len(results)
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    lines: list[str] = [
        "# Phase 0a — Opus 4.6 Proposer Validation",
        "",
        f"**Generated:** {now}",
        f"**Model:** `{MODEL}`",
        f"**Calls:** {len(results)}",
        f"**Overall:** {'PASS ✅' if pass_overall else 'FAIL ❌'}",
        "",
        "## Summary",
        "",
        f"- **Successful calls:** {len(successes)}/{len(results)}",
        f"- **JSON valid (structural):** {json_valid_count}/{len(results)}",
        f"- **p50 latency:** {percentile(latencies, 0.50):.0f} ms",
        f"- **p90 latency:** {percentile(latencies, 0.90):.0f} ms",
        f"- **Mean latency:** {(statistics.fmean(latencies) if latencies else 0):.0f} ms",
        f"- **Mean input tokens:** {mean_input:.0f}",
        f"- **Mean output tokens:** {mean_output:.0f}",
        f"- **Mean $/call:** ${mean_cost:.4f}",
        f"- **Total cost:** ${total_cost:.4f}",
        "",
        "## Per-call detail",
        "",
        "| # | latency (ms) | in tok | out tok | $/call | JSON valid |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.call_index} | {r.latency_ms:.0f} | {r.input_tokens} | "
            f"{r.output_tokens} | ${r.cost_usd:.4f} | "
            f"{'YES' if r.json_valid else 'NO'} |"
        )
    lines.append("")
    lines.append("## Sample response (call 1)")
    lines.append("")
    lines.append("```json")
    lines.append(results[0].response_preview if results else "(no data)")
    lines.append("```")
    lines.append("")
    lines.append("## Cost projection")
    lines.append("")
    lines.append(
        "At measured mean cost per call, 50 iterations per ticker "
        f"(plateau safety cap) across the 10-ticker universe = "
        f"{50 * 10 * mean_cost:.2f} USD for a full discovery cycle."
    )
    lines.append("")
    lines.append(
        "Against the $1,000 research-ratthole kill threshold, that leaves "
        f"{(1000 / (mean_cost if mean_cost > 0 else 1)):.0f} iterations of headroom "
        "before the kill switch would fire."
    )
    lines.append("")
    if pass_overall:
        lines.append("## Conclusion")
        lines.append("")
        lines.append(
            "**Opus 4.6 validated as Bull-Bot v3 proposer.** "
            "All 5 calls succeeded and produced structurally valid JSON. "
            "PROPOSER_MODEL = 'claude-opus-4-6' is locked for Stage 1 build."
        )
    else:
        lines.append("## Conclusion")
        lines.append("")
        lines.append(
            "**Opus 4.6 FAILED validation.** Flip PROPOSER_MODEL to "
            "'claude-sonnet-4-6' (already documented as fallback) and "
            "rerun this probe against Sonnet for confirmation."
        )

    REPORT_MD.write_text("\n".join(lines))
    REPORT_JSON.write_text(
        json.dumps(
            {
                "model": MODEL,
                "generated_at": now,
                "pass": pass_overall,
                "calls": [asdict(r) for r in results],
                "summary": {
                    "n_calls": len(results),
                    "n_success": len(successes),
                    "n_json_valid": json_valid_count,
                    "p50_latency_ms": percentile(latencies, 0.50),
                    "p90_latency_ms": percentile(latencies, 0.90),
                    "mean_latency_ms": statistics.fmean(latencies) if latencies else 0.0,
                    "mean_input_tokens": mean_input,
                    "mean_output_tokens": mean_output,
                    "mean_cost_usd": mean_cost,
                    "total_cost_usd": total_cost,
                },
            },
            indent=2,
            default=str,
        )
    )


def main() -> int:
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY is empty")
        return 2

    set_log_context(script="validate_opus_proposer", run_id="phase0a")
    log.info("starting Opus proposer probe, n=%d", N_CALLS)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    results: list[CallResult] = []
    for i in range(1, N_CALLS + 1):
        log.info("call %d/%d", i, N_CALLS)
        r = call_once(client, i)
        results.append(r)
        log.info(
            "  latency=%.0fms tokens=%d/%d cost=$%.4f json=%s error=%s",
            r.latency_ms,
            r.input_tokens,
            r.output_tokens,
            r.cost_usd,
            "YES" if r.json_valid else "NO",
            r.error,
        )

    write_reports(results)

    pass_overall = (
        all(r.ok for r in results) and all(r.json_valid for r in results)
    )
    log.info("phase0a complete pass=%s report=%s", pass_overall, REPORT_MD)
    return 0 if pass_overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
