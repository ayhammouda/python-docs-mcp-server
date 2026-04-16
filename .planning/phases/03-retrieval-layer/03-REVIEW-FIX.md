---
status: all_fixed
phase: "03"
phase_name: retrieval-layer
fix_scope: critical_warning
findings_in_scope: 3
fixed: 3
skipped: 0
iteration: 1
fixed_at: "2026-04-15"
---

# Phase 03 Code Review Fix Report

## Summary

All 3 warning-level findings from 03-REVIEW.md were fixed and committed atomically. 4 info-level findings were out of scope (critical_warning mode). All 72 tests pass after fixes.

## Fixes Applied

### WR-01: Remove unused imports in server.py -- FIXED

**Commit:** `3c0ef9c`
**Change:** Removed unused `SymbolHit` import from `mcp_server_python_docs.models` and unused `fts5_escape` import from `mcp_server_python_docs.retrieval.query` in server.py.
**Verification:** `ruff check src/mcp_server_python_docs/server.py` passes clean.

### WR-02: Add missing assertion in test_budget_combining_at_boundary -- FIXED

**Commit:** `b018ff4`
**Change:** Replaced dead `last_cat` assignment with an assertion that validates the last character of the truncated result is not an orphaned combining mark (Unicode category starting with "M").
**Verification:** `pytest tests/test_retrieval.py::test_budget_combining_at_boundary` passes.

### WR-03: Convert f-string logging to lazy %-formatting -- FIXED

**Commit:** `81b7f47`
**Change:** Converted 5 f-string logger calls to lazy %-formatting:
- `ranker.py` line 90: `logger.warning("FTS5 query failed for sections: %r", match_expr)`
- `ranker.py` line 148: `logger.warning("FTS5 query failed for symbols: %r", match_expr)`
- `ranker.py` line 207: `logger.warning("FTS5 query failed for examples: %r", match_expr)`
- `server.py` line 80: `logger.info("Loaded %d synonym entries", len(synonyms))`
- `server.py` line 97: `logger.error("Lifespan error: %s", error_msg)`
**Verification:** `ruff check` passes clean on both files. 72 tests pass.

## Out of Scope (Info findings, not included in critical_warning mode)

- IF-01: Import sort order in tests/test_retrieval.py
- IF-02: Line length violations in test fixture SQL
- IF-03: expand_synonyms substring matching design note
- IF-04: lookup_symbols_exact lacks OperationalError guard (by design)

## Post-Fix Verification

- 72 tests pass (44 retrieval + 28 prior)
- Pyright: 0 errors, 0 warnings
- Ruff: 0 errors on production files (src/)
- Ruff: 5 remaining info-level issues on test file (import sort, line length) -- out of scope
