---
phase: 09-compare-versions
plan: 04
subsystem: server
tags: [compare-versions, mcp-tool, fastmcp, dependency-injection, tool-registration, cross-ai-review]

# Dependency graph
requires:
  - "09-02 (CompareVersionsResult + ChangeKind model contract)"
  - "09-03 (CompareService.compare(symbol, v1, v2) -> CompareVersionsResult)"
provides:
  - "AppContext.compare_service: CompareService field (required, lifespan-populated)"
  - "compare_versions FastMCP tool registered over stdio (6th tool)"
  - "SymbolParam / CompareVersionParam parameter aliases"
  - "tests/test_services.py TestToolRegistration updated to the 6-tool surface (H1 closed)"
affects:
  - ".github/INTEGRATION-TEST.md + README (Plan 05 documents the new tool end-to-end)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Thin server layer: tool body delegates to app_ctx.compare_service.compare with DocsServerError->ToolError(str(e)) + generic Internal error mapping (matches search_docs)"
    - "Sync @mcp.tool (no asyncio.to_thread) — compare_versions is pure SQLite reads, matching search_docs/get_docs (not lookup_package_docs which is async network I/O)"
    - "_TOOL_ANNOTATIONS (closed-world, readOnly) reused — NOT _PYPI_TOOL_ANNOTATIONS"
    - "Required (no-default) dataclass field placed among the other no-default service fields, before the first defaulted field"

key-files:
  created:
    - .planning/phases/09-compare-versions/09-04-mcp-tool-wiring-SUMMARY.md
  modified:
    - src/mcp_server_python_docs/app_context.py
    - src/mcp_server_python_docs/server.py
    - tests/test_services.py
    - tests/test_retrieval_regression.py

key-decisions:
  - "H1 closed: test_five_tools_registered renamed to test_six_tools_registered (assert len(tools) == 6); annotation loop + schema assertions extended to compare_versions"
  - "compare_versions uses _TOOL_ANNOTATIONS (closed-world) — it reads only the local index, never the network"
  - "Tool is SYNC (def, not async def) — pure SQLite reads, no to_thread wrapper"
  - "Docstring uses signature_delta (Plan 02 M1 rename), never the deprecated signature_change"

requirements-completed: [CMPR-01, CMPR-02]

# Metrics
duration: ~7min
completed: 2026-05-28
tasks: 6
files_changed: 4
---

# Phase 09 Plan 04: mcp-tool-wiring Summary

Wired `CompareService` into the FastMCP server surface: added the required
`compare_service` field to `AppContext`, constructed it in `app_lifespan`,
registered the sync `compare_versions` `@mcp.tool` block (with two new parameter
aliases), and updated `tests/test_services.py` to the 6-tool surface — closing
cross-AI review finding H1 (the hardcoded 5-tool assertion). `compare_versions`
is now enumerable and callable over stdio alongside the five existing tools.

## What Was Built

- **`src/mcp_server_python_docs/app_context.py`** — one new import
  (`CompareService`) and one new required field `compare_service: CompareService`
  placed after `content_service` and before `version_service` (matches lifespan
  construction order; sits among the no-default fields).
- **`src/mcp_server_python_docs/server.py`** — `CompareVersionsResult` import,
  `CompareService` import, `compare_svc = CompareService(db, content_svc)` in
  `app_lifespan`, `compare_service=compare_svc` in the `AppContext(...)` yield,
  two new parameter aliases (`SymbolParam` max_length=200/min_length=1,
  `CompareVersionParam`), and a sync `@mcp.tool(annotations=_TOOL_ANNOTATIONS)`
  `compare_versions` block delegating to `app_ctx.compare_service.compare` with
  the standard `DocsServerError -> ToolError(str(e))` + generic
  `Internal error: {type}` mapping.
- **`tests/test_services.py`** — H1 fix: `test_five_tools_registered` renamed to
  `test_six_tools_registered` (`assert len(tools) == 6`); `compare_versions`
  added to the `test_all_tools_have_annotations` loop; `compare_versions` schema
  constraints (`symbol` min/max length, `v1`/`v2` presence) asserted in
  `test_runtime_tool_schemas_include_input_constraints`.

## Tasks Completed

| Task | Name | Commit | Files |
| ---- | ---- | ------ | ----- |
| 1 | Add compare_service field to AppContext | `9f63623` | src/mcp_server_python_docs/app_context.py |
| 2 | Construct CompareService in app_lifespan + pass into AppContext | `4ced01a` | src/mcp_server_python_docs/server.py |
| 3 | Param aliases + compare_versions @mcp.tool block | `89e4a2d` | src/mcp_server_python_docs/server.py |
| 4 | Update tests/test_services.py to 6-tool surface (H1) | `c15e328` | tests/test_services.py |
| 5 | In-process MCP smoke test (enumerate tools) | (no file changes — verification only) | — |
| 6 | Full regression gate + deviation fix | `13fe752` | tests/test_retrieval_regression.py |

## Verification

- `uv run ruff check src/ tests/` — clean.
- `uv run pyright src/` — 0 errors, 0 warnings.
- `uv run pytest --tb=short -q` — **281 passed** (no regressions).
- `uv run pytest tests/test_services.py::TestToolRegistration -x` — 6 passed (H1 closed).
- Task 5 enumeration: `Tools registered: ['compare_versions', 'detect_python_version', 'get_docs', 'list_versions', 'lookup_package_docs', 'search_docs']` — six tools, `compare_versions` present.
- Source greps: `compare_versions` defined once (sync `def`), uses `_TOOL_ANNOTATIONS` (not `_PYPI_TOOL_ANNOTATIONS`), returns `CompareVersionsResult`, docstring has zero `signature_change` occurrences (M1). `assert len(tools) == 5` count 0; `assert len(tools) == 6` count 1; `test_five_tools_registered` count 0; `test_six_tools_registered` count 1.

## Cross-AI Review Findings Addressed

- **H1** — The pre-existing hardcoded 5-tool assertion is gone. `test_six_tools_registered`
  asserts `len(tools) == 6`; the annotation-check loop and the runtime-schema test both
  enumerate `compare_versions`. The whole `TestToolRegistration` class is green.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Updated tests/test_retrieval_regression.py for the new required AppContext field**
- **Found during:** Task 6 (full regression gate).
- **Issue:** `tests/test_retrieval_regression.py::_make_app_context` constructs
  `AppContext(...)` directly. Task 1 made `compare_service` a required (no-default)
  field, so the helper raised
  `TypeError: AppContext.__init__() missing 1 required positional argument: 'compare_service'`,
  failing `test_retrieval_regression_cases[local_version_defaulting]`.
- **Fix:** Imported `CompareService`, hoisted `content_service` into a local, and passed
  `compare_service=CompareService(db, content_service)` into the helper's `AppContext(...)`
  (matching the lifespan field order). No production behavior changed; this is a test-fixture
  catch-up directly caused by Task 1's required field — analogous to the H1 catch-up.
- **Files modified:** tests/test_retrieval_regression.py
- **Verification:** Full suite 281 passed; ruff + pyright clean.
- **Committed in:** `13fe752` (Task 6 commit).

This was the only AppContext-direct-construction site in `tests/` (verified via
`grep -rn "AppContext(" tests/`). All other tests go through `create_server()` /
`app_lifespan`, so no further fixture updates were needed.

**Total deviations:** 1 auto-fixed (1 blocking). Test-fixture catch-up only; no scope creep.

## Known Stubs

None. The tool delegates directly to the fully-wired `CompareService` (Plan 03) over
the live read-only SQLite connection; no placeholder data or mock wiring.

## Notes for Downstream Plans

- **Plan 05** can document the now-live `compare_versions` tool in README and add the
  manual `.github/INTEGRATION-TEST.md` entry. The server exposes six tools; restart any
  MCP client (Claude Desktop, Cursor, Inspector) to pick up the new tool.

## Self-Check: PASSED

- FOUND: src/mcp_server_python_docs/app_context.py (contains `compare_service: CompareService`)
- FOUND: src/mcp_server_python_docs/server.py (contains `def compare_versions(` and `app_ctx.compare_service.compare`)
- FOUND: tests/test_services.py (contains `test_six_tools_registered`)
- FOUND: commit 9f63623 (Task 1)
- FOUND: commit 4ced01a (Task 2)
- FOUND: commit 89e4a2d (Task 3)
- FOUND: commit c15e328 (Task 4)
- FOUND: commit 13fe752 (Task 6 deviation fix)
- Task 5 intentionally has no commit (verification-only, no file changes).

---
*Phase: 09-compare-versions*
*Completed: 2026-05-28*
