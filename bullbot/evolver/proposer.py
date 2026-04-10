"""
Evolver proposer — uses an LLM to propose the next strategy variant.

The proposer builds a structured prompt from the current StrategySnapshot,
the past proposal history, and (optionally) the best-known strategy ID, then
calls the configured Anthropic model and parses its JSON response into a
``Proposal`` dataclass.

Config knobs live in ``bullbot.config``:
  PROPOSER_MODEL           — model ID to use
  PROPOSER_MAX_TOKENS      — max output tokens per call
  HISTORY_BLOCK_SIZE       — how many past proposals to include
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from bullbot.config import (
    HISTORY_BLOCK_SIZE,
    PROPOSER_MAX_TOKENS,
    PROPOSER_MODEL,
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

_SYSTEM_PROMPT = """You are an expert algorithmic options trader and quantitative researcher.
Your job is to propose a *single* options strategy variant for the Bull-Bot evolver.

You MUST respond with ONLY a valid JSON object — no prose, no markdown, no code fences.
The JSON must have exactly these three keys:

  "class_name"  — one of the registered strategy class names
  "params"      — a flat dict of strategy parameters (all numeric values)
  "rationale"   — 1-3 sentence justification for this proposal

Registered strategies: {strategy_names}

Example response:
{{
  "class_name": "PutCreditSpread",
  "params": {{"dte": 21, "short_delta": 0.30, "width": 5, "iv_rank_min": 50}},
  "rationale": "Selling premium with defined risk. IV rank above 50 confirms edge."
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

    return f"""=== Market Snapshot ===
Ticker:     {snapshot.ticker}
As-of Unix: {snapshot.asof_ts}
Spot:       {snapshot.spot}
Regime:     {snapshot.regime}
IV Rank:    {snapshot.iv_rank}
Indicators: {json.dumps(snapshot.indicators)}
ATM Greeks: {json.dumps(snapshot.atm_greeks)}

=== Evolver History ===
{history_block}

=== Context ===
{best_note}

Propose the next strategy variant. Output only the JSON object described in your instructions.
"""


def _cost_for_call(input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for one API call.

    Opus 4.6 pricing: $15 / MTok input, $75 / MTok output.
    A minimum floor of $0.001 accounts for API overhead and ensures the
    returned value is always positive (useful even when fake/zero token counts).
    """
    cost = (input_tokens * 15.0 + output_tokens * 75.0) / 1_000_000
    return max(cost, 0.001)


def _extract_text(response: Any) -> str:
    """Pull plain text from an Anthropic (or fake) response object."""
    for block in response.content:
        text = getattr(block, "text", None)
        if text is not None:
            return text
    return ""


def _parse_json(raw: str) -> dict | None:
    """Try to parse *raw* as JSON dict; return None on failure."""
    try:
        data = json.loads(raw.strip())
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
    system_prompt = _SYSTEM_PROMPT.format(
        strategy_names=", ".join(registry.list_all_names())
    )
    user_prompt = build_user_prompt(snapshot, history, best_strategy_id)

    total_input_tokens = 0
    total_output_tokens = 0
    parsed: dict | None = None

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=PROPOSER_MODEL,
                max_tokens=PROPOSER_MAX_TOKENS,
                system=system_prompt,
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
        llm_cost_usd=_cost_for_call(total_input_tokens, total_output_tokens),
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
    )
