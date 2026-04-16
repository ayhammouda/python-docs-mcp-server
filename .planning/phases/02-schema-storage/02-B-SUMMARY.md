# Plan 02-B Summary: Refactor bootstrap_schema() to load schema.sql

**Status:** Complete
**Started:** 2026-04-16
**Completed:** 2026-04-16

## What Was Built

Refactored `bootstrap_schema()` in `storage/db.py` to:
1. Load schema DDL from `schema.sql` via `importlib.resources` (no inline SQL)
2. Drop FTS5 virtual tables before recreation to handle tokenizer migration
3. Maintain idempotent execution (STOR-09)

The function signature is unchanged -- `ingest_inventory()` and all other callers continue to work without modification.

## Self-Check: PASSED

- [x] `import importlib.resources` added
- [x] Old inline CREATE TABLE SQL removed
- [x] FTS5 DROP+CREATE pattern for tokenizer migration
- [x] schema.sql loaded via `importlib.resources.files()`
- [x] Smoke test: bootstrap twice in-memory is a no-op
- [x] Full test suite: 28/28 pass, 0 regressions

## Key Files

### Modified
- `src/mcp_server_python_docs/storage/db.py`

## Deviations

None. Implementation matched the plan exactly.
