"""
Evolver proposer — uses an LLM to propose the next strategy variant.

The proposer builds a structured prompt from the current StrategySnapshot,
the past proposal history, and (optionally) the best-known strategy ID, then
calls the configured Anthropic model and parses its JSON response into a
``Proposal`` dataclass.

Config knobs live in ``bullbot.config``:
  PROPOSER_MODEL           — fallback model when propose() is called without model=
  PROPOSER_MAX_TOKENS      — max output tokens per call
  HISTORY_BLOCK_SIZE       — how many past proposals to include
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from bullbot.config import (
    HISTORY_BLOCK_SIZE,
    PROPOSER_MAX_TOKENS,
)
from bullbot.strategies import registry
from bullbot.strategies.base import StrategySnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProposerJsonError(ValueError):
    """Raised when the LLM response cannot be parsed as valid JSON after retries."""


class ProposerApiError(RuntimeError):
    """Raised on an unrecoverable API-level error."""


class ProposerBudgetError(RuntimeError):
    """Raised when the estimated or actual API cost exceeds the configured ceiling."""


class ProposerUnknownStrategyError(ValueError):
    """Raised when the LLM proposes a class_name not in the strategy registry."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Proposal:
    """Parsed LLM response for one strategy variant."""

    class_name: str
    params: dict[str, Any]
    rationale: str
    llm_cost_usd: float
    input_tokens: int
    output_tokens: int


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_GROWTH_GUIDANCE = """
This ticker is categorized as GROWTH. The growth gate requires:
  CAGR >= 20%, Sortino >= 1.0, max drawdown <= 35%, trade count >= 5.

IMPORTANT: Bearish strategies (BearPutSpread, LongPut) typically produce NEGATIVE
CAGR on growth stocks because the underlying trends upward over time. To pass the
growth gate you almost certainly need a BULLISH strategy.

If the ticker has entries in the long_inventory table (existing LEAPS/shares),
consider CoveredCallOverlay — it sells short-dated calls against those positions
to generate income. Key params: short_delta (0.15-0.40), dte_min (14-30),
dte_max (30-60), coverage_ratio (0.5-1.0), min_rsi (40-55), min_day_return
(0.01-0.03). This works well for generating premium income on beaten-down stocks
where you want to sell into strength.

Otherwise prefer GrowthLEAPS for pure directional exposure.
"""

_INCOME_GUIDANCE = """
This ticker is categorized as INCOME. Focus on premium-selling strategies
(PutCreditSpread, CallCreditSpread, IronCondor, CashSecuredPut) that profit from
time decay.
"""

_SYSTEM_PROMPT = """You are an expert algorithmic options trader and quantitative researcher.
Your job is to propose a *single* options strategy variant for the Bull-Bot evolver.

You MUST respond with ONLY a valid JSON object — no prose, no markdown, no code fences.
The JSON must have exactly these three keys:

  "class_name"  — one of the registered strategy class names
  "params"      — a flat dict of strategy parameters (all numeric values)
  "rationale"   — 1-3 sentence justification for this proposal

The params dict should include BOTH entry params (dte, delta, width, iv_rank_min, etc.)
AND exit params:
  - profit_target_pct: fraction of max profit to close at (e.g. 0.50 = 50%)
  - stop_loss_mult: multiple of credit/debit to stop at (e.g. 2.0 = 2x loss)
  - min_dte_close: close position at this many days to expiry (e.g. 7)

Registered strategies: {strategy_names}

Example response:
{{
  "class_name": "PutCreditSpread",
  "params": {{"dte": 21, "short_delta": 0.30, "width": 5, "iv_rank_min": 50, "profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7}},
  "rationale": "Selling premium with defined risk. 50% profit target captures theta decay efficiently."
}}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_history_block(history: list[dict]) -> str:
    """Format the N most recent past proposals into a compact text block."""
    if not history:
        return "(no prior proposals)"

    recent = history[-HISTORY_BLOCK_SIZE:]
    lines: list[str] = []
    for h in recent:
        gate = "PASSED" if h.get("passed_gate") else "FAILED"
        lines.append(
            f"  iter={h.get('iteration', '?')}  {h.get('class_name', '?')}  "
            f"params={h.get('params', '{}')}  "
            f"pf_is={h.get('pf_is', 'N/A')}  pf_oos={h.get('pf_oos', 'N/A')}  "
            f"trades={h.get('trade_count', '?')}  gate={gate}  "
            f"rationale={h.get('rationale', '')}"
        )
    return "\n".join(lines)


def build_user_prompt(
    snapshot: StrategySnapshot,
    history: list[dict],
    best_strategy_id: str | None,
) -> str:
    """Compose the full user-turn prompt."""
    history_block = build_history_block(history)
    best_note = (
        f"Current best strategy ID: {best_strategy_id}"
        if best_strategy_id
        else "No best strategy identified yet."
    )

    # Regime context — only include if briefs are non-empty
    regime_block = ""
    if snapshot.market_brief:
        regime_block += f"\n=== Market Regime Analysis ===\n{snapshot.market_brief}\n"
    if snapshot.ticker_brief:
        regime_block += f"\n=== Ticker Analysis ({snapshot.ticker}) ===\n{snapshot.ticker_brief}\n"

    return f"""=== Market Snapshot ===
Ticker:     {snapshot.ticker}
As-of Unix: {snapshot.asof_ts}
Spot:       {snapshot.spot}
Regime:     {snapshot.regime}
IV Rank:    {snapshot.iv_rank}
Indicators: {json.dumps(snapshot.indicators)}
ATM Greeks: {json.dumps(snapshot.atm_greeks)}
{regime_block}
=== Evolver History ===
{history_block}

=== Context ===
{best_note}

Propose the next strategy variant. Output only the JSON object described in your instructions.
"""


def _cost_for_call(input_tokens: int, output_tokens: int, model: str) -> float:
    """Estimate USD cost for one API call using per-model pricing.

    Looks up rates in ``config.PROPOSER_MODEL_PRICING``. Falls back to Opus
    rates for unknown models so we never silently zero out a real cost.
    A minimum floor of $0.001 ensures the value is always positive.
    """
    # Lazy import to mirror propose()'s function-scope config access; keeps
    # this module's import graph clean of circular imports through llm.cache.
    from bullbot import config
    in_rate, out_rate = config.PROPOSER_MODEL_PRICING.get(model, (15.0, 75.0))
    cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
    return max(cost, 0.001)


def _extract_text(response: Any) -> str:
    """Pull plain text from an Anthropic (or fake) response object."""
    for block in response.content:
        text = getattr(block, "text", None)
        if text is not None:
            return text
    return ""


def _strip_code_fences(raw: str) -> str:
    """Remove markdown code fences if present."""
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
    return m.group(1).strip() if m else raw.strip()


def _parse_json(raw: str) -> dict | None:
    """Try to parse *raw* as JSON dict; return None on failure."""
    try:
        data = json.loads(_strip_code_fences(raw))
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def propose(
    client: Any,
    snapshot: StrategySnapshot,
    history: list[dict],
    best_strategy_id: str | None,
    category: str = "income",
    model: str | None = None,
) -> Proposal:
    """Call the LLM and return a parsed, validated ``Proposal``.

    Retries once if the first response is not valid JSON.

    Raises
    ------
    ProposerJsonError
        If two consecutive responses fail to parse as valid JSON.
    ProposerUnknownStrategyError
        If the parsed class_name is not in the strategy registry.
    ProposerApiError
        On unexpected API errors.
    """
    from bullbot.llm import cache as llm_cache
    from bullbot import config as bb_config

    effective_model = model if model is not None else bb_config.PROPOSER_MODEL

    guidance = _GROWTH_GUIDANCE if category == "growth" else _INCOME_GUIDANCE
    system_prompt = _SYSTEM_PROMPT.format(
        strategy_names=", ".join(registry.list_all_names())
    ) + guidance
    user_prompt = build_user_prompt(snapshot, history, best_strategy_id)

    # Build cached/uncached system arg per config
    if bb_config.PROPOSER_CACHE_ENABLED:
        system_arg = llm_cache.build_system_blocks([system_prompt])
    else:
        system_arg = system_prompt

    total_input_tokens = 0
    total_output_tokens = 0
    parsed: dict | None = None

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=effective_model,
                max_tokens=PROPOSER_MAX_TOKENS,
                system=system_arg,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as exc:
            raise ProposerApiError(f"Anthropic API call failed: {exc}") from exc

        usage = response.usage
        total_input_tokens += usage.input_tokens
        total_output_tokens += usage.output_tokens

        raw_text = _extract_text(response)
        parsed = _parse_json(raw_text)

        if parsed is not None:
            break

        logger.warning(
            "Proposer attempt %d/%d: could not parse JSON from response: %r",
            attempt + 1, 2, raw_text[:200],
        )

    if parsed is None:
        raise ProposerJsonError(
            "LLM returned non-JSON on both attempts; giving up."
        )

    class_name = parsed.get("class_name", "")
    if class_name not in registry.list_all_names():
        raise ProposerUnknownStrategyError(
            f"Unknown strategy class: {class_name!r}. "
            f"Known: {registry.list_all_names()}"
        )

    return Proposal(
        class_name=class_name,
        params=parsed.get("params", {}),
        rationale=parsed.get("rationale", ""),
        llm_cost_usd=_cost_for_call(total_input_tokens, total_output_tokens, effective_model),
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
    )
