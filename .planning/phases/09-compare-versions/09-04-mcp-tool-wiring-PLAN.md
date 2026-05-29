---
phase: 09-compare-versions
plan: 04
type: execute
wave: 2
depends_on:
  - 09-02-result-models
  - 09-03-compare-service
files_modified:
  - src/mcp_server_python_docs/app_context.py
  - src/mcp_server_python_docs/server.py
  - tests/test_services.py
autonomous: true
requirements:
  - CMPR-01
  - CMPR-02
must_haves:
  truths:
    - "`AppContext` carries a `compare_service: CompareService` field that is populated by `app_lifespan` before the context is yielded."
    - "`server.py::create_server` registers a `@mcp.tool(annotations=_TOOL_ANNOTATIONS)` decorated function named `compare_versions` that delegates to `app_ctx.compare_service.compare`."
    - "FastMCP exposes the new tool over stdio: enumerating tools after lifespan startup lists `compare_versions` alongside the existing five tools."
    - "Calling `compare_versions(symbol='asyncio.TaskGroup', v1='3.10', v2='3.11')` end-to-end via FastMCP returns a `CompareVersionsResult` whose JSON shape matches what `CompareService.compare` produces, with FastMCP's auto-derived `outputSchema` including the new fields."
    - "Errors raised by `CompareService.compare` (VersionNotFoundError, SymbolNotFoundError, PageNotFoundError-fallback-as-changed-note, anything else) are converted to `ToolError(str(e))` or `ToolError(f'Internal error: {type(e).__name__}')` the same way the existing five tools handle them."
    - "Pre-existing tool-registration tests in `tests/test_services.py` are updated to expect SIX tools (not five) per cross-AI review H1: `test_five_tools_registered` (line 450) is renamed to `test_six_tools_registered` and its assertion is `assert len(tools) == 6`. Any other partial tool-list / annotation / schema assertions that need to enumerate the new tool are updated to include `compare_versions`."
  artifacts:
    - path: "src/mcp_server_python_docs/app_context.py"
      provides: "AppContext with new `compare_service: CompareService` field"
      contains: "compare_service: CompareService"
    - path: "src/mcp_server_python_docs/server.py"
      provides: "compare_versions FastMCP tool registration + lifespan wiring + parameter aliases"
      contains: "def compare_versions("
    - path: "tests/test_services.py"
      provides: "Updated tool-registration tests reflecting the 6-tool surface (per H1)"
      contains: "test_six_tools_registered"
  key_links:
    - from: "src/mcp_server_python_docs/server.py::app_lifespan"
      to: "src/mcp_server_python_docs/services/compare.py::CompareService"
      via: "construction call `CompareService(db, content_svc)` and pass into `AppContext(...)`"
      pattern: "CompareService\\("
    - from: "src/mcp_server_python_docs/server.py::create_server"
      to: "AppContext.compare_service"
      via: "tool body: `app_ctx.compare_service.compare(symbol, v1, v2)`"
      pattern: "app_ctx.compare_service.compare"
    - from: "tests/test_services.py (TestToolRegistration class)"
      to: "src/mcp_server_python_docs/server.py::compare_versions"
      via: "Updated assertions for the 6-tool surface"
      pattern: "assert len\\(tools\\) == 6"
---

<objective>
Wire `CompareService` into FastMCP: add the field to `AppContext`, construct the service in `app_lifespan`, register the new `@mcp.tool` block in `create_server`, add the two new parameter aliases (`SymbolParam`, `CompareVersionParam`), AND update `tests/test_services.py` so the tool-registration tests reflect the 6-tool surface (cross-AI review H1).

Purpose: Make `compare_versions` callable from real MCP clients (Claude Desktop, Cursor, MCP Inspector) AND keep the existing test suite green by updating the hardcoded 5-tool assertions. All the behavioral substance shipped in Plan 03; this plan is the thin DI + decorator layer that exposes it plus the test-fixture catch-up.

Output: Three file modifications — `app_context.py` (one new field), `server.py` (two new parameter aliases, one new lifespan construction line, one updated `AppContext(...)` call, one new `@mcp.tool` block, one updated batch import), and `tests/test_services.py` (rename `test_five_tools_registered` → `test_six_tools_registered`, bump the count assertion to 6, and extend any tool-list / annotation / schema assertions in `TestToolRegistration` that name tools explicitly to include `compare_versions`). Existing five tools' behavior and other tests remain unchanged.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@AGENTS.md
@.planning/phases/09-compare-versions/09-CONTEXT.md
@.planning/phases/09-compare-versions/09-RESEARCH.md
@.planning/phases/09-compare-versions/09-REVIEWS.md
@src/mcp_server_python_docs/app_context.py
@src/mcp_server_python_docs/server.py
@src/mcp_server_python_docs/services/compare.py
@tests/test_services.py

<interfaces>
<!-- Existing AppContext shape (verified May 26, 2026). -->

From src/mcp_server_python_docs/app_context.py (current — full file):
```python
@dataclass
class AppContext:
    db: sqlite3.Connection
    index_path: Path
    search_service: SearchService
    content_service: ContentService
    version_service: VersionService
    package_docs_service: PackageDocsService = field(default_factory=PackageDocsService)
    persistent_docs_cache: PersistentDocsCache | None = None
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    detected_python_version: str | None = None
    detected_python_source: str | None = None
```

From src/mcp_server_python_docs/server.py — three hook points:

(1) The existing service constructions in app_lifespan (lines 154-162):
```python
search_svc = SearchService(db, synonyms)
content_svc = ContentService(db, persistent_cache=persistent_docs_cache)
version_svc = VersionService(db)
package_docs_svc = PackageDocsService()
```

(2) The AppContext yield in app_lifespan (lines 179-191) — needs one new kwarg.

(3) The tool registration cluster in create_server (lines 280-376). Existing five tools all use this pattern. The new tool slots in immediately before the final `return mcp` on line 383.

Existing shared annotations (lines 214-225) — REUSE `_TOOL_ANNOTATIONS`:
```python
_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
)
```

Existing parameter alias pattern (lines 227-270) — add two new aliases at module scope:
```python
# Existing:
SearchQueryParam = Annotated[str, Field(max_length=500, description="...")]
VersionParam = Annotated[str | None, Field(description="...")]
PackageParam = Annotated[str, Field(min_length=1, max_length=214, description="...")]

# NEW (Plan 04):
SymbolParam = Annotated[str, Field(min_length=1, max_length=200, description="Qualified Python symbol name, e.g. 'asyncio.TaskGroup'")]
CompareVersionParam = Annotated[str, Field(description="Python version string, e.g. '3.11'")]
```

Existing tool body template (lines 280-299, `search_docs`):
```python
@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def search_docs(query, version=None, kind="auto", max_results=5, ctx: Context = None):
    app_ctx: AppContext = ctx.request_context.lifespan_context
    try:
        return app_ctx.search_service.search(query, version, kind, max_results)
    except DocsServerError as e:
        raise ToolError(str(e))
    except Exception as e:
        logger.exception("Unexpected error in search_docs")
        raise ToolError(f"Internal error: {type(e).__name__}")
```

From tests/test_services.py (current state — TestToolRegistration class around lines 408-515):
```python
class TestToolRegistration:
    def test_create_server_has_three_tools(self):  # Misnamed — actually checks 3 specific tools exist
        server = create_server()
        tools = server._tool_manager._tools
        tool_names = set(tools.keys())
        assert "search_docs" in tool_names
        assert "get_docs" in tool_names
        assert "list_versions" in tool_names

    def test_all_tools_have_annotations(self):
        server = create_server()
        tools = server._tool_manager._tools
        for name in ["search_docs", "get_docs", "list_versions"]:  # partial list — must add compare_versions
            tool = tools[name]
            annotations = tool.annotations
            assert annotations.readOnlyHint is True
            # ... etc

    def test_five_tools_registered(self):  # <-- LINE 450 — the H1 finding
        server = create_server()
        tools = server._tool_manager._tools
        assert len(tools) == 5  # <-- breaks after Plan 04 adds the 6th tool

    def test_runtime_tool_schemas_include_input_constraints(self):
        # ... enumerates specific schemas; check whether compare_versions needs an entry here
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add `compare_service` field to AppContext</name>
  <read_first>
    - src/mcp_server_python_docs/app_context.py (full file — already in context)
    - src/mcp_server_python_docs/services/compare.py (from Plan 03 — constructor signature is `__init__(self, db, content_service)`)
  </read_first>
  <action>
    Modify `src/mcp_server_python_docs/app_context.py`. Add one import line: `from mcp_server_python_docs.services.compare import CompareService` (insert it alphabetically among the existing service imports — between `.content` and `.package_docs` lines). Add one new field to the `AppContext` dataclass, placed immediately after `content_service: ContentService` (line 27) — that placement matches the lifespan construction order: `compare_service: CompareService`. The field is required (no default) — it must be passed explicitly by `app_lifespan`. Do not reorder existing fields. Do not change defaults or types of existing fields.
  </action>
  <verify>
    <automated>uv run ruff check src/mcp_server_python_docs/app_context.py && uv run pyright src/mcp_server_python_docs/app_context.py && uv run python -c "from mcp_server_python_docs.app_context import AppContext; import dataclasses; fields={f.name for f in dataclasses.fields(AppContext)}; assert 'compare_service' in fields, fields; print('AppContext fields:', sorted(fields))"</automated>
  </verify>
  <acceptance_criteria>
    - Source: `src/mcp_server_python_docs/app_context.py` contains `from mcp_server_python_docs.services.compare import CompareService`.
    - Source: `src/mcp_server_python_docs/app_context.py` contains `compare_service: CompareService`.
    - CLI: `uv run ruff check src/mcp_server_python_docs/app_context.py` exits 0.
    - CLI: `uv run pyright src/mcp_server_python_docs/app_context.py` exits 0.
    - Behavior: `dataclasses.fields(AppContext)` includes a field named `compare_service`.
  </acceptance_criteria>
  <done>The dataclass schema is updated and the new import resolves cleanly; pyright agrees the field is typed `CompareService`.</done>
</task>

<task type="auto">
  <name>Task 2: Construct CompareService in app_lifespan and pass it into AppContext</name>
  <read_first>
    - src/mcp_server_python_docs/server.py lines 115-211 (the `app_lifespan` async context manager — already in context)
    - src/mcp_server_python_docs/services/compare.py (from Plan 03 — `CompareService.__init__(self, db, content_service)`)
  </read_first>
  <action>
    Modify `src/mcp_server_python_docs/server.py`. In `app_lifespan` (~line 115), in the service construction block (currently lines 159-162: `search_svc = ...; content_svc = ...; version_svc = ...; package_docs_svc = ...`), insert one new line: `compare_svc = CompareService(db, content_svc)`. Place it immediately after `content_svc = ...` and before `version_svc = ...` to match the dataclass field order. Add the import: `from mcp_server_python_docs.services.compare import CompareService` (insert near the other `services.` imports at the top of the file). In the `yield AppContext(...)` call (lines 180-191), add the kwarg `compare_service=compare_svc` in the position matching the dataclass field order (after `content_service=content_svc`).
  </action>
  <verify>
    <automated>uv run ruff check src/mcp_server_python_docs/server.py && uv run pyright src/mcp_server_python_docs/server.py && grep -nE "compare_svc\s*=\s*CompareService\(db, content_svc\)" src/mcp_server_python_docs/server.py && grep -nE "compare_service\s*=\s*compare_svc" src/mcp_server_python_docs/server.py</automated>
  </verify>
  <acceptance_criteria>
    - Source: `src/mcp_server_python_docs/server.py` contains the line `compare_svc = CompareService(db, content_svc)` inside `app_lifespan`.
    - Source: `src/mcp_server_python_docs/server.py` contains `compare_service=compare_svc` inside the `AppContext(...)` constructor call.
    - Source: `src/mcp_server_python_docs/server.py` contains `from mcp_server_python_docs.services.compare import CompareService`.
    - CLI: `uv run ruff check src/mcp_server_python_docs/server.py` exits 0.
    - CLI: `uv run pyright src/mcp_server_python_docs/server.py` exits 0.
  </acceptance_criteria>
  <done>Lifespan construction and AppContext kwarg are wired; the file passes ruff and pyright.</done>
</task>

<task type="auto">
  <name>Task 3: Add parameter aliases and the `compare_versions` @mcp.tool block</name>
  <read_first>
    - src/mcp_server_python_docs/server.py lines 213-383 (param aliases + create_server with the five existing tools)
    - src/mcp_server_python_docs/models.py (CompareVersionsResult — added by Plan 02 with renamed `signature_delta` and new `note` field)
    - .planning/phases/09-compare-versions/09-RESEARCH.md Q7(c) — the byte-for-byte tool block template
  </read_first>
  <action>
    Modify `src/mcp_server_python_docs/server.py`. (a) After the existing param alias block (the last alias is `PackageParam` ending around line 270), add the two new aliases:
    `SymbolParam = Annotated[str, Field(min_length=1, max_length=200, description="Qualified Python symbol name, e.g. 'asyncio.TaskGroup'")]`
    `CompareVersionParam = Annotated[str, Field(description="Python version string, e.g. '3.11'")]`

    (b) In the module-level batch import from `mcp_server_python_docs.models` (around line 30-40), add `CompareVersionsResult` to the imported names alphabetically (between existing entries — the exact line depends on current alphabetical position).

    (c) In `create_server()`, immediately BEFORE the existing `# SRVR-07: _meta hint for get_docs tool.` comment (currently around line 377) and AFTER the `detect_python_version` tool block (ends at ~line 375), insert a new `@mcp.tool(annotations=_TOOL_ANNOTATIONS)` block following the byte-for-byte template from RESEARCH §Q7(c):

    Decorator: `@mcp.tool(annotations=_TOOL_ANNOTATIONS)` (NOT `_PYPI_TOOL_ANNOTATIONS` — compare_versions is closed-world).

    Function signature: `def compare_versions(symbol: SymbolParam, v1: CompareVersionParam, v2: CompareVersionParam, ctx: Context = None)  # type: ignore[assignment]` returning `CompareVersionsResult`.

    Docstring: short — "Diff a Python stdlib symbol between two indexed versions. Returns `change=added|removed|changed|unchanged` with optional `new_in`, `changed_in`, `deprecated_in`, `signature_delta` (advisory), `see_also_added/removed`, `section_diff`, and `note` fields. Both versions must be indexed; otherwise an actionable error names the available versions." (Note: docstring lists `signature_delta` per Plan 02's M1 rename — NOT `signature_change`.)

    Body — verbatim shape from the existing `search_docs` tool:
    - `app_ctx: AppContext = ctx.request_context.lifespan_context`
    - `try: return app_ctx.compare_service.compare(symbol, v1, v2)`
    - `except DocsServerError as e: raise ToolError(str(e))`
    - `except Exception as e: logger.exception("Unexpected error in compare_versions"); raise ToolError(f"Internal error: {type(e).__name__}")`

    Do NOT add any `asyncio.to_thread` (compare_versions is pure SQLite reads per RESEARCH Pitfall 3; matches `search_docs` / `get_docs` shape, not `lookup_package_docs` which IS async because it does network I/O).
  </action>
  <verify>
    <automated>uv run ruff check src/mcp_server_python_docs/server.py && uv run pyright src/mcp_server_python_docs/server.py && grep -nE "^def compare_versions\(|    def compare_versions\(" src/mcp_server_python_docs/server.py && grep -nE "SymbolParam = Annotated\[str" src/mcp_server_python_docs/server.py && grep -nE "CompareVersionParam = Annotated\[str" src/mcp_server_python_docs/server.py && grep -nE "from mcp_server_python_docs.models import" src/mcp_server_python_docs/server.py | head -1 && grep -c "CompareVersionsResult" src/mcp_server_python_docs/server.py</automated>
  </verify>
  <acceptance_criteria>
    - Source: `src/mcp_server_python_docs/server.py` contains `def compare_versions(` exactly once.
    - Source: `src/mcp_server_python_docs/server.py` contains `SymbolParam = Annotated[str` and `CompareVersionParam = Annotated[str` exactly once each.
    - Source: `src/mcp_server_python_docs/server.py` imports `CompareVersionsResult` from `mcp_server_python_docs.models`.
    - Source: the new tool function uses `@mcp.tool(annotations=_TOOL_ANNOTATIONS)` (not `_PYPI_TOOL_ANNOTATIONS`). Verify: `grep -B1 "def compare_versions(" src/mcp_server_python_docs/server.py | grep -q "_TOOL_ANNOTATIONS"` and that grep does NOT match `_PYPI_TOOL_ANNOTATIONS`.
    - Source: the new tool function returns `CompareVersionsResult` (verified by `grep -A1 "def compare_versions(" src/mcp_server_python_docs/server.py | grep "CompareVersionsResult"`).
    - Source: the new tool function is SYNC (uses `def`, not `async def`). Verify: `grep -E "(async )?def compare_versions\(" src/mcp_server_python_docs/server.py` matches only `def compare_versions(`.
    - Source: the docstring or signature does not contain the deprecated name `signature_change` (M1 — Plan 02 renamed it). Verify: `grep -A 5 "def compare_versions(" src/mcp_server_python_docs/server.py | grep -c "signature_change"` returns 0.
    - CLI: `uv run ruff check src/mcp_server_python_docs/server.py` exits 0.
    - CLI: `uv run pyright src/mcp_server_python_docs/server.py` exits 0.
  </acceptance_criteria>
  <done>The tool block compiles cleanly, the import is in place, and the param aliases are defined.</done>
</task>

<task type="auto">
  <name>Task 4: Update `tests/test_services.py` for the 6-tool surface (H1 — the critical regression fix)</name>
  <read_first>
    - tests/test_services.py lines 405-515 (the full `TestToolRegistration` class — already in context: includes `test_create_server_has_three_tools`, `test_all_tools_have_annotations`, `test_five_tools_registered` (line 450 — the H1 target), and `test_runtime_tool_schemas_include_input_constraints`)
    - src/mcp_server_python_docs/server.py (after Tasks 2-3 edits — already in context; confirm the tool name is exactly `compare_versions`)
    - .planning/phases/09-compare-versions/09-REVIEWS.md (H1 — the explicit pre-existing-test-fail risk)
  </read_first>
  <action>
    Modify `tests/test_services.py` to keep the `TestToolRegistration` class green after Phase 09 adds the 6th tool.

    **Edit 1 (the H1 core fix) — `test_five_tools_registered` → `test_six_tools_registered`:**

    Currently (line 450-455):
    ```python
    def test_five_tools_registered(self):
        from mcp_server_python_docs.server import create_server
        server = create_server()
        tools = server._tool_manager._tools
        assert len(tools) == 5
    ```

    Rename to `test_six_tools_registered` and bump the count assertion to 6:
    ```python
    def test_six_tools_registered(self):
        from mcp_server_python_docs.server import create_server
        server = create_server()
        tools = server._tool_manager._tools
        assert len(tools) == 6
    ```

    **Edit 2 — `test_all_tools_have_annotations` (line 431-448):**

    The for-loop iterates over `["search_docs", "get_docs", "list_versions"]` — a partial list. Since `compare_versions` uses the same `_TOOL_ANNOTATIONS` (readOnlyHint=True, destructiveHint=False, openWorldHint=False), add it to the list:
    ```python
    for name in ["search_docs", "get_docs", "list_versions", "compare_versions"]:
    ```

    Do NOT also add `lookup_package_docs` or `detect_python_version` to this loop — `lookup_package_docs` uses `_PYPI_TOOL_ANNOTATIONS` (openWorldHint=True) and would fail the assertion; pre-existing partial coverage is not this plan's job to expand.

    **Edit 3 — `test_runtime_tool_schemas_include_input_constraints` (line 457-478):**

    The test enumerates `search_docs` and `get_docs` schemas explicitly. Add lightweight schema assertions for `compare_versions` so a regression in its `SymbolParam`/`CompareVersionParam` constraints is caught:
    ```python
    compare_versions = schemas["compare_versions"]["properties"]
    assert compare_versions["symbol"]["maxLength"] == 200
    assert compare_versions["symbol"]["minLength"] == 1
    # v1 and v2 do not have min/max length constraints by current design — just assert they exist
    assert "v1" in compare_versions
    assert "v2" in compare_versions
    ```

    **Edit 4 (do NOT make) — `test_create_server_has_three_tools` (line 411-419):**

    This test asserts a SUBSET of tools by name (`search_docs`, `get_docs`, `list_versions` ARE all in the registry). It does NOT assert `len == 3`. It will continue to pass after Phase 09 adds `compare_versions`. Do NOT modify it; doing so risks expanding scope. (The test name is misleading — it's really a "these three specific tools exist" test, not a count test.)

    **Final source check:** After all edits, the file must have:
    - Zero occurrences of `assert len(tools) == 5` (verify: `grep -c "assert len(tools) == 5" tests/test_services.py` returns 0)
    - Exactly one occurrence of `assert len(tools) == 6` (verify: `grep -c "assert len(tools) == 6" tests/test_services.py` returns 1)
    - Zero occurrences of `test_five_tools_registered` (renamed away)
    - Exactly one occurrence of `test_six_tools_registered`
    - At least one mention of `compare_versions` in `TestToolRegistration` (the assertions in Edits 2 and 3)
  </action>
  <verify>
    <automated>uv run pytest tests/test_services.py::TestToolRegistration -x -v && grep -c "assert len(tools) == 5" tests/test_services.py | grep -q "^0$" && grep -c "assert len(tools) == 6" tests/test_services.py | grep -q "^1$" && grep -c "test_five_tools_registered" tests/test_services.py | grep -q "^0$" && grep -c "test_six_tools_registered" tests/test_services.py | grep -q "^1$" && grep -c "compare_versions" tests/test_services.py | grep -qE "^[1-9][0-9]*$"</automated>
  </verify>
  <acceptance_criteria>
    - Source: `tests/test_services.py` no longer contains `assert len(tools) == 5` (zero matches).
    - Source: `tests/test_services.py` contains `assert len(tools) == 6` exactly once.
    - Source: `tests/test_services.py` no longer contains `test_five_tools_registered` (zero matches).
    - Source: `tests/test_services.py` contains `test_six_tools_registered` exactly once.
    - Source: `tests/test_services.py` `test_all_tools_have_annotations` iterates over a list that includes `"compare_versions"`.
    - Source: `tests/test_services.py` `test_runtime_tool_schemas_include_input_constraints` enumerates `schemas["compare_versions"]["properties"]` with at least one `assert` on its constraints.
    - CLI: `uv run pytest tests/test_services.py::TestToolRegistration -x -v` exits 0 (the WHOLE class is green — H1 closed).
    - CLI: `uv run ruff check tests/test_services.py` exits 0.
  </acceptance_criteria>
  <done>`TestToolRegistration` reflects the 6-tool surface; the H1 finding is fully closed.</done>
</task>

<task type="auto">
  <name>Task 5: In-process MCP smoke test — tool is enumerable and callable</name>
  <read_first>
    - src/mcp_server_python_docs/server.py (after edits from Tasks 2-3 — already in context)
    - tests/test_multi_version.py (lines 17-81 — fixture pattern to clone for a real lifespan-backed db)
    - Any existing test that exercises FastMCP via `create_server()` directly. If none exists, fall back to enumerating registered tools via the `mcp.tool_manager`/`tool_registry` surface as recently as FastMCP 1.27 exposes (the MCP SDK is pinned `mcp>=1.27.0,<2.0.0` — check `uv run python -c "from mcp.server.fastmcp import FastMCP; help(FastMCP)"` if uncertain).
  </read_first>
  <action>
    Run a quick in-process verification that does NOT require a live `index.db` (uses the `compare_db` fixture pattern instead). Two acceptable shapes:

    Option A — enumerate via FastMCP introspection: `uv run python -c "from mcp_server_python_docs.server import create_server; mcp = create_server(); tools = ...; assert 'compare_versions' in [t.name for t in tools]"`. The exact attribute path depends on FastMCP 1.27. Acceptable surfaces include `mcp._tool_manager._tools` or `await mcp.list_tools()` — pick the one that already works for the project (read `tests/` for existing patterns first; if FastMCP exposes only an async `list_tools`, run it under `asyncio.run`).

    Option B — if introspection is impractical, run the existing stdio smoke test suite (if present under `tests/`, look for files containing `subprocess.Popen` + `python-docs-mcp-server`). The stdio test exercises the real `tools/list` over the MCP protocol; if it lists `compare_versions`, the registration works.

    If neither path is workable in <10 minutes of effort, document why in the SUMMARY and rely on the manual integration check Plan 05 adds to `.github/INTEGRATION-TEST.md` instead. Do not block on a brittle introspection check.
  </action>
  <verify>
    <automated>uv run python -c "import asyncio; from mcp_server_python_docs.server import create_server; mcp = create_server(); tools = asyncio.run(mcp.list_tools()); names = [t.name for t in tools]; assert 'compare_versions' in names, names; print('Tools registered:', sorted(names))"</automated>
  </verify>
  <acceptance_criteria>
    - Behavior: the verify command exits 0 and prints `Tools registered:` followed by a list containing `compare_versions` and the five existing tools (`search_docs`, `get_docs`, `lookup_package_docs`, `list_versions`, `detect_python_version`).
    - If the introspection command fails because FastMCP's introspection surface differs from `list_tools()`, the executor must (a) inspect FastMCP 1.27's actual API via `uv run python -c "import mcp.server.fastmcp as m; print(dir(m.FastMCP))"`, (b) substitute the working attribute, (c) leave a note in the SUMMARY explaining the substitution.
  </acceptance_criteria>
  <done>An in-process enumeration confirms `compare_versions` is registered alongside the five existing tools, OR the SUMMARY explicitly documents why the in-process check was skipped and defers verification to Plan 05's INTEGRATION-TEST entry.</done>
</task>

<task type="auto">
  <name>Task 6: Full regression — every existing test still passes (including the updated 6-tools test)</name>
  <read_first>
    - AGENTS.md (Done Means — `pytest`, `ruff`, `pyright`)
  </read_first>
  <action>
    Run the full quality gate. Plan 04's surgical edits to `app_context.py`, `server.py`, AND `tests/test_services.py` could plausibly break the existing AppContext-construction smoke tests, stdio smoke tests, or any test that inspects the tool list. Confirm none break.
  </action>
  <verify>
    <automated>uv run pytest --tb=short -q && uv run ruff check src/ tests/ && uv run pyright src/</automated>
  </verify>
  <acceptance_criteria>
    - CLI: `uv run pytest --tb=short -q` exits 0 (full suite — must include Plan 03's `tests/test_compare_versions.py` AND every pre-existing test AND the updated `test_six_tools_registered`).
    - CLI: `uv run ruff check src/ tests/` exits 0.
    - CLI: `uv run pyright src/` exits 0.
  </acceptance_criteria>
  <done>Full quality gate is green. Phase 09 is feature-complete in code; Plan 05 finishes user-facing docs.</done>
</task>

</tasks>

<verification>
- `compare_versions` appears in FastMCP's registered tools list.
- AppContext has a typed `compare_service` field constructed in `app_lifespan`.
- `tests/test_services.py::TestToolRegistration` is green for the 6-tool surface (H1 closed).
- Existing five tools and their tests are byte-identical (no accidental edits) outside the surgical H1 fix.
- Full quality gate (pytest + ruff + pyright) passes.
</verification>

<success_criteria>
- `uv run python-docs-mcp-server --help` (or equivalent FastMCP introspection) shows six tools total.
- Real MCP clients (Claude Desktop, Cursor, Inspector) can call `compare_versions` after the server restarts — verified end-to-end by Plan 05's INTEGRATION-TEST entry.
- No existing tool's behavior changed.
- The pre-existing 5-tool hardcoded assertion is gone (H1 closed).
</success_criteria>

<output>
Create `.planning/phases/09-compare-versions/09-04-mcp-tool-wiring-SUMMARY.md` when done.
</output>
