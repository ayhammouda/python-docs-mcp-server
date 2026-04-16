# Plan A Summary: Query Processing

## Status: Complete

## What was built
- `retrieval/__init__.py` — package init with public re-exports
- `retrieval/query.py` — fts5_escape(), classify_query(), expand_synonyms(), build_match_expression()
- 50+ fuzz test inputs exercised end-to-end against real FTS5 MATCH

## Key decisions
- Null bytes stripped in fts5_escape (FTS5 treats them as string terminators)
- classify_query uses callback injection for symbol_exists_fn (avoids storage import)
- Synonym expansion uses OR-joined terms; plain queries use implicit AND

## Requirements addressed
- RETR-01: fts5_escape wraps every token in double quotes
- RETR-03: 50+ adversarial inputs fuzz tested
- RETR-04: classify_query detects dotted names and module names
- RETR-05: expand_synonyms with multi-word concept support

## Self-Check: PASSED
- All 44 tests pass
- No imports from storage or server in query.py

## key-files
### created
- src/mcp_server_python_docs/retrieval/__init__.py
- src/mcp_server_python_docs/retrieval/query.py
- tests/test_retrieval.py
