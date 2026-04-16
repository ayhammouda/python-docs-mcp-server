# Plan D Summary: Domain Error Routing and MATCH Audit

## Status: Complete

## What was built
- server.py updated to use retrieval layer (classify_query, build_match_expression, ranker functions)
- Domain error routing: VersionNotFoundError, SymbolNotFoundError, PageNotFoundError -> ToolError -> isError: true
- MATCH audit: all FTS5 MATCH calls use parameterized ? in ranker.py; no raw concatenation
- _do_search() helper routes kind="auto" through classifier -> symbol fast-path or FTS5

## Key decisions
- ToolError imported from mcp.server.fastmcp.exceptions (SDK 1.27.0 path)
- DocsServerError catch-all after specific error types for safety
- kind="auto" tries symbol fast-path first, falls back to sections FTS, then symbols FTS
- kind="page" currently routes to sections search (full page search deferred to Phase 4 content ingestion)

## Requirements addressed
- SRVR-08: Domain errors surface as isError: true via ToolError
- RETR-02: Grep audit shows all MATCH calls use parameterized queries

## Self-Check: PASSED
- 72 tests pass (all prior tests + new retrieval tests)
- rg 'MATCH' src/ shows only parameterized MATCH ? in ranker.py

## key-files
### modified
- src/mcp_server_python_docs/server.py
