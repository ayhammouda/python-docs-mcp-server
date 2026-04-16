---
phase: 5
plan_id: 05-B
title: "Tool registration — get_docs, list_versions, and server.py refactor"
wave: 1
depends_on:
  - 05-A
files_modified:
  - src/mcp_server_python_docs/server.py
  - src/mcp_server_python_docs/app_context.py
requirements:
  - SRVR-03
  - SRVR-04
  - SRVR-07
autonomous: true
---

<objective>
Register `get_docs` and `list_versions` as MCP tools in `server.py` with the same `ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)`. Add `_meta = {"anthropic/maxResultSizeChars": 16000}` to `get_docs`. Refactor `server.py` to delegate all tool logic to services (SearchService, ContentService, VersionService) instead of inline functions. Remove `_do_search()` and `_symbol_exists()` from server.py.
</objective>

<tasks>

<task id="1">
<title>Update AppContext to hold service instances</title>
<read_first>
- src/mcp_server_python_docs/app_context.py (current dataclass with db, index_path, synonyms)
- src/mcp_server_python_docs/services/search.py (SearchService constructor)
- src/mcp_server_python_docs/services/content.py (ContentService constructor)
- src/mcp_server_python_docs/services/version.py (VersionService constructor)
</read_first>
<action>
Update `src/mcp_server_python_docs/app_context.py` to add optional service instance fields. The services are constructed in `app_lifespan` and stored on AppContext for tool handlers to use.

Add these imports and fields:

```python
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_server_python_docs.services.search import SearchService
    from mcp_server_python_docs.services.content import ContentService
    from mcp_server_python_docs.services.version import VersionService


@dataclass
class AppContext:
    """Application context with typed dependencies for lifespan DI."""

    db: sqlite3.Connection
    index_path: Path
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    search_service: SearchService | None = None
    content_service: ContentService | None = None
    version_service: VersionService | None = None
```

Using `TYPE_CHECKING` for service imports avoids circular imports since services import from models/retrieval.
</action>
<acceptance_criteria>
- `app_context.py` has `search_service`, `content_service`, `version_service` fields
- Fields default to `None` to maintain backwards compatibility
- Service types use `TYPE_CHECKING` guard to avoid circular imports
- `python -c "from mcp_server_python_docs.app_context import AppContext"` succeeds
</acceptance_criteria>
</task>

<task id="2">
<title>Refactor server.py — wire services in lifespan, delegate tool handlers</title>
<read_first>
- src/mcp_server_python_docs/server.py (full file — understand current structure)
- src/mcp_server_python_docs/services/search.py (SearchService API)
- src/mcp_server_python_docs/services/content.py (ContentService API)
- src/mcp_server_python_docs/services/version.py (VersionService API)
- src/mcp_server_python_docs/models.py (GetDocsResult, ListVersionsResult)
</read_first>
<action>
Refactor `src/mcp_server_python_docs/server.py`:

**1. Update imports** — add service imports:
```python
from mcp_server_python_docs.services.search import SearchService
from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.services.version import VersionService
```

**2. Update `app_lifespan`** — construct services after DB and synonyms are ready:
```python
# After synonyms are loaded and db is opened:
search_svc = SearchService(db, synonyms)
content_svc = ContentService(db)
version_svc = VersionService(db)

yield AppContext(
    db=db,
    index_path=index_path,
    synonyms=synonyms,
    search_service=search_svc,
    content_service=content_svc,
    version_service=version_svc,
)
```

**3. Remove `_symbol_exists()` and `_do_search()`** — these are now in SearchService.

**4. Refactor `search_docs` tool handler** to delegate to SearchService:
```python
@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False,
    )
)
def search_docs(
    query: str,
    version: str | None = None,
    kind: Literal["auto", "page", "symbol", "section", "example"] = "auto",
    max_results: int = 5,
    ctx: Context = None,
) -> SearchDocsResult:
    """Search Python documentation. Use kind='symbol' for API lookups
    (asyncio.TaskGroup), kind='example' for code samples, kind='auto' otherwise."""
    app_ctx: AppContext = ctx.request_context.lifespan_context
    try:
        return app_ctx.search_service.search(query, version, kind, max_results)
    except DocsServerError as e:
        raise ToolError(str(e))
```

**5. Add `get_docs` tool** with `_meta` for SRVR-07:
```python
@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False,
    )
)
def get_docs(
    slug: str,
    version: str | None = None,
    anchor: str | None = None,
    max_chars: int = 8000,
    start_index: int = 0,
    ctx: Context = None,
) -> GetDocsResult:
    """Retrieve a documentation page or specific section. Provide anchor for
    section-only retrieval (much cheaper). Pagination via start_index."""
    app_ctx: AppContext = ctx.request_context.lifespan_context
    try:
        result = app_ctx.content_service.get_docs(
            slug, version, anchor, max_chars, start_index
        )
        return result
    except DocsServerError as e:
        raise ToolError(str(e))
```

For `_meta`, check if FastMCP's `@mcp.tool()` supports a `metadata` parameter. If it does, add `metadata={"anthropic/maxResultSizeChars": 16000}`. If not, the `_meta` may need to be set on the tool definition after registration. Try the direct approach first:
```python
@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False,
    ),
)
```
If FastMCP does not support `_meta` via the decorator, add it post-registration by accessing `mcp._tool_manager` or similar internal API and setting the metadata directly.

**6. Add `list_versions` tool**:
```python
@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False,
    )
)
def list_versions(
    ctx: Context = None,
) -> ListVersionsResult:
    """List Python documentation versions available in this index."""
    app_ctx: AppContext = ctx.request_context.lifespan_context
    return app_ctx.version_service.list_versions()
```

**7. Remove unused imports** from server.py — the retrieval imports (`classify_query`, `build_match_expression`, `lookup_symbols_exact`, `search_sections`, `search_symbols`, `search_examples`) are no longer needed since they're used by SearchService, not directly by server.py.
</action>
<acceptance_criteria>
- `server.py` no longer contains `_do_search()` or `_symbol_exists()` functions
- `server.py` imports `SearchService`, `ContentService`, `VersionService`
- `app_lifespan` constructs all three services and passes them to `AppContext`
- `search_docs` tool delegates to `app_ctx.search_service.search()`
- `get_docs` tool is registered with `ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)`
- `list_versions` tool is registered with the same annotations
- `python -c "from mcp_server_python_docs.server import create_server; s = create_server()"` succeeds
- No remaining imports of `classify_query`, `build_match_expression`, `lookup_symbols_exact`, `search_sections`, `search_symbols`, `search_examples` in server.py (moved to SearchService)
- `get_docs` tool return type is `GetDocsResult`
- `list_versions` tool return type is `ListVersionsResult`
</acceptance_criteria>
</task>

<task id="3">
<title>Handle _meta for get_docs tool (SRVR-07)</title>
<read_first>
- src/mcp_server_python_docs/server.py (after task 2 refactor)
</read_first>
<action>
Verify that `_meta = {"anthropic/maxResultSizeChars": 16000}` is correctly associated with the `get_docs` tool.

**Approach 1 (preferred):** If FastMCP 1.27 `@mcp.tool()` accepts a `metadata` kwarg, use it directly in the decorator.

**Approach 2 (fallback):** If the decorator doesn't support metadata, add it after `create_server()` returns but before `mcp.run()` is called. Access the tool's definition via `mcp._tool_manager._tools["get_docs"]` (or equivalent internal path) and set `_meta` on the tool's metadata dict.

**Approach 3 (simplest fallback):** Include `_meta` in the tool's return value. The `GetDocsResult` model could have a `_meta` field, but this conflates response data with tool metadata. Only use this if approaches 1 and 2 fail.

Document whichever approach works in a comment explaining why.
</action>
<acceptance_criteria>
- `get_docs` tool metadata includes `anthropic/maxResultSizeChars: 16000`
- The approach taken is documented in a comment in server.py
- If using internal API access, a comment notes it may need updating on mcp SDK version bumps
</acceptance_criteria>
</task>

</tasks>

<verification>
1. `uv run python -c "from mcp_server_python_docs.server import create_server; s = create_server()"` succeeds
2. Three tools registered: search_docs, get_docs, list_versions
3. All three tools have `readOnlyHint=True, destructiveHint=False, openWorldHint=False`
4. get_docs includes _meta with maxResultSizeChars
5. Existing tests pass: `uv run pytest tests/ -x -q`
</verification>

<must_haves>
- get_docs registered as MCP tool with ToolAnnotations (SRVR-03)
- list_versions registered as MCP tool with ToolAnnotations (SRVR-04)
- get_docs has _meta = {"anthropic/maxResultSizeChars": 16000} (SRVR-07)
- server.py is thin — delegates to services, no inline search/content logic
</must_haves>
