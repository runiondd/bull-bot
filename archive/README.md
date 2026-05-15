# archive/

Code retired but kept for traceability. **Do not import from `archive/`**; modules here are no longer maintained.

## Contents

### `clients/uw_client.py`
Unusual Whales REST client. Retired 2026-05-14. Subscription canceled 2026-05-11; project moved to Option A (Polygon-only) per `.mentor/proposals/2026-05-11-uw-replacement-strategy.md` (accepted). Had zero live importers in `bullbot/` at retirement; its only consumer was `scripts/validate_uw.py` (also archived).

### `scripts/validate_uw.py`
Phase-0 Unusual Whales validator. Retired 2026-05-14 alongside the client. Used by `validate_uw_historical_options.py` only (also archived).

### `scripts/validate_uw_historical_options.py`
Phase-0b UW historical-options-data validator. Retired 2026-05-14. Imported from `scripts.validate_uw`, so moved together.

### `scripts/validate_uw_historical_options_expired.py`
Phase-0b extension probing `/historic` on already-expired SPY contracts. Retired 2026-05-15 — missed by stage 1 because it was a standalone script with no Python-level importers of the retired `clients/uw_client.py` (it builds its own `urllib3` session from `config.UW_API_KEY`). Captured here for completeness; reference its proof that Polygon expired-options access works as the de-facto replacement.

If UW is ever re-evaluated, restore from `archive/` rather than rewriting from scratch.

## Known working-tree orphans (FUSE-rename workaround)

During stage 1 retirement on 2026-05-14, the Cowork sandbox's FUSE mount blocks `unlink(2)` on existing files, so `git mv` was implemented as a copy-then-rename rather than a true delete of the working-tree source. The duplicates that remain on disk but are **not tracked by git**:

- `clients/uw_client.py` — byte-identical to `archive/clients/uw_client.py`.
- `scripts/validate_uw.py` — byte-identical to `archive/scripts/validate_uw.py`.
- `scripts/validate_uw_historical_options.py` — byte-identical to `archive/scripts/validate_uw_historical_options.py`.

These are clutter, not a code-correctness risk (zero importers; verified). To remove them, from a non-FUSE terminal:

```bash
rm clients/uw_client.py scripts/validate_uw.py scripts/validate_uw_historical_options.py
```
