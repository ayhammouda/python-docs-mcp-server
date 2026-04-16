---
phase: 7
plan: b
title: "Stdio Smoke Test & Test Pyramid Verification"
wave: 1
depends_on: []
files_modified:
  - tests/test_stdio_smoke.py
requirements:
  - TEST-02
  - TEST-03
  - TEST-04
  - TEST-05
autonomous: true
---

# Plan 07b: Stdio Smoke Test & Test Pyramid Verification

<objective>
Create a comprehensive stdio smoke test that spawns the server as a subprocess, lists tools, issues one round-trip per tool, and verifies zero stdout pollution. Also verify the full test pyramid (unit, storage, ingestion, tool tests) is green.
</objective>

## Tasks

<task id="1">
<title>Create stdio smoke test with full round-trip verification</title>

<read_first>
- tests/test_stdio_hygiene.py
- src/mcp_server_python_docs/__main__.py
- src/mcp_server_python_docs/server.py
- src/mcp_server_python_docs/models.py
</read_first>

<action>
Create `tests/test_stdio_smoke.py` that spawns the MCP server as a subprocess and verifies MCP protocol compliance. The test file should:

1. **Import requirements:** `subprocess`, `sys`, `json`, `tempfile`, `os`, `time`, `pytest`

2. **Helper function `send_mcp_request(process, method, params=None, req_id=1)`:**
   - Builds a JSON-RPC 2.0 request: `{"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}`
   - Writes the JSON followed by `\n` to process.stdin
   - Reads lines from process.stdout until a complete JSON-RPC response with matching `id` is found
   - Returns the parsed response dict
   - Has a timeout mechanism (5 seconds max per response)

3. **Helper function `start_server(tmp_dir, db_path=None)`:**
   - Spawns `sys.executable -m mcp_server_python_docs serve` as a subprocess with `stdin=subprocess.PIPE`, `stdout=subprocess.PIPE`, `stderr=subprocess.PIPE`
   - Sets `HOME` and `XDG_CACHE_HOME` env vars to tmp_dir
   - If db_path is provided, copies it or symlinks it into the expected cache location
   - Returns the process

4. **Test class `TestStdioSmoke`:**

   a. `test_server_lists_tools_no_stdout_pollution`:
      - Create a minimal test database (bootstrapped schema + doc_set + a few symbols) at the expected cache path
      - Start the server subprocess
      - Send `initialize` request (with `{"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test"}}`)
      - Send `notifications/initialized` notification
      - Send `tools/list` request
      - Assert the response contains tool names: `search_docs`, `get_docs`, `list_versions`
      - Assert `nextCursor` is NOT in the response (HYGN-06)
      - Kill the process
      - Assert process.stdout contained only valid JSON-RPC lines (no stray text)
      - Assert no non-JSON bytes appeared on stdout

   b. `test_search_docs_round_trip`:
      - Start server with a populated test DB (same as above)
      - Initialize + initialized notification
      - Send `tools/call` with `{"name": "search_docs", "arguments": {"query": "asyncio.TaskGroup"}}`
      - Assert response has `result` with `content` field (list of content items)
      - Assert no stdout pollution outside JSON-RPC
      - Kill process

   c. `test_get_docs_round_trip`:
      - Start server with populated test DB that has sections
      - Send `tools/call` with `{"name": "get_docs", "arguments": {"slug": "library/asyncio-task.html"}}`
      - Assert response has `result` with content
      - No stdout pollution

   d. `test_list_versions_round_trip`:
      - Start server with populated test DB
      - Send `tools/call` with `{"name": "list_versions", "arguments": {}}`
      - Assert response has `result` with content mentioning version info
      - No stdout pollution

   e. `test_all_stdout_is_valid_jsonrpc`:
      - Start server, send initialize + list tools + one search call
      - Capture ALL stdout output
      - Split by newlines, parse each non-empty line as JSON
      - Assert every line is valid JSON with `"jsonrpc": "2.0"` field
      - This is the definitive zero-pollution check

**Important implementation notes:**
- The MCP protocol over stdio uses newline-delimited JSON-RPC. Each message is a single JSON object on one line.
- Use `process.stdin.write(json.dumps(msg).encode() + b"\n")` and `process.stdin.flush()`
- Read with a timeout to avoid hanging on protocol errors
- Clean up subprocesses in finally blocks
- Mark tests with `@pytest.mark.timeout(30)` or use internal timeouts
- If the server cannot be started with a test DB (e.g., missing index triggers exit), the test should create the index in the temp dir first using bootstrap_schema + symbol insertion
</action>

<acceptance_criteria>
- `tests/test_stdio_smoke.py` exists with at least 4 test functions
- Each test spawns a real subprocess (not mocked)
- Each test verifies `stdout` contains only valid JSON-RPC messages
- `test_server_lists_tools_no_stdout_pollution` asserts `search_docs`, `get_docs`, `list_versions` are listed
- `test_all_stdout_is_valid_jsonrpc` parses every stdout line as JSON and checks `"jsonrpc": "2.0"`
- All smoke tests pass: `pytest tests/test_stdio_smoke.py -v`
</acceptance_criteria>
</task>

<task id="2">
<title>Verify full test pyramid is green</title>

<read_first>
- tests/test_retrieval.py
- tests/test_schema.py
- tests/test_ingestion.py
- tests/test_services.py
- tests/test_publish.py
- tests/test_multi_version.py
- tests/test_packaging.py
- tests/test_schema_snapshot.py
- tests/test_synonyms.py
- tests/test_phase1_integration.py
</read_first>

<action>
Run the full test suite and verify all existing test categories are green. The test pyramid should include:

**Unit tests (TEST-02):** `test_retrieval.py` covers fts5_escape fuzz (50+ inputs), budget truncation (Unicode edge cases), synonym expansion, symbol classification.

**Storage tests (TEST-03):** `test_schema.py` covers schema bootstrap idempotency, WAL mode, FTS5 check, repository queries.

**Ingestion tests (TEST-04):** `test_ingestion.py` covers objects.inv fixture, Sphinx JSON fixture, `test_publish.py` covers atomic swap.

**Tool/service tests:** `test_services.py` covers tool response shapes, `test_schema_snapshot.py` covers schema drift guard.

If any test failures are found, fix them. The goal is a fully green suite including the new stability and smoke tests.

Run:
```bash
uv run pytest --tb=short -q
```

Expected: 172 existing + ~20 stability + ~5 smoke = ~197 tests, all passing.
</action>

<acceptance_criteria>
- `uv run pytest --tb=short -q` exits with code 0
- Total test count is at least 190 tests
- No test file has any failures or errors
- Test categories present: test_retrieval, test_schema, test_ingestion, test_publish, test_services, test_stability, test_stdio_smoke
</acceptance_criteria>
</task>

## Verification

```bash
# Run full test suite
uv run pytest --tb=short -q 2>&1

# Count tests per file
uv run pytest --co -q 2>&1 | grep '::' | cut -d: -f1 | sort | uniq -c | sort -rn

# Verify smoke tests specifically
uv run pytest tests/test_stdio_smoke.py -v 2>&1

# Verify zero stdout pollution in smoke test
uv run pytest tests/test_stdio_smoke.py::TestStdioSmoke::test_all_stdout_is_valid_jsonrpc -v 2>&1
```

<must_haves>
- Stdio smoke test spawns a real subprocess and verifies JSON-RPC purity on stdout
- At least one test does a full tool round-trip (initialize -> tools/list -> tools/call)
- Full test pyramid passes with zero failures
- Total test count >= 190
</must_haves>
