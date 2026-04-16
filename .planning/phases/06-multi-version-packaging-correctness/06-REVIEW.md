---
status: findings
phase: "06"
depth: standard
files_reviewed: 5
findings:
  critical: 1
  warning: 3
  info: 2
  total: 6
reviewed_at: "2026-04-15"
---

# Phase 6 Code Review: Multi-Version & Packaging Correctness

## Files Reviewed

1. `src/mcp_server_python_docs/ingestion/inventory.py`
2. `src/mcp_server_python_docs/__main__.py`
3. `src/mcp_server_python_docs/services/search.py`
4. `tests/test_multi_version.py`
5. `tests/test_packaging.py`

## Findings

### CR-01 [critical] — SearchService._resolve_version returns None, skips default-version resolution

**File:** `src/mcp_server_python_docs/services/search.py:38-59`

`SearchService._resolve_version(None)` returns `None`, which is then passed through to all ranker functions as the `version` parameter. The SQL queries use `(? IS NULL OR d.version = ?)`, so `None` means "search all versions." This is semantically different from `ContentService._resolve_version(None)`, which resolves to the actual default version string (e.g., "3.13").

**Impact:** When a user calls `search_docs(query="asyncio", version=None)`, they get results from ALL ingested versions (3.12 AND 3.13), with possible duplicates. But when they call `get_docs(slug=..., version=None)`, they get content from only the default version (3.13). This inconsistency means:
1. A search hit for version 3.12 followed by a `get_docs` call without specifying version will retrieve the 3.13 version of that page, not the 3.12 version the search matched.
2. The `search_docs` response already includes `version` in each `SymbolHit`, so the LLM client CAN pass it through -- but nothing forces this, and the behavior is surprising.

**Fix:** Either (a) make `SearchService._resolve_version` also resolve `None` to the default version, matching `ContentService`, or (b) document this as intentional ("search is cross-version by design") and add a note in the `search_docs` tool description telling the LLM to pass the hit's `version` field when calling `get_docs`. Option (b) may actually be the better UX for LLMs -- they see all versions and can compare.

---

### WR-01 [warning] — Duplicated _resolve_version logic across two services

**Files:** `src/mcp_server_python_docs/services/search.py:38-59`, `src/mcp_server_python_docs/services/content.py:29-57`

Both `SearchService` and `ContentService` have their own `_resolve_version` method. The implementations differ: `ContentService` falls back to `is_default=1` then `ORDER BY version DESC`, while `SearchService` returns `None` for unset version. The validation path (unknown version raises `VersionNotFoundError`) is duplicated with slightly different error message casing ("version" vs "Version").

**Impact:** Maintenance risk. If version resolution rules change (e.g., adding a per-session default), both must be updated. The inconsistent error casing (`"version {version!r} not found"` vs `"Version {version!r} not found"`) is minor but indicates copy-paste.

**Fix:** Extract a shared `resolve_version(db, version)` function or mixin. Even a module-level function in a shared location (e.g., `services/version_resolution.py`) would eliminate the duplication.

---

### WR-02 [warning] — test_packaging.py checks 7 deps but pyproject.toml has 8

**File:** `tests/test_packaging.py:76-86`

`test_required_deps_present` asserts 7 dependency names: `mcp, sphobjinv, pydantic, click, platformdirs, pyyaml, markdownify`. But `pyproject.toml` has 8 runtime dependencies -- `beautifulsoup4>=4.12,<5.0` is missing from the test's assertion list.

**Impact:** If someone accidentally removes `beautifulsoup4` from `pyproject.toml`, this PKG-02 test will not catch it. The `beautifulsoup4` package is imported as `bs4` in `ingestion/sphinx_json.py` and is required for content ingestion.

**Fix:** Add `"beautifulsoup4"` to the `required_dep_names` list in `test_required_deps_present`.

---

### WR-03 [warning] — --version flag uses raise SystemExit(0) instead of ctx.exit(0)

**File:** `src/mcp_server_python_docs/__main__.py:51`

The `--version` handler uses `raise SystemExit(0)` directly. While this works, Click provides `ctx.exit(0)` which integrates better with Click's own cleanup hooks and testing infrastructure. `SystemExit` bypasses Click's exception handling chain.

**Impact:** Low severity for production usage -- `SystemExit(0)` works fine. But in test environments using Click's `CliRunner`, a `SystemExit` can behave differently than a normal Click exit depending on the `catch_exceptions` setting. The current subprocess-based tests sidestep this by running in a separate process.

**Fix:** Replace `raise SystemExit(0)` with `ctx.exit(0)`.

---

### IR-01 [info] — Test asserts row dict access but fixture does not verify row_factory

**File:** `tests/test_multi_version.py:100-101`

Tests like `test_default_is_3_13` use dict-style row access (`rows[0]["is_default"]`), which requires `conn.row_factory = sqlite3.Row`. This works because `get_readwrite_connection` sets `row_factory = sqlite3.Row`. But if the test fixture ever stops using `get_readwrite_connection` and creates a raw connection, all dict-access assertions would fail with `TypeError`.

**Impact:** Fragile coupling. Not a bug today, but worth noting.

---

### IR-02 [info] — test_version_flag_output and test_module_runnable overlap significantly

**File:** `tests/test_packaging.py:93-148`

`TestVersionFlag.test_version_flag_output` and `TestInstallability.test_module_runnable` both run `python -m mcp_server_python_docs --version` and assert `0.1.0` in the output. They test slightly different requirements (PKG-06 vs PKG-03) but execute identical subprocess commands.

**Impact:** Minor test redundancy. Both tests spawn a subprocess doing the same thing. Not harmful, but adds ~2 seconds of test time.

---

## Summary

| Severity | Count | Key Theme |
|----------|-------|-----------|
| Critical | 1 | Version resolution inconsistency between search and content services |
| Warning  | 3 | Code duplication, missing dep assertion, non-idiomatic exit |
| Info     | 2 | Test fragility, test redundancy |

The critical finding (CR-01) should be evaluated for whether the cross-version search behavior is intentional design or an oversight. If intentional, it needs documentation. If an oversight, `SearchService._resolve_version` should be aligned with `ContentService._resolve_version`.
