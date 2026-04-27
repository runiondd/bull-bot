"""Tests for bullbot.llm.cache — content-block builders with cache_control."""
from __future__ import annotations

from bullbot.llm import cache


def test_cached_text_block_basic():
    block = cache.cached_text_block("hello world")
    assert block == {
        "type": "text",
        "text": "hello world",
        "cache_control": {"type": "ephemeral"},
    }


def test_text_block_uncached():
    block = cache.text_block("hello world")
    assert block == {"type": "text", "text": "hello world"}
    assert "cache_control" not in block


def test_build_system_blocks_marks_last_block_cached(monkeypatch):
    """When caching is enabled, the LAST block in the system list gets cache_control.
    Anthropic's cache markers are *cumulative* — marking the final block caches
    everything before it as well."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_CACHE_ENABLED", True)
    blocks = cache.build_system_blocks(["base instructions", "strategy catalog"])
    assert len(blocks) == 2
    # Final block has cache_control; earlier blocks do not
    assert "cache_control" not in blocks[0]
    assert blocks[-1].get("cache_control") == {"type": "ephemeral"}


def test_build_system_blocks_no_caching_when_disabled(monkeypatch):
    """When caching is disabled, no block gets cache_control."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_CACHE_ENABLED", False)
    blocks = cache.build_system_blocks(["base instructions", "strategy catalog"])
    for b in blocks:
        assert "cache_control" not in b


def test_build_system_blocks_empty():
    """Empty input → empty list, not an error."""
    blocks = cache.build_system_blocks([])
    assert blocks == []


def test_build_user_content_marks_last_static_block(monkeypatch):
    """User content has a static prefix and a fresh suffix; mark the last static block cached."""
    from bullbot import config
    monkeypatch.setattr(config, "PROPOSER_CACHE_ENABLED", True)
    blocks = cache.build_user_content(
        static_blocks=["instructions", "history"],
        fresh_blocks=["current snapshot"],
    )
    assert len(blocks) == 3
    # Last static block (index 1) gets cache_control; fresh block does not
    assert "cache_control" not in blocks[0]
    assert blocks[1].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in blocks[2]
