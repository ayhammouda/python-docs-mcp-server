---
phase: 3
plan: B
title: "BM25 Ranking with Column Weights and FTS5 Snippets"
wave: 1
depends_on: []
files_modified:
  - src/mcp_server_python_docs/retrieval/ranker.py
  - tests/test_retrieval.py
requirements:
  - RETR-06
  - RETR-07
  - RETR-09
autonomous: true
---

# Plan B: BM25 Ranking with Column Weights and FTS5 Snippets

<objective>
Create `retrieval/ranker.py` with search functions that use BM25 column weights (heading > content_text, qualified_name > module) and FTS5 `snippet()` for ~200-char excerpts. All search paths produce the locked `SymbolHit` shape (RETR-09) — identical schema whether hit came from symbol fast-path or FTS5.
</objective>

<must_haves>
- BM25 column weights: heading weighted 10x over content_text for sections
- BM25 column weights: qualified_name weighted 10x over module for symbols
- Every hit carries a ~200-char FTS5 snippet() excerpt
- All functions return list[SymbolHit] with the locked hit shape
- Score normalization: symbol exact match = 1.0, FTS5 = normalized [0.1, 1.0]
</must_haves>

## Tasks

### Task 03-B-01: Create ranker.py with BM25 + snippet search functions

<read_first>
- src/mcp_server_python_docs/storage/schema.sql (FTS5 table definitions, column order)
- src/mcp_server_python_docs/models.py (SymbolHit shape)
- src/mcp_server_python_docs/retrieval/query.py (fts5_escape, build_match_expression)
- python-docs-mcp-server-build-guide.md lines 134-141 (§5 token efficiency)
</read_first>

<action>
Create `src/mcp_server_python_docs/retrieval/ranker.py` with:

Module-level imports: `from __future__ import annotations`, `logging`, `sqlite3`. Import `SymbolHit` from `mcp_server_python_docs.models`.

**`search_sections(conn: sqlite3.Connection, match_expr: str, version: str | None, max_results: int) -> list[SymbolHit]`**:
- Execute FTS5 query against `sections_fts` with BM25 column weights:
  ```sql
  SELECT s.id, s.heading, s.uri, s.anchor,
         d.version, doc.slug,
         bm25(sections_fts, 10.0, 1.0) as score,
         snippet(sections_fts, 1, '**', '**', '...', 32) as snippet_text
  FROM sections_fts
  JOIN sections s ON sections_fts.rowid = s.id
  JOIN documents doc ON s.document_id = doc.id
  JOIN doc_sets d ON doc.doc_set_id = d.id
  WHERE sections_fts MATCH ?
    AND (? IS NULL OR d.version = ?)
  ORDER BY bm25(sections_fts, 10.0, 1.0)
  LIMIT ?
  ```
- BM25 column weights: `10.0` for heading (column 0), `1.0` for content_text (column 1)
- `snippet()` extracts from content_text (column 1), with `**` match markers, `...` trailing, 32 tokens (~200 chars)
- Map each row to `SymbolHit` with:
  - `uri` = section URI
  - `title` = heading
  - `kind` = `"section"`
  - `snippet` = snippet_text from FTS5
  - `score` = normalized BM25 score
  - `version` = doc_set version
  - `slug` = document slug
  - `anchor` = section anchor
- Normalize scores: best BM25 score in batch = 1.0, worst = 0.1, linear interpolation

**`search_symbols(conn: sqlite3.Connection, match_expr: str, version: str | None, max_results: int) -> list[SymbolHit]`**:
- Execute FTS5 query against `symbols_fts` with BM25 column weights:
  ```sql
  SELECT sym.id, sym.qualified_name, sym.symbol_type, sym.uri, sym.anchor,
         sym.module, d.version,
         bm25(symbols_fts, 10.0, 1.0) as score,
         snippet(symbols_fts, 0, '**', '**', '...', 32) as snippet_text
  FROM symbols_fts
  JOIN symbols sym ON symbols_fts.rowid = sym.id
  JOIN doc_sets d ON sym.doc_set_id = d.id
  WHERE symbols_fts MATCH ?
    AND (? IS NULL OR d.version = ?)
  ORDER BY bm25(symbols_fts, 10.0, 1.0)
  LIMIT ?
  ```
- BM25 column weights: `10.0` for qualified_name (column 0), `1.0` for module (column 1)
- `snippet()` extracts from qualified_name (column 0)
- Map each row to `SymbolHit` with:
  - `uri` = symbol URI
  - `title` = qualified_name
  - `kind` = symbol_type (class, function, method, etc.)
  - `snippet` = snippet_text
  - `score` = normalized BM25 score
  - `version` = doc_set version
  - `slug` = URI split on `#` to get page slug
  - `anchor` = symbol anchor

**`search_examples(conn: sqlite3.Connection, match_expr: str, version: str | None, max_results: int) -> list[SymbolHit]`**:
- Execute FTS5 query against `examples_fts`:
  ```sql
  SELECT e.id, e.code, e.is_doctest,
         s.heading, s.uri as section_uri, s.anchor,
         d.version, doc.slug,
         bm25(examples_fts) as score,
         snippet(examples_fts, 0, '**', '**', '...', 32) as snippet_text
  FROM examples_fts
  JOIN examples e ON examples_fts.rowid = e.id
  JOIN sections s ON e.section_id = s.id
  JOIN documents doc ON s.document_id = doc.id
  JOIN doc_sets d ON doc.doc_set_id = d.id
  WHERE examples_fts MATCH ?
    AND (? IS NULL OR d.version = ?)
  ORDER BY bm25(examples_fts)
  LIMIT ?
  ```
- Map to `SymbolHit` with `kind="example"` (or `"doctest"` if `is_doctest=1`)
- `title` = parent section heading
- `snippet` = code snippet from FTS5

**`lookup_symbols_exact(conn: sqlite3.Connection, query: str, version: str | None, max_results: int) -> list[SymbolHit]`**:
- Direct symbol table query (no FTS5) for the symbol fast-path:
  ```sql
  SELECT s.qualified_name, s.symbol_type, s.uri, s.anchor, s.module, d.version
  FROM symbols s
  JOIN doc_sets d ON s.doc_set_id = d.id
  WHERE (s.qualified_name = ? OR s.qualified_name LIKE ? ESCAPE '\')
    AND (? IS NULL OR d.version = ?)
  ORDER BY CASE WHEN s.qualified_name = ? THEN 0 ELSE 1 END
  LIMIT ?
  ```
- Escape LIKE wildcards in query: `%` -> `\%`, `_` -> `\_`
- Map to `SymbolHit` with:
  - `score` = 1.0 for exact match, 0.8 for prefix match
  - `snippet` = `""` (no FTS5 snippet for direct lookup)
  - `slug` = URI split on `#`
  - `kind` = symbol_type or `"symbol"`

**Helper `_normalize_scores(hits: list[SymbolHit]) -> list[SymbolHit]`**:
- If empty or single hit, return as-is
- Find min and max raw scores in batch
- Normalize to [0.1, 1.0] range: `0.1 + 0.9 * (raw - min) / (max - min)`
- Return new list with updated scores
</action>

<acceptance_criteria>
- `src/mcp_server_python_docs/retrieval/ranker.py` contains `def search_sections(`
- `src/mcp_server_python_docs/retrieval/ranker.py` contains `def search_symbols(`
- `src/mcp_server_python_docs/retrieval/ranker.py` contains `def search_examples(`
- `src/mcp_server_python_docs/retrieval/ranker.py` contains `def lookup_symbols_exact(`
- `src/mcp_server_python_docs/retrieval/ranker.py` contains `bm25(sections_fts, 10.0, 1.0)`
- `src/mcp_server_python_docs/retrieval/ranker.py` contains `snippet(sections_fts`
- `src/mcp_server_python_docs/retrieval/ranker.py` contains `bm25(symbols_fts, 10.0, 1.0)`
- Every function returns `list[SymbolHit]`
- No imports from `mcp_server_python_docs.server` in ranker.py
</acceptance_criteria>

### Task 03-B-02: Tests for BM25 ranking and snippet excerpts

<read_first>
- src/mcp_server_python_docs/retrieval/ranker.py (just created)
- src/mcp_server_python_docs/storage/schema.sql (table DDL for fixture setup)
- tests/test_retrieval.py (add to existing test file)
</read_first>

<action>
Add tests to `tests/test_retrieval.py`:

1. **Fixture `fts_db`**: Create an in-memory SQLite DB with full schema (doc_sets, documents, sections, symbols, examples, plus all FTS5 tables). Insert test data:
   - One doc_set (version="3.13")
   - One document (slug="library/asyncio-task.html")
   - Two sections: one with heading "asyncio.TaskGroup" (high relevance), one with heading "Introduction" containing "TaskGroup" in content_text (lower relevance)
   - Two symbols: "asyncio.TaskGroup" (class) and "asyncio.run" (function)
   - One example: code containing "async with TaskGroup"
   - Rebuild all FTS tables: `INSERT INTO sections_fts(sections_fts) VALUES('rebuild')`, same for symbols_fts and examples_fts

2. **`test_bm25_heading_over_content`**: Search sections for "TaskGroup". Assert the heading match ranks higher (lower BM25 = better) than the content_text-only match. Verify `hits[0].title == "asyncio.TaskGroup"`.

3. **`test_bm25_qualified_name_over_module`**: Search symbols for "asyncio". Assert qualified_name match on "asyncio.TaskGroup" and "asyncio.run" are returned with scores.

4. **`test_snippet_present_on_section_hits`**: Search sections. Assert every hit has a non-empty `snippet` field. Assert snippet length is approximately <=200 chars.

5. **`test_hit_shape_consistency`**: Run both `lookup_symbols_exact` and `search_sections`. Assert both return `SymbolHit` instances with all fields populated (uri, title, kind, snippet, score, version, slug, anchor). Score is float in [0, 1.0] range.

6. **`test_lookup_symbols_exact_match_score`**: Exact match on "asyncio.TaskGroup" returns score=1.0. Prefix match on "asyncio" returns score=0.8.
</action>

<acceptance_criteria>
- `tests/test_retrieval.py` contains `test_bm25_heading_over_content`
- `tests/test_retrieval.py` contains `test_snippet_present_on_section_hits`
- `tests/test_retrieval.py` contains `test_hit_shape_consistency`
- `uv run pytest tests/test_retrieval.py::test_bm25_heading_over_content -x` exits 0
- `uv run pytest tests/test_retrieval.py::test_snippet_present_on_section_hits -x` exits 0
- `uv run pytest tests/test_retrieval.py::test_hit_shape_consistency -x` exits 0
</acceptance_criteria>

<verification>
```bash
uv run pytest tests/test_retrieval.py -x -q -k "bm25 or snippet or hit_shape" 2>&1
```
</verification>
