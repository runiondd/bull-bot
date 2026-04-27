"""Anthropic prompt-caching helpers.

Anthropic's API supports `cache_control: {"type": "ephemeral"}` on individual
content blocks. The cache covers everything from the start of the prompt up
to and including the marked block (it's cumulative, not block-specific).
First call writes the cache; subsequent calls within ~5 minutes hit it for
~10% of the original input-token cost.

Usage:
    system_blocks = build_system_blocks([SYSTEM_PROMPT, STRATEGY_CATALOG])
    user_content = build_user_content(
        static_blocks=[FORMAT_INSTRUCTIONS, history_text],
        fresh_blocks=[current_snapshot_text],
    )
    response = client.messages.create(
        model=...,
        system=system_blocks,
        messages=[{"role": "user", "content": user_content}],
    )

The static parts get cached after the first call; only `current_snapshot_text`
is freshly billed on each subsequent call within the cache window.
"""
from __future__ import annotations


def text_block(text: str) -> dict:
    """A plain (uncached) text content block."""
    return {"type": "text", "text": text}


def cached_text_block(text: str) -> dict:
    """A text content block marked for ephemeral caching."""
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }


def build_system_blocks(static_parts: list[str]) -> list[dict]:
    """Build a system content-block list. The LAST block is cache-marked
    (so everything in `static_parts` is cached cumulatively).

    If `config.PROPOSER_CACHE_ENABLED` is False, returns plain text blocks
    without cache_control. If `static_parts` is empty, returns an empty list.
    """
    from bullbot import config

    if not static_parts:
        return []

    if not config.PROPOSER_CACHE_ENABLED:
        return [text_block(p) for p in static_parts]

    # Mark only the last block; cache covers everything up to and including it.
    blocks = [text_block(p) for p in static_parts[:-1]]
    blocks.append(cached_text_block(static_parts[-1]))
    return blocks


def build_user_content(
    static_blocks: list[str],
    fresh_blocks: list[str],
) -> list[dict]:
    """Build a user-message content list with a cached static prefix and an
    uncached fresh suffix.

    The last block in `static_blocks` is cache-marked; `fresh_blocks` are
    always plain. If caching is disabled, all blocks are plain.
    """
    from bullbot import config

    static_part = build_system_blocks(static_blocks) if config.PROPOSER_CACHE_ENABLED \
        else [text_block(s) for s in static_blocks]
    fresh_part = [text_block(f) for f in fresh_blocks]
    return static_part + fresh_part
