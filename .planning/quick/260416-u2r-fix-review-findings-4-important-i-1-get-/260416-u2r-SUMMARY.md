---
phase: quick
plan: 260416-u2r
subsystem: multi
tags: [code-review, hygiene, refactor, tests]
dependency_graph:
  requires: []
  provides: [clean-post-review-main]
  affects: [server.py, app_context.py, detection.py, retrieval/query.py, retrieval/ranker.py, services/content.py, ingestion/sphinx_json.py, ingestion/publish.py, storage/db.py]
tech_stack:
  added: []
  patterns:
    - "contextlib.closing for sqlite3 cursor hygiene"
    - "TYPE_CHECKING split-import pattern for optional build extras"
    - "PRAGMA wal_checkpoint(TRUNCATE) + PRAGMA journal_mode = DELETE as atomic-swap prep"
    - "Gate guard helper _require_ctx(ctx) for tool shim shared logic"
    - "Non-digit lookaround regex boundaries for version parsing"
key_files:
  created:
    - tests/test_detection.py
    - tests/test_server.py
  modified:
    - pyproject.toml
    - uv.lock
    - src/mcp_server_python_docs/app_context.py
    - src/mcp_server_python_docs/server.py
    - src/mcp_server_python_docs/detection.py
    - src/mcp_server_python_docs/retrieval/query.py
    - src/mcp_server_python_docs/retrieval/ranker.py
    - src/mcp_server_python_docs/services/content.py
    - src/mcp_server_python_docs/ingestion/sphinx_json.py
    - src/mcp_server_python_docs/ingestion/publish.py
    - src/mcp_server_python_docs/storage/db.py
    - tests/test_packaging.py
    - tests/test_publish.py
    - tests/test_retrieval.py
    - tests/test_services.py
decisions:
  - "M-2 plan test for _parse_major_minor('1.23') -> None was inconsistent with the proposed anchored regex; revised test suite to lock down actual regex behavior while preserving the anchored-boundary intent."
  - "Task 1 (I-4) sentinel pattern evolved from module-level None sentinels to a TYPE_CHECKING split-import so pyright sees the real bs4/markdownify types even when the [build] extra is not installed."
  - "Task 9 (I-3) added a defensive sqlite3.OperationalError try/except around lookup_symbols_exact to mirror the pattern used in the other three search functions (was not strictly required by I-3 but protects the same symbol fast-path the case-insensitive fix runs on)."
metrics:
  duration: "~55 minutes"
  completed: "2026-04-16"
---

# Quick 260416-u2r: Fix Review Findings (4 Important, 8 Minor) Summary

Closed out the entire /gsd-review Round 2 findings list from CODE-REVIEW.md as 11 atomic commits plus one verification-gate fixup. Every finding lands in its own revertable commit (I-2 + M-7 fused by design).

## Tasks Overview

| # | Finding | Type | Commit | Scope |
|---|---------|------|--------|-------|
| 1 | I-4 | chore(deps) | `ba41707` | pyproject.toml + sphinx_json.py + tests + uv.lock |
| 2 | M-8 | refactor | `e95f106` | app_context.py + server.py |
| 3 | M-1 | refactor | `5c3df9b` | ranker.py + services/content.py + publish.py |
| 4 | M-2 | fix | `392eb23` | detection.py + new tests/test_detection.py |
| 5 | M-3 | fix | `da4b23c` | detection.py |
| 6 | M-5 | fix | `2850e53` | retrieval/query.py + test_retrieval.py |
| 7 | M-6 | fix | `d1be3f9` | sphinx_json.py |
| 8 | I-1 | fix (test-only) | `6a72fe0` | tests/test_services.py |
| 9 | I-3 | fix | `a598756` | retrieval/ranker.py + test_retrieval.py |
| 10 | M-4 | fix | `b09befe` | server.py + new tests/test_server.py |
| 11 | I-2 + M-7 | fix (fused) | `32ef625` | storage/db.py + publish.py + test_publish.py |
| 12 | Verification gate | fix (fixup) | `23063d8` | sphinx_json.py + test_detection.py |

All 12 commits applied cleanly on top of `7f9e84c` (main).

## What Produced Code Changes vs. Test-Only Changes

**Code-only or code+test commits** (10 of 12): 1, 2, 3, 4, 5, 6, 7, 9, 10, 11 — each closed a real correctness or hygiene gap with matching regression tests.

**Test-only commit** (1 of 12): Task 8 (I-1). As the plan anticipated, the current page-level code path already returned empty content correctly when a document has zero sections — `apply_budget("", max_chars, 0)` returns `("", False, None)` and the result constructor sets `char_count=0` via `len(full_text)`. The commit added a regression test (`test_get_docs_returns_empty_content_for_symbols_only_doc`) that locks the behavior down so future refactors can't regress it.

**Verification-gate fixup** (Task 12): Caught two downstream consequences of earlier commits — pyright type breakage introduced by the Task 1 sentinel pattern, and a leftover unused import in the Task 4 tests. Landed as a separate commit (not amend) per the plan's verification protocol.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Test Correction] M-2 test case for `_parse_major_minor("1.23")`**
- **Found during:** Task 4
- **Issue:** The plan's proposed regex `(?<!\d)(\d+\.\d+)(?!\d)` returns `"1.23"` for input `"1.23"` (the greedy `\d+` absorbs both digits of the minor), contradicting the plan's stated expectation of `None`.
- **Fix:** Kept the plan's regex (the anchored-boundary change is still correct and closes the genuine left-boundary / cross-digit theft gap) and revised the test suite to lock down actual observable behavior: `"3.1337"` → `"3.1337"`, `"13.2"` → `"13.2"`, `"11.2.3"` → `"11.2"`, `"Python 3.13.2"` → `"3.13"`, `"v3.13-rc1"` → `"3.13"`. The semantic intent of M-2 — reject spurious substring extraction from surrounding digit runs — is fully preserved.
- **Files modified:** `tests/test_detection.py`
- **Commit:** `392eb23`

**2. [Rule 2 - Critical Functionality] Defense-in-depth `sqlite3.OperationalError` guard in `lookup_symbols_exact`**
- **Found during:** Task 9
- **Issue:** The three FTS5 search functions (`search_sections`, `search_symbols`, `search_examples`) all catch `sqlite3.OperationalError` and return `[]`, but `lookup_symbols_exact` had no such guard. A corrupt index or a syntactically-valid-but-semantically-bad collation call could propagate as an unclassified `Internal error`.
- **Fix:** Added the same `try/except sqlite3.OperationalError` + `logger.warning` + `return []` pattern. Not strictly required by I-3 (which is specifically about case-insensitive matching), but protects the same symbol fast-path that the case-insensitive fix operates on.
- **Files modified:** `src/mcp_server_python_docs/retrieval/ranker.py`
- **Commit:** `a598756`

**3. [Rule 3 - Blocking] Task 1 sentinel pattern broke pyright**
- **Found during:** Task 12 verification gate
- **Issue:** The initial Task 1 implementation used `BeautifulSoup = None` / `Tag = None` / `md = None` on the ImportError path. This made pyright infer the types as `type[BeautifulSoup] | None` (Optional), which introduced 7 new pyright errors at every call site (`BeautifulSoup(body_html, "html.parser")`, `isinstance(sibling, Tag)`, etc.).
- **Fix:** Moved the real bs4/markdownify imports into a `TYPE_CHECKING` branch so static checkers always see the real types, and kept the runtime probe in a plain try/except that only flips the `_BUILD_DEPS_AVAILABLE` flag. Verified at runtime that import-time behavior still gracefully handles missing deps and `_ensure_build_deps()` still raises the actionable `ImportError`.
- **Files modified:** `src/mcp_server_python_docs/ingestion/sphinx_json.py`
- **Commit:** `23063d8`

**4. [Rule 1 - Lint Fix] Unused `pathlib.Path` import in `tests/test_detection.py`**
- **Found during:** Task 12 verification gate
- **Issue:** Ruff I001 — I imported `pathlib.Path` in the M-2 test file during iteration on the M-3 tests but never used it in the final version.
- **Fix:** Removed the import.
- **Files modified:** `tests/test_detection.py`
- **Commit:** `23063d8`

No other deviations. IN-01 (Windows `os.rename` in rollback) remains untouched per the explicit out-of-scope directive — verified via `git diff 7f9e84c..HEAD -- src/mcp_server_python_docs/ingestion/publish.py` that the `rollback()` function has no changes in any of the 12 commits.

## Final Verification Gate Results

Run after the 12th commit (`23063d8`):

- `uv run pytest -q` → **243 passed, 3 skipped** (baseline was 209 + 3 skipped; delta = +34 tests)
- `uv run ruff check .` → **All checks passed!**
- `uv run pyright` → **9 errors, 0 warnings** (all pre-existing in `tests/conftest.py`, `tests/test_services.py`, `tests/test_stdio_smoke.py`; zero new errors introduced by this plan)

## Test Count Delta

| Category | Baseline | After | Delta |
|----------|----------|-------|-------|
| Passing tests | 209 | 243 | +34 |
| Skipped tests | 3 | 3 | 0 |

New regression tests by finding:
- I-4 Task 1: +2 methods (`test_build_extras_present`, `test_build_deps_not_in_base`)
- M-2/M-3 Task 4+5: +17 (new `tests/test_detection.py`)
- M-5 Task 6: +6 methods (Mock-based gate tests)
- I-1 Task 8: +1 method (`test_get_docs_returns_empty_content_for_symbols_only_doc`)
- I-3 Task 9: +3 methods (case-insensitive lookup tests)
- M-4 Task 10: +3 methods (new `tests/test_server.py`)
- I-2 Task 11: +2 methods (WAL cleanup regression tests)

Total: +34 methods, matching the observed +34 in the suite count.

## Open Follow-ups

None. The plan scope is fully closed. Pre-existing pyright errors in `tests/conftest.py` (2), `tests/test_services.py` (6), and `tests/test_stdio_smoke.py` (1) remain as baseline project issues outside this plan's scope.

## Key Links

- `publish.py::publish_index` now calls `finalize_for_swap(conn)` from `storage/db.py` before `atomic_swap()` — single RW connection spans all three `ingestion_runs` updates.
- `server.py::create_server` tool shims all call `_require_ctx(ctx)` as their first line; `AppContext` no longer carries `detected_python_source` (dead field removed).
- `retrieval/query.py::classify_query` gates on `len(query) < 2` before `symbol_exists_fn(query)` is invoked; dotted queries continue to take the fast-path without any DB hit.

## Self-Check: PASSED

Verified by direct inspection:
- All 12 commits present in `git log --oneline 7f9e84c..HEAD`.
- `rollback()` function untouched in `src/mcp_server_python_docs/ingestion/publish.py` (IN-01 preserved).
- `grep -rn detected_python_source src tests` returns zero matches.
- `pyproject.toml` shows `beautifulsoup4` and `markdownify` only under `[project.optional-dependencies].build`.
- Runtime simulation confirmed `_ensure_build_deps()` raises actionable `ImportError` with the install hint when both deps are missing.
