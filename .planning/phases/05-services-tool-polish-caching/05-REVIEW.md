---
status: findings
phase: "05"
phase_name: services-tool-polish-caching
depth: standard
files_reviewed: 10
findings:
  critical: 1
  warning: 4
  info: 3
  total: 8
reviewed_at: "2026-04-15"
---

# Phase 05 Code Review: Services, Tool Polish, Caching

## Files Reviewed

1. `src/mcp_server_python_docs/services/__init__.py`
2. `src/mcp_server_python_docs/services/search.py`
3. `src/mcp_server_python_docs/services/content.py`
4. `src/mcp_server_python_docs/services/version.py`
5. `src/mcp_server_python_docs/services/observability.py`
6. `src/mcp_server_python_docs/services/cache.py`
7. `src/mcp_server_python_docs/server.py`
8. `src/mcp_server_python_docs/app_context.py`
9. `src/mcp_server_python_docs/__main__.py`
10. `tests/test_services.py`

## Findings

### CR-01: SearchService._resolve_symbol created but never used [warning]

**File:** `src/mcp_server_python_docs/services/search.py`, line 35

The constructor creates `self._resolve_symbol = create_symbol_cache(db)` but this cached function is never called anywhere in the class. The `_symbol_exists` method performs its own raw SQL query instead of using the cached resolver. This means:

1. A symbol cache is allocated (128-slot LRU) but never populated, wasting memory.
2. The `_symbol_exists` method bypasses the cache, defeating OPS-04 for symbol lookups in the classify path.

**Recommendation:** Either remove `self._resolve_symbol` if it is not needed yet, or refactor `_symbol_exists` to use it. If the cache is intended for future use, add a comment explaining that.

```python
# Current (line 35):
self._resolve_symbol = create_symbol_cache(db)

# Option A - Remove unused:
# (delete line 35 and the create_symbol_cache import)

# Option B - Use it:
def _symbol_exists(self, name: str) -> bool:
    # Needs version param -- but classify_query doesn't have version context
    # This is why direct SQL is used instead. Add a comment.
    ...
```

---

### CR-02: validate-corpus raises SystemExit(0) on success [warning]

**File:** `src/mcp_server_python_docs/__main__.py`, line 328

```python
if passed:
    logger.info("Corpus validation PASSED")
    raise SystemExit(0)  # <-- problematic
```

Raising `SystemExit(0)` on success is unconventional and can cause issues:

1. Click expects commands to return normally for exit code 0. Raising `SystemExit(0)` bypasses Click's cleanup and any `finally` blocks in the call chain.
2. In tests, `SystemExit(0)` is caught by pytest as an exception, making the success path harder to test (the test would need `pytest.raises(SystemExit)`).
3. Inconsistency: the `serve` command returns normally on success.

**Recommendation:** Let the function return normally for the success case. Only raise `SystemExit(1)` for failure.

```python
if passed:
    logger.info("Corpus validation PASSED")
    # Return normally -- Click exits with code 0 by default
else:
    logger.error("Corpus validation FAILED")
    raise SystemExit(1)
```

---

### CR-03: Return type annotation uses lowercase `callable` instead of `Callable` [warning]

**File:** `src/mcp_server_python_docs/services/cache.py`, lines 39 and 73

```python
def create_section_cache(db: sqlite3.Connection) -> callable:
def create_symbol_cache(db: sqlite3.Connection) -> callable:
```

`callable` (lowercase) is the built-in function, not a type. While Python 3.12+ allows `callable` as a type in some contexts, the correct type annotation is `Callable` from `collections.abc` (or `typing`). Pyright will flag this as an error in strict mode. Since the return types are specific closure signatures, a more precise annotation would be ideal.

**Recommendation:**

```python
from collections.abc import Callable

def create_section_cache(db: sqlite3.Connection) -> Callable[[int], CachedSection | None]:
    ...

def create_symbol_cache(db: sqlite3.Connection) -> Callable[[str, str], CachedSymbol | None]:
    ...
```

---

### CR-04: Observability decorator is not async-compatible [warning]

**File:** `src/mcp_server_python_docs/services/observability.py`, lines 50-106

The `log_tool_call` decorator uses a synchronous `wrapper` function. If any service method is ever made `async` (e.g., for async SQLite or future HTTP-based data sources), the decorator will silently break -- it will return the coroutine object instead of awaiting it, and the timing measurement will be wrong.

This is not a bug today since all service methods are synchronous, but it is a fragility risk given that the MCP server framework is async-native.

**Recommendation:** Add a comment documenting this limitation, or make the wrapper detect async functions:

```python
# Note: This decorator only works with synchronous service methods.
# If a method becomes async, this wrapper must be updated to await the result.
```

---

### IR-01: AppContext service fields typed as Optional but always set [info]

**File:** `src/mcp_server_python_docs/app_context.py`, lines 27-29

```python
search_service: SearchService | None = None
content_service: ContentService | None = None
version_service: VersionService | None = None
```

These are typed as `Optional` with default `None`, but `app_lifespan` in `server.py` always sets all three. Tool handlers access them via `app_ctx.search_service.search(...)` without None-checking, which would produce `AttributeError` at runtime if a service were actually `None`. The type system provides no protection here.

This is not a bug (the lifespan always sets them) but weakens type safety. Consider making them required fields or adding assertions in tool handlers.

---

### IR-02: Observability version extraction from positional args is fragile [info]

**File:** `src/mcp_server_python_docs/services/observability.py`, lines 65-71

```python
version_val = kwargs.get("version")
if version_val is None and args:
    if len(args) >= 2:
        version_val = args[1]
```

This assumes `version` is always the second positional argument. It works today because `search(query, version, ...)` and `get_docs(slug, version, ...)` both have version as `args[1]`. But if someone adds a parameter before `version`, the log will silently extract the wrong value. The decorator already has special handling per result type -- extending it to use `inspect.signature` or kwargs-only calls would be more robust, though the current approach is acceptable given the small number of methods.

---

### IR-03: Development tools (ruff, pyright) not installed in project venv [info]

The `pyproject.toml` development dependencies do not appear to include `ruff` or `pyright` in the venv. The CLAUDE.md lists both as required development tools. Without them in the venv, CI/CD and local linting/type-checking cannot run from the project environment.

**Recommendation:** Ensure both are in dev dependencies (e.g., `[project.optional-dependencies]` or `[tool.uv.dev-dependencies]`).

---

## Summary

The Phase 5 implementation is solid. The service layer cleanly separates concerns (server -> services -> retrieval/storage), the dependency rule is enforced (no MCP imports in services, no direct SQL in services), and the observability logging works correctly via stderr.

The most actionable finding is **CR-01** (unused `_resolve_symbol` cache) which wastes memory. **CR-02** (SystemExit on success) is a correctness issue that will make the validate-corpus command harder to test properly. **CR-03** (lowercase `callable`) is a type annotation error that will cause issues with strict type checkers.

All 34 tests pass. The architecture is well-structured for future extension.
