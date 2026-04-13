# Synthetic Options Chain Generator Design Spec

**Date:** 2026-04-13
**Status:** Draft

## Goal

Generate synthetic option chains from historical bar data and realized volatility so the evolver can discover growth strategies on tickers without real options data backfilled. The output matches the `OptionContract` schema exactly, so no downstream code changes are needed beyond the integration point.

## Module

`bullbot/data/synthetic_chain.py`

### Public Function

```python
def generate_synthetic_chain(
    ticker: str,
    spot: float,
    cursor: int,
    bars: list[Bar],
    risk_free_rate: float = config.RISK_FREE_RATE,
) -> list[OptionContract]:
```

### Realized Volatility

Annualized standard deviation of daily log returns from the last 30 bars:

```
returns = [ln(close[i] / close[i-1]) for i in range(1, 30)]
realized_vol = std(returns) * sqrt(252)
```

Falls back to 0.30 (30% vol) if fewer than 30 bars are available.

### Strike Generation

ATM ± 20% of spot price, in increments based on price level:
- Spot < $50: $2.50 increments
- Spot $50-$200: $5 increments
- Spot > $200: $10 increments

### Expiry Generation

Fixed set of DTEs from cursor: 30, 60, 90, 180, 270, 365 days. Converted to ISO date strings (`YYYY-MM-DD`).

### Black-Scholes Pricing

Standard European Black-Scholes formula:

```
d1 = (ln(S/K) + (r + σ²/2) * T) / (σ * sqrt(T))
d2 = d1 - σ * sqrt(T)

Call = S * N(d1) - K * e^(-rT) * N(d2)
Put  = K * e^(-rT) * N(-d2) - S * N(-d1)
```

Where:
- S = spot price
- K = strike price
- r = risk-free rate (config.RISK_FREE_RATE = 0.045)
- σ = realized volatility from bars
- T = time to expiry in years (DTE / 365)
- N() = standard normal CDF

### Bid/Ask Spread

- `nbbo_bid = max(0.01, theoretical_price * 0.95)`
- `nbbo_ask = theoretical_price * 1.05`

5% spread simulates realistic market conditions. Minimum bid of $0.01 prevents zero-bid contracts.

### IV Field

Each synthetic `OptionContract` gets `iv = realized_vol` (the same vol used for pricing). This is self-consistent — if you priced with that vol, the implied vol is that vol.

### Output

For each (strike, expiry) combination, generates both a call and a put `OptionContract` with:
- `ticker`, `expiry`, `strike`, `kind` ("C" or "P")
- `ts = cursor`
- `nbbo_bid`, `nbbo_ask` from Black-Scholes + spread
- `volume = 100`, `open_interest = 1000` (nominal values)
- `iv = realized_vol`

## Integration Point

**File:** `bullbot/engine/step.py`, function `_load_chain_at_cursor()`

After the existing DB query, if the result is empty, generate a synthetic chain:

```python
def _load_chain_at_cursor(conn, ticker, cursor):
    rows = conn.execute(...)  # existing query
    if rows:
        return [OptionContract(...) for r in rows]  # existing logic
    
    # Fallback: synthetic chain
    bars = _load_bars_at_cursor(conn, ticker, cursor, limit=60)
    if len(bars) < 30:
        return []
    from bullbot.data.synthetic_chain import generate_synthetic_chain
    return generate_synthetic_chain(
        ticker=ticker, spot=bars[-1].close, cursor=cursor, bars=bars,
    )
```

This is the only change to existing code. Everything downstream (strategies, fill model, exit manager) works unchanged because synthetic contracts are valid `OptionContract` objects.

## Limitations

- Realized vol is not implied vol. Synthetic prices will be systematically different from market prices, especially during vol events. This is acceptable for initial strategy discovery.
- No skew modeling. All strikes at the same expiry use the same vol. Real markets have a volatility smile. This means OTM options will be underpriced relative to reality.
- European pricing applied to American-style options (all equity options are American). Black-Scholes underprices American options slightly due to early exercise premium. Acceptable for discovery.
- No dividends modeled. TSLA doesn't currently pay dividends, so this is fine for the initial use case.

## Testing

- Unit test Black-Scholes pricing against known values (e.g., ATM call with 30% vol, 1 year, $100 stock ≈ $13.28)
- Unit test realized vol computation
- Unit test strike/expiry generation produces expected ranges
- Integration test: synthetic chain produces valid OptionContract objects that strategies can evaluate
