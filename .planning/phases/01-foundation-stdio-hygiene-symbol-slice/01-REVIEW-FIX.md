---
status: all_fixed
phase: "01"
phase_name: foundation-stdio-hygiene-symbol-slice
findings_in_scope: 10
fixed: 10
skipped: 0
iteration: 2
---

# Phase 01 Code Review Fix Report

## Fix Summary

All 10 findings from 01-REVIEW.md have been resolved in a single commit. Iteration 2 re-review confirmed zero new issues introduced.

## Fixes Applied

### CR-01: FTS5 check on read-only connection [FIXED]
**Commit:** `9a74943`
**File:** `src/mcp_server_python_docs/storage/db.py`
**Fix:** `assert_fts5_available()` now handles read-only connections by catching the `OperationalError: attempt to write a readonly database` and falling back to `PRAGMA compile_options` to check for `ENABLE_FTS5`. The CREATE/DROP path is still used for read-write connections as a definitive check.

### WR-01: search_docs version filtering [FIXED]
**Commit:** `9a74943`
**File:** `src/mcp_server_python_docs/server.py`
**Fix:** SQL query now joins `symbols s` with `doc_sets d` on `doc_set_id` and filters with `AND (? IS NULL OR d.version = ?)`. Version is read from the result row (`row["version"]`) instead of hardcoded `"3.13"`.

### WR-02: LIKE wildcards not escaped [FIXED]
**Commit:** `9a74943`
**File:** `src/mcp_server_python_docs/server.py`
**Fix:** User input is now escaped (`%` -> `\%`, `_` -> `\_`, `\` -> `\\`) before interpolation into the LIKE pattern. The SQL uses `ESCAPE '\'` clause.

### WR-03: ToolAnnotations as dict [FIXED]
**Commit:** `9a74943`
**File:** `src/mcp_server_python_docs/server.py`
**Fix:** Changed from `annotations={...}` dict literal to `annotations=ToolAnnotations(readOnlyHint=True, ...)`. Added `from mcp.types import ToolAnnotations` import.

### WR-04: Unused imports in server.py [FIXED]
**Commit:** `9a74943`
**File:** `src/mcp_server_python_docs/server.py`
**Fix:** Removed unused `FTS5UnavailableError` and `IndexNotBuiltError` imports. Replaced with `ToolAnnotations` import (needed for WR-03 fix).

### WR-05: Empty version strings accepted [FIXED]
**Commit:** `9a74943`
**File:** `src/mcp_server_python_docs/__main__.py`
**Fix:** Added `if v.strip()` filter to version list comprehension. Added early exit with error message if no valid versions remain.

### IR-01: Import ordering violations [FIXED]
**Commit:** `9a74943`
**Files:** `src/mcp_server_python_docs/__main__.py`, `src/mcp_server_python_docs/models.py`
**Fix:** Added `# noqa: E402` to intentional late imports (`logging`, `click`). Reordered `platformdirs` import before local imports in `build_index()`. Removed extra blank line in `models.py` import block.

### IR-02: Unused imports in test files [FIXED]
**Commit:** `9a74943`
**Files:** `tests/test_phase1_integration.py`, `tests/test_stdio_hygiene.py`, `tests/test_synonyms.py`
**Fix:** Removed unused `sqlite3` import from integration tests. Removed unused `pytest` imports from hygiene and synonym tests.

### IR-03: Line too long in models.py [FIXED]
**Commit:** `9a74943`
**File:** `src/mcp_server_python_docs/models.py`
**Fix:** Wrapped long `description` string with parenthesized string concatenation to stay under 100-char limit.

### IR-04: Pyright errors from untyped sphobjinv [FIXED]
**Commit:** `9a74943`
**File:** `src/mcp_server_python_docs/ingestion/inventory.py`
**Fix:** Added targeted `# type: ignore[...]` comments with explanatory notes for `DataObjStr` attribute access, comparison, return types, and `Inventory(url=...)` constructor call. Pyright now reports 0 errors.

## Verification

After all fixes:
- **ruff:** All checks passed (0 errors)
- **pyright:** 0 errors, 0 warnings, 0 informations
- **pytest:** 23 passed in 0.95s

## Files Modified

- `src/mcp_server_python_docs/storage/db.py`
- `src/mcp_server_python_docs/server.py`
- `src/mcp_server_python_docs/__main__.py`
- `src/mcp_server_python_docs/models.py`
- `src/mcp_server_python_docs/ingestion/inventory.py`
- `tests/test_phase1_integration.py`
- `tests/test_stdio_hygiene.py`
- `tests/test_synonyms.py`
