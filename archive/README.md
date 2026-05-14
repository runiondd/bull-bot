# archive/

Code retired but kept for traceability. **Do not import from `archive/`**; modules here are no longer maintained.

## Contents

### `clients/uw_client.py`
Unusual Whales REST client. Retired 2026-05-14. Subscription canceled 2026-05-11; project moved to Option A (Polygon-only) per `.mentor/proposals/2026-05-11-uw-replacement-strategy.md` (accepted). Had zero live importers in `bullbot/` at retirement; its only consumer was `scripts/validate_uw.py` (also archived).

### `scripts/validate_uw.py`
Phase-0 Unusual Whales validator. Retired 2026-05-14 alongside the client. Used by `validate_uw_historical_options.py` only (also archived).

### `scripts/validate_uw_historical_options.py`
Phase-0b UW historical-options-data validator. Retired 2026-05-14. Imported from `scripts.validate_uw`, so moved together.

If UW is ever re-evaluated, restore from `archive/` rather than rewriting from scratch.
