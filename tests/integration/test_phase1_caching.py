"""Integration test: verify Phase 1 wiring — proposer system arg is structured,
retired ticker briefs are skipped — within a full scheduler.tick().
"""
from __future__ import annotations

import sqlite3

import pytest

from bullbot import config


def test_full_tick_uses_cached_system_blocks_for_proposer(
    db_conn, fake_anthropic, fake_uw, monkeypatch, tmp_path
):
    """Full tick() under PROPOSER_CACHE_ENABLED=True should pass cache-marked
    blocks to every proposer call."""
    monkeypatch.setattr(config, "PROPOSER_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "SKIP_BRIEFS_FOR_RETIRED", True)
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(config, "UNIVERSE", ["SPY"])

    db_conn.execute(
        "INSERT INTO ticker_state (ticker, phase, iteration_count, updated_at) "
        "VALUES ('SPY', 'discovering', 0, 0)"
    )

    # Seed bars for both SPY and the full regime-market ticker set so
    # _refresh_regime can compute market signals
    base_ts = 1_700_000_000
    market_tickers = ["SPY"] + list(config.REGIME_DATA_TICKERS)
    for ticker in set(market_tickers):
        for i in range(60):
            db_conn.execute(
                "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
                "VALUES (?, '1d', ?, 500, 502, 498, 500, 1000000)",
                (ticker, base_ts + i * 86400),
            )

    # Queue brief + proposer responses; tick() may invoke many briefs and one
    # proposer call. Pad generously.
    for _ in range(30):
        fake_anthropic.queue_response("brief text")
    fake_anthropic.queue_response(
        '{"class_name": "PutCreditSpread", '
        '"params": {"dte": 21, "short_delta": 0.30, "width": 5, '
        '"profit_target_pct": 0.5, "stop_loss_mult": 2.0, "min_dte_close": 7}, '
        '"rationale": "test"}'
    )

    from bullbot import scheduler
    scheduler.tick(db_conn, fake_anthropic, fake_uw)

    # Find the proposer call. It's the call whose `system` is a list of blocks
    # containing the strategy catalog text (phrase "class_name").
    proposer_calls = [
        c for c in fake_anthropic.call_log
        if isinstance(c.get("system"), list)
        and any("class_name" in str(b.get("text", "")) for b in c["system"])
    ]
    assert len(proposer_calls) >= 1, (
        f"proposer was not invoked; call_log={fake_anthropic.call_log}"
    )

    # The proposer call's system must be a list of blocks with cache_control on
    # the last entry.
    call = proposer_calls[0]
    system = call["system"]
    assert isinstance(system, list)
    assert system[-1].get("cache_control") == {"type": "ephemeral"}
