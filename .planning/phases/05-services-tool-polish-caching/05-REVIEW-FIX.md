---
status: all_fixed
phase: "05"
phase_name: services-tool-polish-caching
findings_in_scope: 4
fixed: 4
skipped: 0
iteration: 1
fixed_at: "2026-04-15"
---

# Phase 05 Code Review Fix Report

## Fix Scope

Critical + Warning only (4 findings in scope; 3 Info findings excluded).

## Fixes Applied

### CR-01: Remove unused _resolve_symbol cache from SearchService [FIXED]

**Commit:** `fix(05): remove unused _resolve_symbol cache from SearchService (CR-01)`

**Changes:**
- Removed `self._resolve_symbol = create_symbol_cache(db)` from `SearchService.__init__`
- Removed unused `from mcp_server_python_docs.services.cache import create_symbol_cache` import
- Added comment explaining why `_symbol_exists` uses direct SQL (classify_query callback has no version context, so the version-scoped cache cannot be used)

**File:** `src/mcp_server_python_docs/services/search.py`

---

### CR-02: Let validate-corpus return normally on success [FIXED]

**Commit:** `fix(05): let validate-corpus return normally on success (CR-02)`

**Changes:**
- Replaced `raise SystemExit(0)` with a comment and normal return on success
- Failure path still raises `SystemExit(1)` as expected

**File:** `src/mcp_server_python_docs/__main__.py`

---

### CR-03: Use Callable type annotations in cache factory functions [FIXED]

**Commit:** `fix(05): use Callable type annotations in cache factory functions (CR-03)`

**Changes:**
- Added `from collections.abc import Callable` import
- Changed `create_section_cache` return type from `callable` to `Callable[[int], CachedSection | None]`
- Changed `create_symbol_cache` return type from `callable` to `Callable[[str, str], CachedSymbol | None]`

**File:** `src/mcp_server_python_docs/services/cache.py`

---

### CR-04: Document sync-only limitation on log_tool_call decorator [FIXED]

**Commit:** `fix(05): document sync-only limitation on log_tool_call decorator (CR-04)`

**Changes:**
- Added docstring note explaining the decorator only works with synchronous service methods
- Documents that the wrapper must be updated to detect coroutines and await results if any method becomes async

**File:** `src/mcp_server_python_docs/services/observability.py`

---

## Verification

- All 34 Phase 5 tests pass after fixes
- Full test suite: 144 tests pass, 0 failures
- No regressions introduced

## Info Findings (Not Fixed -- Out of Scope)

- **IR-01:** AppContext service fields typed as Optional but always set -- acceptable since lifespan guarantees them
- **IR-02:** Observability version extraction from positional args -- acceptable given the small number of methods
- **IR-03:** Dev tools (ruff, pyright) not in venv -- deferred to project setup improvements
