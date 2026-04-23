"""Scheduler integration: confirm a research_health_*.md file is produced."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bullbot import config


def _seed_minimal_db(conn: sqlite3.Connection) -> None:
    # Just enough for tick() + health checks to not crash; no LLM calls.
    from bullbot.db import migrations
    migrations.apply_schema(conn)


def test_scheduler_tick_writes_health_brief(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(config, "UNIVERSE", [])  # skip ticker dispatch entirely

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed_minimal_db(conn)

    # Provide minimal fake clients — tick() tolerates them when UNIVERSE is empty
    class _Nop:
        pass

    from bullbot import scheduler
    scheduler.tick(conn=conn, anthropic_client=_Nop(), data_client=_Nop())

    briefs = list(tmp_path.glob("research_health_*.md"))
    assert len(briefs) == 1
    assert briefs[0].read_text().startswith("# Research Health")
