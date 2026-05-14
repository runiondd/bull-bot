"""Tests for scripts/run_continuous.py — the hourly daemon.

These tests intentionally do NOT exercise the real scheduler.tick (that
belongs in scheduler integration tests). They verify only the daemon
shell: one round writes a heartbeat, and the restart back-off kicks in
after 3 crashes within an hour.
"""

from __future__ import annotations

import pytest


def test_daemon_runs_one_round_and_writes_heartbeat(tmp_path, monkeypatch):
    """run_one_round writes an ISO-8601 UTC timestamp to the heartbeat path."""
    monkeypatch.setattr("bullbot.clock.is_market_open_now", lambda: True)
    monkeypatch.setattr("bullbot.scheduler.tick", lambda *a, **kw: None)
    from scripts.run_continuous import run_one_round

    run_one_round(heartbeat_path=tmp_path / "hb.txt")

    assert (tmp_path / "hb.txt").exists()
    ts = (tmp_path / "hb.txt").read_text()
    assert ts.startswith("2026-")


def test_three_crashes_within_an_hour_exits_loop(tmp_path, monkeypatch):
    """run_loop bails out with non-zero after 3 crashes in <1h.

    The back-off is what protects the host from a tight crash-restart
    spin. We force scheduler.tick to raise, set sleep_seconds=0 so the
    loop iterates immediately, and confirm run_loop returns a non-zero
    exit code without looping forever.
    """
    monkeypatch.setattr("bullbot.clock.is_market_open_now", lambda: True)

    def boom(*a, **kw):
        raise RuntimeError("synthetic crash")

    monkeypatch.setattr("bullbot.scheduler.tick", boom)

    # The real run_loop builds an Anthropic client; bypass that by
    # injecting pre-built (None) clients.
    from scripts.run_continuous import run_loop

    rc = run_loop(
        heartbeat_path=tmp_path / "hb.txt",
        sleep_seconds=0,
        conn=object(),  # placeholder; scheduler.tick is mocked so it's unused
        anthropic_client=None,
        data_client=None,
    )

    assert rc != 0
