---
status: passed
phase: 02-schema-storage
verified: 2026-04-16
---

# Phase 2: Schema & Storage -- Verification

## Phase Goal

A locked-in SQLite schema that preserves Python identifier search, eliminates cross-version URI collisions, resolves cache paths via `platformdirs`, and can be bootstrapped idempotently -- verified before any content ingestion so the tokenizer choice never triggers a full rebuild later.

## Success Criteria Verification

### 1. FTS5 tokenizer regression fixture -- PASSED

`test_fts5_tokenizer_preserves_identifiers` indexes `asyncio.TaskGroup`, `json.dumps`, and `collections.OrderedDict` into `sections_fts`, `symbols_fts`, and `examples_fts`, then retrieves each via exact-token `MATCH` search. Also verifies Porter stemming is NOT active by asserting `"dump"` does not match `json.dumps` heading.

**Evidence:** `uv run python -m pytest tests/test_schema.py::test_fts5_tokenizer_preserves_identifiers` exits 0.

### 2. Composite symbol uniqueness -- PASSED

`test_symbol_composite_uniqueness` inserts `json.dumps` as both `function` and `method` into `symbols` under the same `doc_set_id`. Both inserts succeed. A third insert with the same `(doc_set_id, qualified_name, symbol_type)` triple raises `IntegrityError`.

**Evidence:** `uv run python -m pytest tests/test_schema.py::test_symbol_composite_uniqueness` exits 0.

### 3. Cross-version URI collision fixture -- PASSED

`test_cross_version_uri_no_collision` creates doc_sets for 3.12 and 3.13, inserts sections with identical URI `library/json.html#json.dumps` for both versions. Both inserts succeed because `sections.uri` has no standalone `UNIQUE` constraint. `UNIQUE(document_id, anchor)` is still enforced (duplicate within same document fails).

**Evidence:** `uv run python -m pytest tests/test_schema.py::test_cross_version_uri_no_collision` exits 0.

### 4. Idempotent bootstrap -- PASSED

`test_bootstrap_idempotent` calls `bootstrap_schema(conn)` twice on the same in-memory database. Data inserted between calls survives. `doc_sets.language` defaults to `'en'` when omitted.

**Evidence:** `uv run python -m pytest tests/test_schema.py::test_bootstrap_idempotent` exits 0.

### 5. platformdirs everywhere -- PASSED

`test_no_hardcoded_cache_path` scans all `.py` files under `src/` for `~/.cache` in executable code (excluding comments and docstrings). Zero violations found. All cache paths flow through `platformdirs.user_cache_dir()`.

**Evidence:** `uv run python -m pytest tests/test_schema.py::test_no_hardcoded_cache_path` exits 0. `rg '~/.cache' src/` returns only a docstring reference at `db.py:20`.

## Requirements Coverage

| REQ-ID | Description | Status |
|--------|-------------|--------|
| STOR-01 | schema.sql defines all 8 tables per build guide §7 | PASSED |
| STOR-02 | FTS5 tokenizer is unicode61 without Porter stemming | PASSED |
| STOR-03 | symbols UNIQUE(doc_set_id, qualified_name, symbol_type) | PASSED |
| STOR-04 | sections drops UNIQUE(uri), keeps UNIQUE(document_id, anchor) | PASSED |
| STOR-05 | doc_sets.language defaults to 'en' | PASSED |
| STOR-09 | Schema bootstrap is idempotent | PASSED |

## Regression Check

Full test suite: 28/28 pass. No regressions introduced.

## must_haves

- [x] Complete DDL with corrected FTS5 tokenizer (B1 blocker resolution)
- [x] Composite symbol uniqueness constraint (STOR-03)
- [x] Cross-version URI safety (STOR-04)
- [x] doc_sets.language with DEFAULT 'en' (STOR-05)
- [x] Idempotent bootstrap via schema.sql (STOR-09)
- [x] All 5 success criteria have passing tests
