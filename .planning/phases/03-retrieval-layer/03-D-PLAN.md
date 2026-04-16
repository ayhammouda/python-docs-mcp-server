---
phase: 3
plan: D
title: "Domain Error Routing and MATCH Audit"
wave: 2
depends_on:
  - 03-A
  - 03-B
  - 03-C
files_modified:
  - src/mcp_server_python_docs/server.py
  - tests/test_retrieval.py
requirements:
  - SRVR-08
  - RETR-02
autonomous: true
---

# Plan D: Domain Error Routing and MATCH Audit

<objective>
Wire SRVR-08: domain errors (VersionNotFoundError, SymbolNotFoundError, PageNotFoundError) raised in the retrieval layer surface as `isError: true` content in MCP tool responses, not as protocol errors. Also enforce RETR-02: grep audit verifying every FTS5 MATCH call in the codebase routes through `fts5_escape()`.
</objective>

<must_haves>
- VersionNotFoundError surfaces as isError: true with informative message
- SymbolNotFoundError surfaces as isError: true with informative message
- PageNotFoundError surfaces as isError: true with informative message
- No protocol error for domain errors — always tool-level error
- Every FTS5 MATCH in codebase routes through fts5_escape
</must_haves>

## Tasks

### Task 03-D-01: Wire domain error handling in server.py tool handlers

<read_first>
- src/mcp_server_python_docs/server.py (current search_docs implementation)
- src/mcp_server_python_docs/errors.py (VersionNotFoundError, SymbolNotFoundError, PageNotFoundError)
- src/mcp_server_python_docs/retrieval/query.py (classify_query, fts5_escape, build_match_expression)
- src/mcp_server_python_docs/retrieval/ranker.py (search functions)
</read_first>

<action>
Update `src/mcp_server_python_docs/server.py`:

1. Add import for `ToolError` from `mcp.server.fastmcp` (or `mcp.shared.exceptions` depending on SDK version). Check the actual import path — FastMCP provides this for `isError: true` responses.

2. Import retrieval functions:
   ```python
   from mcp_server_python_docs.retrieval.query import (
       classify_query,
       fts5_escape,
       build_match_expression,
   )
   from mcp_server_python_docs.retrieval.ranker import (
       lookup_symbols_exact,
       search_sections,
       search_symbols,
       search_examples,
   )
   from mcp_server_python_docs.retrieval.budget import apply_budget
   ```

3. Wrap the `search_docs` tool handler with domain error catching:
   ```python
   @mcp.tool(...)
   def search_docs(query, version, kind, max_results, ctx):
       app_ctx = ctx.request_context.lifespan_context
       try:
           # ... retrieval logic using classify_query, ranker functions
       except VersionNotFoundError as e:
           raise ToolError(str(e))
       except SymbolNotFoundError as e:
           raise ToolError(str(e))
       except PageNotFoundError as e:
           raise ToolError(str(e))
   ```

4. Update the search_docs implementation to use the retrieval layer:
   - Use `classify_query()` to decide symbol fast-path vs FTS
   - For symbol fast-path: call `lookup_symbols_exact()`
   - For FTS path: call `build_match_expression()` with synonyms from `app_ctx.synonyms`
   - Route to `search_sections()`, `search_symbols()`, or `search_examples()` based on `kind`
   - For `kind="auto"`: try symbol fast-path first, fall back to section search
   - The Phase 1 inline symbol query is REPLACED by the retrieval layer calls

5. Verify that the `ToolError` import path is correct by checking the `mcp` package. The standard path in mcp SDK 1.27.0 is:
   - `from mcp.server.fastmcp import ToolError` — if available
   - Or re-raise as `McpError` from `mcp.types`
   - If neither exists, use the pattern of returning error content directly in the result model

Note: If `ToolError` is not available in the SDK, use the alternative pattern:
```python
except VersionNotFoundError as e:
    return SearchDocsResult(
        hits=[],
        note=str(e),
    )
```
And mark `isError` via the MCP content mechanism. Research the actual `mcp` SDK 1.27.0 API during execution.
</action>

<acceptance_criteria>
- `src/mcp_server_python_docs/server.py` contains `from mcp_server_python_docs.retrieval` imports
- `src/mcp_server_python_docs/server.py` catches `VersionNotFoundError`
- `src/mcp_server_python_docs/server.py` catches `SymbolNotFoundError`
- `src/mcp_server_python_docs/server.py` catches `PageNotFoundError`
- Domain errors produce `isError: true` in the MCP response (not JSON-RPC protocol errors)
- `src/mcp_server_python_docs/server.py` calls `classify_query` from retrieval layer
- `src/mcp_server_python_docs/server.py` calls `fts5_escape` or `build_match_expression` for FTS queries
- The old inline symbol query in search_docs is replaced by retrieval layer calls
</acceptance_criteria>

### Task 03-D-02: Domain error routing tests

<read_first>
- src/mcp_server_python_docs/server.py (updated error handling)
- src/mcp_server_python_docs/errors.py (error classes)
- tests/test_retrieval.py (add to existing)
</read_first>

<action>
Add tests to `tests/test_retrieval.py`:

1. **`test_version_not_found_error_message`**: Create a `VersionNotFoundError("version 3.99 not found; available: [3.12, 3.13]")`. Assert the error message contains "3.99" and "available".

2. **`test_symbol_not_found_error_message`**: Create a `SymbolNotFoundError`. Assert it's a subclass of `DocsServerError`.

3. **`test_page_not_found_error_message`**: Create a `PageNotFoundError`. Assert it's a subclass of `DocsServerError`.

4. **`test_domain_errors_are_not_protocol_errors`**: Assert that `VersionNotFoundError`, `SymbolNotFoundError`, `PageNotFoundError` are all subclasses of `DocsServerError` but NOT subclasses of any MCP protocol error type.

5. **`test_error_hierarchy`**: Verify the full error hierarchy:
   - `DocsServerError` is base
   - `VersionNotFoundError(DocsServerError)`
   - `SymbolNotFoundError(DocsServerError)`
   - `PageNotFoundError(DocsServerError)`
   - All are catchable via `except DocsServerError`
</action>

<acceptance_criteria>
- `tests/test_retrieval.py` contains `test_version_not_found_error_message`
- `tests/test_retrieval.py` contains `test_domain_errors_are_not_protocol_errors`
- `uv run pytest tests/test_retrieval.py -x -q -k "error" 2>&1` exits 0
</acceptance_criteria>

### Task 03-D-03: RETR-02 MATCH audit

<read_first>
- src/mcp_server_python_docs/retrieval/ranker.py (MATCH queries)
- src/mcp_server_python_docs/server.py (any remaining MATCH queries)
</read_first>

<action>
1. Run `rg 'MATCH' src/mcp_server_python_docs/ --type py` to find all FTS5 MATCH usage sites.

2. Verify every MATCH call receives its query parameter from:
   - `fts5_escape()` — direct call
   - `build_match_expression()` — which internally uses `fts5_escape()`
   - A test/fixture context (not user-facing)

3. If any MATCH call in production code takes raw user input without going through fts5_escape, fix it.

4. Add a test `test_no_raw_match_in_source` to `tests/test_retrieval.py`:
   ```python
   import subprocess
   def test_no_raw_match_in_source():
       """RETR-02: Every MATCH query routes through fts5_escape."""
       result = subprocess.run(
           ["rg", "MATCH", "src/mcp_server_python_docs/", "--type", "py", "-n"],
           capture_output=True, text=True
       )
       # All MATCH occurrences should be in ranker.py (parameterized queries)
       # and none should concatenate user input directly
       for line in result.stdout.strip().split("\n"):
           if not line:
               continue
           # ranker.py uses parameterized ? for MATCH values — safe
           # Any file doing string formatting with MATCH is a violation
           assert "f'" not in line or "MATCH" not in line, (
               f"Possible raw MATCH concatenation: {line}"
           )
   ```

5. If `rg` is not available in test environment, use `pathlib` + `re` to scan source files instead.
</action>

<acceptance_criteria>
- `rg 'MATCH' src/mcp_server_python_docs/ --type py` shows MATCH only in ranker.py (parameterized queries)
- No string concatenation or f-string builds raw MATCH queries from user input
- `tests/test_retrieval.py` contains `test_no_raw_match_in_source`
- `uv run pytest tests/test_retrieval.py::test_no_raw_match_in_source -x` exits 0
</acceptance_criteria>

<verification>
```bash
uv run pytest tests/test_retrieval.py -x -q 2>&1
rg 'MATCH' src/mcp_server_python_docs/ --type py -n 2>&1
```
</verification>
