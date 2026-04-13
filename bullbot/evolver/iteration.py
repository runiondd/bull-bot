"""
Evolver iteration — the core algorithm loop for one ticker.

Each call to ``run()`` executes a single iteration of the evolver:
propose a strategy variant, backtest it, classify the result, and
update the ticker's lifecycle state.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from bullbot.backtest import walkforward
from bullbot.engine import step as engine_step
from bullbot.evolver import plateau, proposer
from bullbot.risk import cost_ledger
from bullbot.strategies import registry
from bullbot import config

log = logging.getLogger("bullbot.evolver.iteration")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_or_create_ticker_state(conn: sqlite3.Connection, ticker: str) -> dict[str, Any]:
    """Return the ticker_state row as a dict, creating it if absent."""
    row = conn.execute(
        "SELECT * FROM ticker_state WHERE ticker=?", (ticker,)
    ).fetchone()
    if row is not None:
        return dict(row)

    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES (?, 'discovering', ?)",
        (ticker, int(time.time())),
    )
    row = conn.execute(
        "SELECT * FROM ticker_state WHERE ticker=?", (ticker,)
    ).fetchone()
    return dict(row)


def _load_history(conn: sqlite3.Connection, ticker: str) -> list[dict]:
    """Load past evolver proposals joined with strategy info."""
    rows = conn.execute(
        "SELECT ep.*, s.class_name, s.params "
        "FROM evolver_proposals ep "
        "JOIN strategies s ON ep.strategy_id = s.id "
        "WHERE ep.ticker=? ORDER BY ep.iteration",
        (ticker,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_cursor(conn: sqlite3.Connection, ticker: str) -> int:
    """Return the timestamp of the most recent daily bar, or now."""
    row = conn.execute(
        "SELECT MAX(ts) FROM bars WHERE ticker=? AND timeframe='1d'", (ticker,)
    ).fetchone()
    if row and row[0]:
        return row[0]
    return int(time.time())


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(
    conn: sqlite3.Connection,
    anthropic_client: Any,
    data_client: Any,
    ticker: str,
) -> None:
    """Execute one evolver iteration for *ticker*.

    Steps:
    1. Load / create ticker_state
    2. Load proposal history
    3. Build market snapshot
    4. Call proposer for a new strategy variant
    5. Log cost
    6. Dedup check
    7. If duplicate: record proposal, bump counter, return
    8. If new: insert strategy, backtest, classify, write results
    """
    # 1. ticker state
    state = _load_or_create_ticker_state(conn, ticker)
    iteration_num = state["iteration_count"] + 1
    category = config.TICKER_CATEGORY.get(ticker, "income")

    # 2. history
    history = _load_history(conn, ticker)

    # 3. snapshot
    cursor = _get_cursor(conn, ticker)
    snapshot = engine_step._build_snapshot(conn, ticker, cursor)
    if snapshot is None:
        log.warning("Not enough bar data for %s at cursor=%d; skipping iteration", ticker, cursor)
        return

    # 4. propose
    proposal = proposer.propose(
        client=anthropic_client,
        snapshot=snapshot,
        history=history,
        best_strategy_id=state.get("best_strategy_id"),
        category=category,
    )

    # 5. cost
    now_ts = int(time.time())
    cost_ledger.append(
        conn,
        ts=now_ts,
        category="llm",
        ticker=ticker,
        amount_usd=proposal.llm_cost_usd,
        details={
            "model": "proposer",
            "input_tokens": proposal.input_tokens,
            "output_tokens": proposal.output_tokens,
        },
    )

    # 6. dedup
    p_hash = registry.params_hash(proposal.params)
    cls = registry.get_class(proposal.class_name)
    class_version = cls.CLASS_VERSION

    existing_strategy = conn.execute(
        "SELECT id FROM strategies WHERE class_name=? AND class_version=? AND params_hash=?",
        (proposal.class_name, class_version, p_hash),
    ).fetchone()

    if existing_strategy is not None:
        # 7. duplicate — record proposal with previous metrics, bump counter
        strategy_id = existing_strategy["id"]
        # Grab metrics from the earlier proposal for this strategy if available
        prev = conn.execute(
            "SELECT pf_is, pf_oos, sharpe_is, max_dd_pct, trade_count, regime_breakdown, passed_gate "
            "FROM evolver_proposals WHERE strategy_id=? ORDER BY iteration DESC LIMIT 1",
            (strategy_id,),
        ).fetchone()

        conn.execute(
            "INSERT INTO evolver_proposals "
            "(ticker, iteration, strategy_id, rationale, llm_cost_usd, "
            " pf_is, pf_oos, sharpe_is, max_dd_pct, trade_count, regime_breakdown, passed_gate, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticker, iteration_num, strategy_id, proposal.rationale,
                proposal.llm_cost_usd,
                prev["pf_is"] if prev else None,
                prev["pf_oos"] if prev else None,
                prev["sharpe_is"] if prev else None,
                prev["max_dd_pct"] if prev else None,
                prev["trade_count"] if prev else None,
                prev["regime_breakdown"] if prev else None,
                prev["passed_gate"] if prev else 0,
                now_ts,
            ),
        )
        conn.execute(
            "UPDATE ticker_state SET iteration_count=?, cumulative_llm_usd=cumulative_llm_usd+?, updated_at=? "
            "WHERE ticker=?",
            (iteration_num, proposal.llm_cost_usd, now_ts, ticker),
        )
        log.info("Iteration %d for %s: duplicate strategy_id=%d, skipping backtest", iteration_num, ticker, strategy_id)
        return

    # 8. new strategy — insert, materialize, backtest, classify
    canonical_params = registry.canonicalize_params(proposal.params)
    cur = conn.execute(
        "INSERT INTO strategies (class_name, class_version, params, params_hash, parent_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (proposal.class_name, class_version, canonical_params, p_hash, state.get("best_strategy_id"), now_ts),
    )
    strategy_id = cur.lastrowid

    strategy_obj = registry.materialize(proposal.class_name, proposal.params)

    metrics = walkforward.run_walkforward(
        conn=conn,
        strategy=strategy_obj,
        strategy_id=strategy_id,
        ticker=ticker,
    )

    # 9. classify
    class _State:
        def __init__(self, s):
            self.iteration_count = s["iteration_count"]
            self.plateau_counter = s["plateau_counter"]
            self.best_pf_oos = s["best_pf_oos"] or 0.0

    result = plateau.classify(_State(state), metrics, category=category)

    passed_gate = 1 if result.verdict == "edge_found" else 0

    # 10. write proposal + update state
    conn.execute(
        "INSERT INTO evolver_proposals "
        "(ticker, iteration, strategy_id, rationale, llm_cost_usd, "
        " pf_is, pf_oos, sharpe_is, max_dd_pct, trade_count, regime_breakdown, passed_gate, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticker, iteration_num, strategy_id, proposal.rationale,
            proposal.llm_cost_usd,
            metrics.pf_is, metrics.pf_oos, metrics.sharpe_is,
            metrics.max_dd_pct, metrics.trade_count,
            json.dumps(metrics.regime_breakdown),
            passed_gate, now_ts,
        ),
    )

    update_fields: dict[str, Any] = {
        "iteration_count": iteration_num,
        "plateau_counter": result.new_plateau_counter,
        "best_pf_oos": result.new_best_pf_oos,
        "cumulative_llm_usd": state["cumulative_llm_usd"] + proposal.llm_cost_usd,
        "updated_at": now_ts,
    }

    if result.improved or state.get("best_strategy_id") is None:
        update_fields["best_strategy_id"] = strategy_id
        update_fields["best_pf_is"] = metrics.pf_is

    if result.verdict == "edge_found":
        update_fields["phase"] = "paper_trial"
        update_fields["best_strategy_id"] = strategy_id
        update_fields["best_pf_is"] = metrics.pf_is
    elif result.verdict == "no_edge":
        update_fields["phase"] = "no_edge"
        update_fields["verdict_at"] = now_ts

    set_clause = ", ".join(f"{k}=?" for k in update_fields)
    values = list(update_fields.values()) + [ticker]
    conn.execute(
        f"UPDATE ticker_state SET {set_clause} WHERE ticker=?",
        values,
    )

    log.info(
        "Iteration %d for %s: verdict=%s pf_oos=%.3f strategy_id=%d",
        iteration_num, ticker, result.verdict, metrics.pf_oos, strategy_id,
    )
