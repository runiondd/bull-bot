"""
End-to-end smoke test for Bull-Bot v3.

Runs three real evolver iterations on SPY against a sandbox SQLite file
(NOT the production one). Uses real Anthropic + real UW API calls.

Cost: ~$0.15/run (3 Opus calls). Intended to run before merging any branch
that touches data/, evolver/, engine/, or risk/.

Usage:
    python scripts/smoke_test.py

Exits 0 on success, 1 on any exception or failed assertion.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import anthropic

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bullbot import config
from bullbot.data import fetchers, options_backfill
from bullbot.db import connection as db_connection
from bullbot.evolver import iteration
from bullbot.risk import cost_ledger


SANDBOX_DB = PROJECT_ROOT / "cache" / "smoke_test.db"


def main() -> int:
    # Fresh sandbox DB
    if SANDBOX_DB.exists():
        SANDBOX_DB.unlink()

    print(f"Opening sandbox DB at {SANDBOX_DB}")
    conn = db_connection.open_persistent_connection(SANDBOX_DB)

    print("Creating Anthropic client...")
    anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    print("Creating UW client...")
    uw_client = fetchers.UWHttpClient(api_key=config.UW_API_KEY)

    print("Fetching SPY daily bars (first fetch, cold cache)...")
    bars = fetchers.fetch_daily_ohlc(uw_client, "SPY", limit=500)
    print(f"  → got {len(bars)} bars")
    for b in bars:
        conn.execute(
            "INSERT OR REPLACE INTO bars (ticker, timeframe, ts, open, high, low, close, volume, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (b.ticker, b.timeframe, b.ts, b.open, b.high, b.low, b.close, b.volume, b.source),
        )

    print("Backfilling SPY option contracts (small window, 60 days)...")
    from datetime import date, timedelta
    spot = bars[-1].close
    summary = options_backfill.run(
        conn=conn, client=uw_client, ticker="SPY", spot=spot,
        start=date.today() - timedelta(days=60),
        end=date.today() + timedelta(days=45),
        rate_limit_sleep=0.05,
    )
    print(f"  → {summary}")
    assert summary["rows_written"] > 0, "backfill produced no rows"

    print("Initializing ticker_state for SPY...")
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'discovering', ?)",
        (int(time.time()),),
    )

    print("Running 3 evolver iterations against real Opus...")
    for i in range(3):
        print(f"  iteration {i + 1}...")
        iteration.run(
            conn=conn,
            anthropic_client=anthropic_client,
            data_client=uw_client,
            ticker="SPY",
        )
        row = conn.execute(
            "SELECT iteration_count, cumulative_llm_usd, phase FROM ticker_state WHERE ticker='SPY'"
        ).fetchone()
        print(
            f"    iter_count={row['iteration_count']} "
            f"llm_usd=${row['cumulative_llm_usd']:.4f} phase={row['phase']}"
        )

    state = conn.execute("SELECT * FROM ticker_state WHERE ticker='SPY'").fetchone()
    print()
    print("Smoke test summary:")
    print(f"  Final phase: {state['phase']}")
    print(f"  Iterations completed: {state['iteration_count']}")
    print(f"  Total LLM spend: ${state['cumulative_llm_usd']:.4f}")
    print(f"  Global cost ledger: ${cost_ledger.cumulative_llm_usd(conn):.4f}")

    assert state["iteration_count"] >= 3, f"expected ≥3 iterations, got {state['iteration_count']}"
    assert state["cumulative_llm_usd"] <= 1.0, (
        f"cost ceiling exceeded: ${state['cumulative_llm_usd']:.4f}"
    )
    assert state["phase"] in ("discovering", "paper_trial", "no_edge"), (
        f"unexpected phase: {state['phase']}"
    )

    print()
    print("Smoke test PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nSmoke test FAIL: {e}", file=sys.stderr)
        raise SystemExit(1)
