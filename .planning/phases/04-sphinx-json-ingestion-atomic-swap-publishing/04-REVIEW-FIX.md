---
status: all_fixed
phase: "04"
findings_in_scope: 6
fixed: 6
skipped: 0
iteration: 1
fixed_at: "2026-04-15"
---

# Phase 04 Code Review Fix Report

## Fix Summary

All 6 findings in scope (1 critical + 5 warnings) were fixed and committed atomically. All 110 tests pass. Ruff lint is clean.

## Fixes Applied

### CR-01: Add beautifulsoup4 as explicit dependency -- FIXED

**Commit:** `353e394` fix(04): add beautifulsoup4 as explicit dependency (CR-01)
**File:** `pyproject.toml`
**Change:** Added `"beautifulsoup4>=4.12,<5.0"` to `[project] dependencies`. Previously only available as transitive dep of markdownify.

### WR-01: Use platform-aware venv scripts path -- FIXED

**Commit:** `dd349ac` fix(04): use platform-aware venv scripts path (WR-01)
**File:** `src/mcp_server_python_docs/__main__.py`
**Change:** Replaced hardcoded `os.path.join(venv_dir, "bin", "pip")` and `os.path.join(venv_dir, "bin", "sphinx-build")` with a `scripts_dir` variable that resolves to `"Scripts"` on Windows and `"bin"` elsewhere.

### WR-02: Wrap publish_index connections in try/finally -- FIXED

**Commit:** `9c390a6` fix(04): wrap publish_index connections in try/finally (WR-02)
**File:** `src/mcp_server_python_docs/ingestion/publish.py`
**Change:** All three `get_readwrite_connection()` calls in `publish_index()` now use try/finally to ensure `conn.close()` runs even on exceptions.

### WR-03: Wrap build_index connection in try/finally -- FIXED

**Commit:** `1f52205` fix(04): wrap build_index connection in try/finally (WR-03)
**File:** `src/mcp_server_python_docs/__main__.py`
**Change:** The main database connection in `build_index()` is now wrapped in try/finally, ensuring cleanup on `FTS5UnavailableError` or any other exception.

### WR-04: Break long line in smoke test message -- FIXED

**Commit:** `ef53b70` fix(04): break long line in smoke test message (WR-04)
**File:** `src/mcp_server_python_docs/ingestion/publish.py`
**Change:** Split 113-character string into two implicit concatenated strings to stay within the 100-character ruff limit.

### WR-05: Remove unused imports and fix import ordering -- FIXED

**Commit:** `422f718` fix(04): remove unused imports and fix import ordering (WR-05)
**Files:** `tests/test_ingestion.py`, `tests/test_publish.py`
**Change:** Removed unused imports (`json`, `Path`, `bootstrap_schema`, `get_readwrite_connection`, `pytest`). Auto-sorted import blocks via ruff.

## Info Findings (not in scope)

The 3 info-level findings (IR-01 timestamp collision, IR-02 non-atomic two-step swap, IR-03 per-file commits) were not in scope for this fix pass. They are documented in the review for future consideration.

## Verification

- All 110 tests pass (38 phase-4 + 72 prior)
- Ruff lint: all checks passed on all phase 4 files
