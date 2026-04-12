# Engine-Level Exit Manager

**Date:** 2026-04-12
**Goal:** Add position exit logic so the walk-forward backtest produces closed trades with PnL, enabling the evolver to evaluate strategies.

## Problem

All 6 strategy implementations only generate `intent="open"` signals. Once a position opens, strategies block forever (`if any(open_positions): return None`). Positions never close, so walk-forward reports zero trades and PF=0.0 for every strategy.

The close infrastructure exists in the engine (`step.py` handles `intent='close'`, fill model prices closes, `position_id_to_close` exists on Signal) but nothing triggers it.

## Approach: Engine-Level Exit Manager

Exit rules are defined at entry time per position and checked by the engine on every bar. Strategies only handle entry logic.

## Data Model

### Signal changes (`bullbot/data/schemas.py`)

Three new optional fields on Signal:

```python
profit_target_pct: float | None = None   # 0.50 = close at 50% of max profit
stop_loss_mult: float | None = None      # 2.0 = close when loss hits 2x credit received
min_dte_close: int | None = None         # 7 = close at 7 DTE regardless of P&L
```

`None` disables that exit condition for the position.

### Positions table (`bullbot/db/schema.sql`)

New column:

```sql
exit_rules TEXT  -- JSON: {"profit_target_pct": 0.5, "stop_loss_mult": 2.0, "min_dte_close": 7}
```

Populated at position open time from the Signal's exit fields.

### Config defaults (`bullbot/config.py`)

```python
DEFAULT_PROFIT_TARGET_PCT = 0.50
DEFAULT_STOP_LOSS_MULT = 2.0
DEFAULT_MIN_DTE_CLOSE = 7
```

Used when the proposer omits exit params. Strategies pass these through from their `params` dict, falling back to config defaults.

## Engine Exit Manager

New function `check_exits()` in `bullbot/engine/step.py`. Called on every bar before `strategy.evaluate()`.

For each open position in the current run:

1. **Load exit rules** from position's `exit_rules` JSON column.
2. **Compute unrealized P&L** by pricing each leg against current chain (snapshot) via fill model's `simulate_close_multi_leg()`.
3. **Check conditions in priority order:**
   - **DTE close:** nearest leg expiry minus current bar date <= `min_dte_close` -> close.
   - **Stop loss:** unrealized loss >= `stop_loss_mult` * |credit received| -> close. For credit strategies, credit received = abs(open_price). For debit strategies, max loss = open_price.
   - **Take profit:** unrealized profit >= `profit_target_pct` * max_profit -> close. For credit strategies, max profit = |credit received|. For debit strategies, profit = current value - open_price.
4. **If triggered:** execute close through existing engine close flow (fill model, update position, record order with `intent='close'`).

If a position closes, it's removed from `open_positions` before `evaluate()` runs, so the strategy is free to open a new position on the same bar.

If the chain doesn't have current prices for a position's legs (fill rejected), skip the exit check for that bar.

### P&L semantics by strategy type

**Credit strategies** (PutCreditSpread, CallCreditSpread, IronCondor, CashSecuredPut):
- `open_price` is negative (credit received).
- Max profit = abs(open_price).
- Unrealized profit = abs(open_price) - close_cost. Take profit fires when this >= `profit_target_pct` * max_profit.
- Unrealized loss = close_cost - abs(open_price). Stop loss fires when this >= `stop_loss_mult` * abs(open_price).

**Debit strategies** (LongCall, LongPut):
- `open_price` is positive (debit paid).
- Unrealized profit = current_value - open_price. Take profit fires when this >= `profit_target_pct` * open_price.
- Unrealized loss = open_price - current_value. Stop loss fires when this >= `stop_loss_mult` * open_price.

## Strategy Changes

Each strategy passes exit params from its `params` dict onto the Signal. Minimal change: read `profit_target_pct`, `stop_loss_mult`, `min_dte_close` from `self.params` with config defaults as fallback. No exit logic in strategies.

## Proposer Prompt Update

Add exit params to the expected JSON response format so the LLM can propose them:

```json
{
  "class_name": "PutCreditSpread",
  "params": {
    "dte": 30, "short_delta": 0.25, "width": 5,
    "profit_target_pct": 0.50, "stop_loss_mult": 2.0, "min_dte_close": 7
  }
}
```

The proposer can experiment with aggressive exits (profit_target_pct=0.30) vs. patient exits (profit_target_pct=0.75), tight stops (stop_loss_mult=1.0) vs. loose stops (stop_loss_mult=3.0), etc.

## Testing

### Unit tests for `check_exits()`
- Position with profit target 50%, chain shows spread worth 40% of credit -> no exit.
- Same position, chain shows spread worth 55% of credit -> exit fires.
- Position with stop loss 2x, chain shows loss at 1.5x -> no exit.
- Same position, chain shows loss at 2.5x -> exit fires.
- Position with min_dte_close=7, cursor at 10 DTE -> no exit.
- Same position, cursor at 5 DTE -> exit fires.
- Position with `None` for all exit rules -> never exits (matches current behavior).
- Fill rejected (no chain data) -> skip gracefully, no exit.

### Integration test
- Open a PutCreditSpread via `step()`, advance cursor to where spread has decayed, verify close order created with correct PnL.

### Walk-forward smoke test
- Run mini walk-forward, verify `trade_count > 0` and `profit_factor > 0`.

## Files Changed

- `bullbot/data/schemas.py` — add 3 fields to Signal
- `bullbot/db/schema.sql` — add `exit_rules` column to positions
- `bullbot/config.py` — add 3 default constants
- `bullbot/engine/step.py` — add `check_exits()`, call it before evaluate, store exit rules on open
- `bullbot/strategies/base.py` — no change (evaluate signature unchanged)
- `bullbot/strategies/put_credit_spread.py` — pass exit params onto Signal
- `bullbot/strategies/call_credit_spread.py` — same
- `bullbot/strategies/iron_condor.py` — same
- `bullbot/strategies/cash_secured_put.py` — same
- `bullbot/strategies/long_call.py` — same
- `bullbot/strategies/long_put.py` — same
- `bullbot/evolver/proposer.py` — add exit params to prompt
- `tests/` — new test file for exit manager + update existing tests
