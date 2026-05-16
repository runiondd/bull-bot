"""v2 paper-trade dispatcher — decides what to do with a DirectionalSignal.

Pure decision logic plus persistence calls. Rules:
- confidence < CONFIDENCE_THRESHOLD       → skip (no action)
- direction in {chop, no_edge}           → close any open position
- direction == bullish:
    * no position                         → open long
    * existing long                       → hold
    * existing short                      → close short + open long (flip)
- direction == bearish: mirror of bullish

Sizing: floor(budget_usd / spot). If < 1 share, skip with budget reason.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from bullbot.v2 import trades
from bullbot.v2.signals import DirectionalSignal

CONFIDENCE_THRESHOLD = 0.50
STOP_LOSS_PCT = 0.10  # close any open position whose loss exceeds 10% of entry

ACTION_KINDS = (
    "opened",
    "held",
    "flipped",
    "closed_to_flat",
    "stopped_out",
    "skipped_low_confidence",
    "skipped_budget",
    "skipped_no_action",
)


@dataclass(frozen=True)
class TraderAction:
    kind: str
    ticker: str
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise ValueError(f"unknown action kind: {self.kind}")


def _signal_to_position_direction(direction: str) -> str | None:
    if direction == "bullish":
        return "long"
    if direction == "bearish":
        return "short"
    return None


def dispatch(
    conn: sqlite3.Connection,
    *,
    signal: DirectionalSignal,
    signal_id: int | None,
    spot: float,
    budget_usd: float,
    now_ts: int,
) -> TraderAction:
    """Decide and execute one paper-trade action for a single ticker."""
    ticker = signal.ticker
    open_pos = trades.open_position_for(conn, ticker)

    # 0. Stop-loss takes precedence over any signal-based action. If the
    # open position's loss exceeds STOP_LOSS_PCT of entry, close immediately
    # and do not re-enter this tick — wait for the next signal cycle.
    if open_pos is not None and spot > 0:
        if open_pos.direction == "long":
            loss_pct = (spot - open_pos.entry_price) / open_pos.entry_price
        else:  # short
            loss_pct = (open_pos.entry_price - spot) / open_pos.entry_price
        if loss_pct <= -STOP_LOSS_PCT:
            trades.close_trade(
                conn, trade_id=open_pos.id, exit_price=spot, exit_ts=now_ts,
                exit_reason="stop_loss",
            )
            return TraderAction(
                kind="stopped_out", ticker=ticker,
                reason=f"loss {loss_pct:.1%} <= -{STOP_LOSS_PCT:.0%}",
            )

    # 1. Close-to-flat path: chop / no_edge always closes any open position.
    if signal.direction in ("chop", "no_edge"):
        if open_pos is not None:
            trades.close_trade(
                conn, trade_id=open_pos.id, exit_price=spot, exit_ts=now_ts,
                exit_reason=f"signal_{signal.direction}",
            )
            return TraderAction(kind="closed_to_flat", ticker=ticker)
        return TraderAction(kind="skipped_no_action", ticker=ticker, reason=signal.direction)

    # 2. Low-confidence guard: do not change state (don't close existing position
    # just because confidence dropped slightly — only chop/no_edge triggers exit).
    if signal.confidence < CONFIDENCE_THRESHOLD:
        return TraderAction(
            kind="skipped_low_confidence",
            ticker=ticker,
            reason=f"confidence {signal.confidence:.2f} < {CONFIDENCE_THRESHOLD}",
        )

    desired = _signal_to_position_direction(signal.direction)
    assert desired is not None  # mypy / readability — already filtered chop/no_edge above

    # 3. Hold if already in the right direction.
    if open_pos is not None and open_pos.direction == desired:
        return TraderAction(kind="held", ticker=ticker)

    # 4. Flip if in opposite direction.
    if open_pos is not None and open_pos.direction != desired:
        trades.close_trade(
            conn, trade_id=open_pos.id, exit_price=spot, exit_ts=now_ts,
            exit_reason="signal_flip",
        )
        # fall through to open the new direction

    # 5. Open fresh position.
    shares = math.floor(budget_usd / spot) if spot > 0 else 0
    if shares < 1:
        return TraderAction(
            kind="skipped_budget", ticker=ticker,
            reason=f"budget ${budget_usd:.0f} too small for 1 share at ${spot:.2f}",
        )
    trades.open_trade(
        conn, ticker=ticker, direction=desired, shares=float(shares),
        entry_price=spot, entry_ts=now_ts, signal_id=signal_id,
    )
    return TraderAction(
        kind="flipped" if open_pos is not None else "opened",
        ticker=ticker,
    )
