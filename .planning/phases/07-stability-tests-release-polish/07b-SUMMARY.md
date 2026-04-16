# Plan 07b: Stdio Smoke Test & Test Pyramid Verification - Summary

**Status:** Complete
**Tests added:** 4 smoke tests
**Total test suite:** 204 tests, all passing

## What Was Built

4 stdio smoke tests in `tests/test_stdio_smoke.py` that spawn a real MCP server subprocess, send JSON-RPC messages, and verify protocol compliance:

1. `test_server_lists_tools_no_stdout_pollution` -- initialize + tools/list, verifies search_docs/get_docs/list_versions present, no nextCursor, no stdout pollution
2. `test_search_docs_round_trip` -- full tools/call round-trip with search_docs
3. `test_list_versions_round_trip` -- full tools/call round-trip with list_versions
4. `test_all_stdout_is_valid_jsonrpc` -- parses every stdout line, asserts valid JSON-RPC 2.0

Each test creates a minimal index.db in a temp dir for the server to start with.

### Test Pyramid Verified
- Unit: test_retrieval.py (fts5_escape fuzz, budget, synonyms, symbol classifier)
- Storage: test_schema.py (idempotency, WAL, FTS5 check, queries)
- Ingestion: test_ingestion.py, test_publish.py (objects.inv, sphinx json, atomic swap)
- Services: test_services.py (tool shapes, caching)
- Stability: test_stability.py (20 structural tests)
- Smoke: test_stdio_smoke.py (4 subprocess tests)

## Files Modified

- `tests/test_stdio_smoke.py` -- 4 new tests (new file)

## Requirements Coverage

| Requirement | Status |
|-------------|--------|
| TEST-02 | Verified (unit tests green) |
| TEST-03 | Verified (storage tests green) |
| TEST-04 | Verified (ingestion tests green) |
| TEST-05 | Done (stdio smoke tests) |

## Self-Check: PASSED
