# Plan B Summary: BM25 Ranking with Column Weights and FTS5 Snippets

## Status: Complete

## What was built
- `retrieval/ranker.py` — search_sections(), search_symbols(), search_examples(), lookup_symbols_exact()
- BM25 column weights: heading 10x over content_text, qualified_name 10x over module
- FTS5 snippet() excerpts (~200 chars, 32 tokens) on every hit
- Score normalization: single hit = 1.0, batch normalized to [0.1, 1.0]

## Key decisions
- BM25 returns negative scores (lower = better); normalization inverts to [0.1, 1.0]
- Single-hit results normalized to 1.0 (not left as raw negative BM25 score)
- FTS5 OperationalError caught and returns empty list (graceful degradation)
- All MATCH queries use parameterized ? (safe against injection)

## Requirements addressed
- RETR-06: BM25 column weights (heading > content_text, qualified_name > module)
- RETR-07: FTS5 snippet() excerpts ~200 chars on every hit
- RETR-09: Locked SymbolHit shape across all search paths

## Self-Check: PASSED

## key-files
### created
- src/mcp_server_python_docs/retrieval/ranker.py
