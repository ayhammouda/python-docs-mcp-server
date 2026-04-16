---
status: findings
phase: 02-schema-storage
reviewed: 2026-04-15
depth: standard
files_reviewed: 3
findings:
  critical: 0
  warning: 2
  info: 3
  total: 5
---

# Phase 2: Schema & Storage -- Code Review

## Files Reviewed

1. `src/mcp_server_python_docs/storage/schema.sql` (created)
2. `src/mcp_server_python_docs/storage/db.py` (modified -- bootstrap_schema refactor)
3. `tests/test_schema.py` (created)

## Findings

### WR-01: `executescript()` issues implicit COMMIT -- can silently commit partial caller transactions

**File:** `src/mcp_server_python_docs/storage/db.py`, line 129
**Severity:** warning

`conn.executescript(schema_sql)` issues an implicit `COMMIT` before executing the script (this is documented SQLite/Python behavior). If `bootstrap_schema()` is ever called while a caller has an open transaction, that transaction will be silently committed -- potentially committing partial work.

Currently safe because the only caller (`ingest_inventory`) calls `bootstrap_schema(conn)` before any data inserts. However, this is a latent hazard for future callers.

**Recommendation:** Add a docstring warning about the implicit commit behavior so future callers know not to call `bootstrap_schema()` mid-transaction:

```python
# NOTE: executescript() issues an implicit COMMIT before executing.
# Do not call bootstrap_schema() while a transaction is in progress
# unless you intend to commit all pending changes.
conn.executescript(schema_sql)
```

### WR-02: `test_no_hardcoded_cache_path` docstring tracker has fragile triple-quote state machine

**File:** `tests/test_schema.py`, lines 356-368
**Severity:** warning

The docstring boundary tracking counts triple-quote occurrences per line to decide whether code is inside a docstring. This approach has edge cases:

- A line with `"""text""" + """more"""` (triple_count=4) would be treated as "stays outside" but the logic is correct by accident.
- A raw string `r"""..."""` spanning multiple lines where intermediate lines contain `"""` will confuse the tracker.
- f-strings with embedded triple-quotes would also break tracking.

These edge cases are unlikely in the current small codebase but could produce false negatives as the project grows.

**Recommendation:** Consider using Python's `ast` module to reliably identify string literals, or simply grep for `~/.cache` in `.py` files and assert zero matches outside of lines starting with `#` or containing known docstring patterns. Alternatively, accept the limitation and add a comment noting the approximation.

### IN-01: FTS5 DROP without coordinated rebuild leaves indexes empty until caller rebuilds

**File:** `src/mcp_server_python_docs/storage/db.py`, lines 121-128
**Severity:** info

`bootstrap_schema()` drops all three FTS5 virtual tables and recreates them empty. If there is existing data in the canonical tables (sections, symbols, examples), the FTS indexes will be empty after bootstrap until someone runs the `INSERT INTO <fts>(<fts>) VALUES('rebuild')` command. The function does not perform this rebuild itself.

This is documented behavior (the docstring says FTS5 tables are "derived data that can be rebuilt"), and the current caller (`ingest_inventory`) rebuilds `symbols_fts` after populating data. No bug here -- noting for awareness. Future callers that bootstrap an existing database must know to rebuild FTS indexes.

### IN-02: `schema.sql` does not create indexes on frequently queried foreign key columns

**File:** `src/mcp_server_python_docs/storage/schema.sql`
**Severity:** info

Several foreign key columns that will be used in JOINs and WHERE clauses lack explicit indexes:
- `documents.doc_set_id`
- `sections.document_id`
- `symbols.doc_set_id`
- `symbols.document_id`
- `symbols.section_id`
- `examples.section_id`
- `redirects.doc_set_id`

SQLite does not automatically create indexes on FK columns (unlike some other databases). For the expected data volume (tens of thousands of rows), this will work fine without indexes, but query plans for multi-table JOINs in the retrieval layer (Phase 3) may benefit from them.

**Recommendation:** Defer index creation to Phase 3 (Retrieval Layer) when actual query patterns are known. Add indexes based on EXPLAIN QUERY PLAN output rather than speculatively. Not a blocker.

### IN-03: `test_fts5_tokenizer_preserves_identifiers` uses hardcoded rowid assumptions for examples

**File:** `tests/test_schema.py`, line 122
**Severity:** info

The example insert references `section_id=1` as a hardcoded literal rather than capturing the actual inserted section's rowid. This works because SQLite assigns rowid=1 to the first insert in an empty table, but it couples the test to insert ordering. If the test setup changes (e.g., inserting sections in different order), the FK reference could become invalid.

The test currently passes and is correct. Noting as minor test hygiene -- other tests in the same file correctly capture IDs from helper functions.

## Summary

The Phase 2 implementation is solid. The schema correctly implements all STOR requirements. The `bootstrap_schema()` refactor to load from `schema.sql` via `importlib.resources` is clean, and the FTS5 drop-recreate pattern handles tokenizer migration correctly. The test suite covers all success criteria.

No critical issues. Two warnings are both about latent hazards rather than current bugs:
- WR-01 is a documentation improvement to prevent future misuse
- WR-02 is about test robustness in an edge case

Static analysis clean: ruff (0 findings), pyright (0 errors, 0 warnings). All 28 tests pass.
