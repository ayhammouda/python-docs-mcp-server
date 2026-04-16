---
status: findings
phase: "03"
phase_name: retrieval-layer
depth: standard
files_reviewed: 6
findings:
  critical: 0
  warning: 3
  info: 4
  total: 7
reviewed_at: "2026-04-15"
---

# Phase 03 Code Review: Retrieval Layer

## Summary

Reviewed 6 files at standard depth. All 72 tests pass. Pyright reports 0 errors. Ruff found 8 lint violations (3 auto-fixable). No critical issues found. Three warnings and four informational findings.

## Files Reviewed

1. `src/mcp_server_python_docs/retrieval/__init__.py`
2. `src/mcp_server_python_docs/retrieval/query.py`
3. `src/mcp_server_python_docs/retrieval/ranker.py`
4. `src/mcp_server_python_docs/retrieval/budget.py`
5. `src/mcp_server_python_docs/server.py`
6. `tests/test_retrieval.py`

---

## Findings

### WR-01: Unused imports in server.py (Warning)

**File:** `src/mcp_server_python_docs/server.py`, lines 27 and 31
**Category:** Code quality / lint

`SymbolHit` (line 27) and `fts5_escape` (line 31) are imported but never used. Ruff F401 catches both. These are dead imports left over from wiring the retrieval layer into server.py.

**Impact:** Minor -- dead code, no runtime effect, but violates the project's ruff-clean convention.

**Fix:** Remove both unused imports.

### WR-02: Incomplete test assertion in test_budget_combining_at_boundary (Warning)

**File:** `tests/test_retrieval.py`, lines 586-589
**Category:** Test quality

The test assigns `last_cat = unicodedata.category(result[-1])` but never asserts anything with it. The variable is assigned and immediately followed by a comment explaining the intent, but no actual assertion is made. Ruff catches this as F841 (unused variable).

```python
if len(result) > 0:
    last_cat = unicodedata.category(result[-1])
    # Last char should not be a combining mark without seeing its base
    # (which is fine since we include the base)
```

**Impact:** Medium -- the test passes unconditionally for the combining-mark-at-boundary case, providing false confidence. The intent is clear but unimplemented.

**Fix:** Add an assertion that validates the last character is not an orphaned combining mark:
```python
if len(result) > 0:
    last_cat = unicodedata.category(result[-1])
    assert not last_cat.startswith("M"), (
        f"Last char is orphaned combining mark (category {last_cat})"
    )
```

### WR-03: f-string logging in ranker.py and server.py (Warning)

**File:** `src/mcp_server_python_docs/retrieval/ranker.py`, lines 90, 148, 207
**File:** `src/mcp_server_python_docs/server.py`, lines 80, 97
**Category:** Code quality / performance

All logging calls use f-strings (`logger.warning(f"...")`), which evaluate the string even when the log level is suppressed. The idiomatic pattern is `logger.warning("...: %r", match_expr)` with lazy %-formatting.

**Impact:** Low -- these are in error/warning paths that execute rarely. No correctness issue. But the project's CLAUDE.md references ruff as the lint standard and ruff's G004 rule flags this pattern.

**Fix:** Convert to lazy formatting:
```python
logger.warning("FTS5 query failed for sections: %r", match_expr)
```

---

### IF-01: Import sort order in tests/test_retrieval.py (Info)

**File:** `tests/test_retrieval.py`, lines 2-30
**Category:** Lint / formatting

Ruff I001 flags the import block as unsorted. The `from mcp_server_python_docs.models import SymbolHit` import (line 30) should be grouped with the other `mcp_server_python_docs` imports above it.

**Fix:** `ruff check --fix` will auto-sort.

### IF-02: Line length violations in test fixture SQL (Info)

**File:** `tests/test_retrieval.py`, lines 65, 68, 71, 73
**Category:** Lint / formatting

Four SQL INSERT string literals in the `fts_db` fixture exceed 100 chars (the configured max). These are inline SQL test data strings that are hard to split readably.

**Fix:** Either suppress E501 for the fixture block or break the SQL strings across more lines.

### IF-03: expand_synonyms substring matching may over-expand (Info)

**File:** `src/mcp_server_python_docs/retrieval/query.py`, lines 121-125
**Category:** Design consideration

The multi-word concept check uses `if concept in query_lower`, which is a substring match. This means a query like "testing" would match the synonym key "test" (if it existed), and "parsing" would match "parse json" (because "parse" is not in "parsing" -- actually this specific case does NOT match). More subtly, a query like "parse json data" would match "parse json" correctly, but a query like "how to parse" would NOT match "parse json".

The current behavior is reasonable and intentional (per SUMMARY: "multi-word concept support"). Just noting that `in` is a substring match on the full query string, not a token-subset match.

**Impact:** None for current synonym table. Could produce unexpected expansions if future synonym keys happen to be substrings of common words.

### IF-04: lookup_symbols_exact has no OperationalError guard (Info)

**File:** `src/mcp_server_python_docs/retrieval/ranker.py`, lines 252-264
**Category:** Consistency

The three FTS5 search functions (`search_sections`, `search_symbols`, `search_examples`) all wrap their queries in `try/except sqlite3.OperationalError` for graceful degradation. `lookup_symbols_exact` does not, because it uses a regular SQL query (not FTS5 MATCH). This is correct -- non-FTS queries should not raise OperationalError under normal conditions. However, if the symbols table is missing (corrupt index), the error would propagate as an unhandled exception.

**Impact:** Low -- the lifespan startup validates the database, so a missing table at query time would indicate a corrupted index, which should indeed crash. Current behavior is defensible.

---

## Requirement Coverage

| Requirement | Status | Evidence |
|---|---|---|
| RETR-01 | Covered | `fts5_escape` wraps every token in double quotes; null bytes stripped |
| RETR-02 | Covered | `test_no_raw_match_in_source` scans all .py files; all MATCH uses parameterized `?` |
| RETR-03 | Covered | 50+ fuzz inputs tested against real FTS5 MATCH |
| RETR-04 | Covered | `classify_query` detects dotted names and module names with callback injection |
| RETR-05 | Covered | `expand_synonyms` with token + multi-word concept matching |
| RETR-06 | Covered | BM25 weights: heading 10x, qualified_name 10x |
| RETR-07 | Covered | FTS5 snippet() with 32-token, ~200 char excerpts on every hit |
| RETR-08 | Covered | `apply_budget` with combining-mark-safe truncation and pagination |
| RETR-09 | Covered | All search paths return `SymbolHit` with identical shape |
| SRVR-08 | Covered | Domain errors caught and re-raised as `ToolError` for `isError: true` |

## Verification

- 72 tests pass (44 retrieval + 28 prior)
- Pyright: 0 errors, 0 warnings
- Ruff: 8 violations (2 unused imports, 1 unused variable, 1 import sort, 4 line length)
- No `print()` calls in retrieval layer (stdio hygiene preserved)
- All MATCH queries use parameterized `?` (no injection risk)
