---
status: passed
phase: 03
verified: 2026-04-16
---

# Phase 3: Retrieval Layer - Verification

## Phase Goal
A pure-logic retrieval module that never crashes SQLite on adversarial FTS input, expands synonym queries, classifies symbol-shaped queries to fast-path before FTS, ranks via BM25 with column weights + snippet() excerpts, and enforces Unicode-safe budget truncation -- with all known domain errors surfaced as isError: true content rather than protocol errors.

## Requirements Coverage

| Requirement | Status | Evidence |
|-------------|--------|----------|
| RETR-01 | PASSED | `fts5_escape()` in query.py wraps every token in double quotes, strips null bytes |
| RETR-02 | PASSED | All MATCH calls in ranker.py use parameterized `MATCH ?`; test_no_raw_match_in_source verifies no f-string/format concatenation |
| RETR-03 | PASSED | 75 fuzz inputs (exceeds 50+ requirement) tested end-to-end against real FTS5 MATCH; zero OperationalError |
| RETR-04 | PASSED | `classify_query()` detects dotted names and single-word modules via callback; tested with positive and negative cases |
| RETR-05 | PASSED | `expand_synonyms()` and `build_match_expression()` produce OR-joined expansions from synonym dict |
| RETR-06 | PASSED | `bm25(sections_fts, 10.0, 1.0)` and `bm25(symbols_fts, 10.0, 1.0)` in ranker.py; heading/qualified_name weighted 10x |
| RETR-07 | PASSED | `snippet(sections_fts, 1, '**', '**', '...', 32)` produces ~200-char excerpts; test_snippet_present_on_section_hits verifies |
| RETR-08 | PASSED | `apply_budget()` in budget.py; Unicode-safe (combining marks, emoji, CJK); 13 test cases including pagination |
| RETR-09 | PASSED | All search functions return `list[SymbolHit]`; test_hit_shape_consistency verifies identical schema across paths |
| SRVR-08 | PASSED | VersionNotFoundError/SymbolNotFoundError/PageNotFoundError caught in server.py, raised as ToolError for isError: true |

## Must-Haves Verification

| Must-Have | Status |
|-----------|--------|
| fts5_escape never raises sqlite3.OperationalError | PASSED (75 fuzz inputs) |
| Grep audit shows zero raw MATCH concatenation | PASSED (test_no_raw_match_in_source) |
| Symbol classifier detects dotted names | PASSED |
| BM25 heading > content_text weighting | PASSED (10.0 vs 1.0) |
| FTS5 snippet() on every hit | PASSED |
| apply_budget never splits codepoint/combining mark | PASSED (emoji + combining char tests) |
| Domain errors -> isError: true | PASSED (ToolError routing) |

## Test Results

```
72 passed in 0.95s
```

- 44 retrieval-specific tests (fuzz, classifier, synonyms, ranker, budget, errors, MATCH audit)
- 28 prior tests (schema, snapshot, stdio, synonyms, Phase 1 integration) -- no regressions

## Files Created/Modified

### Created
- `src/mcp_server_python_docs/retrieval/__init__.py`
- `src/mcp_server_python_docs/retrieval/query.py`
- `src/mcp_server_python_docs/retrieval/ranker.py`
- `src/mcp_server_python_docs/retrieval/budget.py`
- `tests/test_retrieval.py`

### Modified
- `src/mcp_server_python_docs/server.py` (rewired to use retrieval layer)

## Deviations

- FTS5 tokenizer (`tokenchars '._'`) means "asyncio.TaskGroup" is a single token. Tests adjusted to search for full tokens rather than partial names.
- Null bytes (\x00) crash FTS5 with "unterminated string" -- fts5_escape strips them before quoting.
- Score normalization: single-hit results get score 1.0 (not raw negative BM25).
