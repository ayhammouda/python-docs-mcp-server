# Phase 1: Foundation & Stdio Hygiene & Symbol Slice - Research

**Researched:** 2026-04-16
**Confidence:** HIGH
**Research mode:** Synthesis from project research + Context7 SDK docs + build guide

## Executive Summary

Phase 1 uses well-documented, stable patterns. The FastMCP lifespan + typed AppContext pattern is confirmed in the official `mcp` 1.27.0 SDK README. Tool annotations (`readOnlyHint`, `destructiveHint`, `openWorldHint`) are supported via the `@mcp.tool()` decorator. Pydantic BaseModel return types auto-generate `structuredContent` + `outputSchema`. The `sphobjinv` API (`Inventory(url=...)` + `.objects` iteration) is stable since v2.3.x. All Phase 1 patterns are standard — no upstream risk.

## Key Technical Findings

### 1. FastMCP Lifespan + Typed AppContext (SRVR-01)

**Pattern confirmed from official SDK README (Context7):**

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from mcp.server.fastmcp import Context, FastMCP

@dataclass
class AppContext:
    db: Database  # typed dependency

@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    db = await Database.connect()
    try:
        yield AppContext(db=db)
    finally:
        await db.disconnect()

mcp = FastMCP("My App", lifespan=app_lifespan)
```

**Access in tool handlers:**
```python
@mcp.tool()
def my_tool(query: str, ctx: Context) -> str:
    app_ctx: AppContext = ctx.request_context.lifespan_context
    # use app_ctx.db, app_ctx.synonyms, etc.
```

### 2. Structured Output via Pydantic (SRVR-05)

Tools returning `BaseModel` subclasses automatically get `structuredContent` + `outputSchema`. Confirmed in SDK docs:

```python
class SearchDocsResult(BaseModel):
    hits: list[SymbolHit]
    note: str | None = None

@mcp.tool()
def search_docs(query: str, ...) -> SearchDocsResult:
    ...
```

No explicit `outputSchema` registration needed — FastMCP generates it from the return type annotation.

### 3. Tool Annotations (SRVR-02)

The `@mcp.tool()` decorator accepts annotation kwargs. Based on MCP spec 2025-11-25 and CLAUDE.md confirmation:

```python
@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    }
)
def search_docs(...) -> SearchDocsResult: ...
```

### 4. Stdio Hygiene (HYGN-01 through HYGN-06)

**os.dup2 fd redirect (B3 blocker):** Must happen before any import that could write to stdout:
1. Save real stdout fd: `real_stdout_fd = os.dup(1)`
2. Redirect fd 1 to stderr: `os.dup2(2, 1)` 
3. Create a file object from saved fd for MCP framer: `real_stdout = os.fdopen(real_stdout_fd, 'w')`
4. Replace `sys.stdout` with stderr wrapper

This must be the very first code in `__main__.py`, before `import mcp` or any other library import.

**SIGPIPE handling (HYGN-03):** `signal.signal(signal.SIGPIPE, signal.SIG_IGN)` + catch `BrokenPipeError` in shutdown. On Windows, SIGPIPE doesn't exist — guard with `hasattr(signal, 'SIGPIPE')`.

**Lifespan error handling (HYGN-05):** Wrap `app_lifespan` body in try/except, log to stderr, write to `last-error.log` in cache dir, raise `SystemExit(1)`.

**nextCursor omission (HYGN-06):** FastMCP handles `tools/list` internally. The SDK's default behavior should not include `nextCursor` for a 1-tool server. If it does, verify via the stdio smoke test.

### 5. sphobjinv Inventory Parsing (INGR-I-01 through INGR-I-06)

**API (confirmed v2.4):**
```python
import sphobjinv as soi
inv = soi.Inventory(url=f"https://docs.python.org/{version}/objects.inv")
for obj in inv.objects:
    # obj.name: str — qualified name (e.g., "asyncio.TaskGroup")
    # obj.domain: str — "py"
    # obj.role: str — "class", "function", "method", "attribute", "data", "module", "exception"
    # obj.uri: str — may contain "$" shorthand
    # obj.dispname: str — display name, "-" means use obj.name
    # obj.priority: str — "1" (default), "0" (important), "-1" (unimportant)
```

**URI expansion (INGR-I-03):** `$` in URI means "replace with obj.name". E.g., `library/asyncio-task.html#$` becomes `library/asyncio-task.html#asyncio.TaskGroup`.

**dispname fallback (INGR-I-04):** When `obj.dispname == '-'`, use `obj.name` as the display name.

**Duplicate handling (INGR-I-05):** Some symbols appear multiple times with different roles (e.g., `json.dumps` as both `function` and `method`). Phase 1 uses `UNIQUE(doc_set_id, qualified_name)` on the symbols table (Phase 2 corrects to `UNIQUE(doc_set_id, qualified_name, symbol_type)`). For Phase 1, use INSERT OR REPLACE with priority ordering: class > function > method > attribute > data.

### 6. SQLite Connection Factory (STOR-06, STOR-07, STOR-08)

**Two handles:**
- **Serving (read-only):** `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`
- **Ingestion (read-write):** `sqlite3.connect(path)`

**Both set PRAGMAs:**
```python
conn.execute("PRAGMA journal_mode = WAL")
conn.execute("PRAGMA synchronous = NORMAL")
conn.execute("PRAGMA foreign_keys = ON")
```

**FTS5 check (STOR-08):**
```python
def assert_fts5_available(conn):
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_check USING fts5(x)")
        conn.execute("DROP TABLE _fts5_check")
    except sqlite3.OperationalError:
        # Platform-aware message
        import platform
        if platform.system() == "Linux" and platform.machine() == "x86_64":
            hint = "pip install 'mcp-server-python-docs[pysqlite3]'"
        else:
            hint = "Install Python from python.org or run: uv python install"
        raise FTS5UnavailableError(f"SQLite FTS5 not available. {hint}")
```

### 7. Click CLI (CLI-01, CLI-03)

```python
import click

@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)

@main.command()
def serve(): ...

@main.command()
def build_index(): ...

@main.command()
def validate_corpus(): ...
```

### 8. Package Structure (Phase 1 subset)

```
src/mcp_server_python_docs/
    __init__.py
    __main__.py          # stdio hygiene + Click CLI entry
    server.py            # FastMCP setup, tool registration, lifespan
    app_context.py       # AppContext dataclass
    models.py            # Pydantic input/output models
    errors.py            # DocsServerError hierarchy
    storage/
        __init__.py
        db.py            # connection factory, FTS5 check, PRAGMAs
    ingestion/
        __init__.py
        inventory.py     # sphobjinv wrapper
    data/
        synonyms.yaml    # curated synonym table (100-200 entries)
tests/
    __init__.py
    test_phase1_integration.py
    test_schema_snapshot.py
    test_stdio_hygiene.py
    fixtures/
        schema-search_docs-input.json
        schema-search_docs-output.json
```

## Validation Architecture

### Dimension 1: Functional Correctness
- `search_docs("asyncio.TaskGroup")` returns hit with `asyncio-task.html` in URI
- `build-index --versions 3.13` populates 13K+ symbol rows
- Missing index produces copy-paste stderr message
- FTS5 unavailable produces platform-aware error

### Dimension 2: Protocol Compliance
- Subprocess stdout-sentinel test: zero non-MCP bytes on stdout
- `tools/list` returns exactly `search_docs` (no `get_docs`, no `list_versions`)
- Tool schema matches committed JSON fixtures

### Dimension 3: Structural Integrity
- Pydantic schema snapshot drift guard
- Wheel content test for synonyms.yaml
- `app_lifespan` yields typed `AppContext` with all dependencies

### Dimension 4: Error Handling
- Lifespan errors logged + `last-error.log` + `SystemExit(1)`
- SIGPIPE/BrokenPipeError safe shutdown
- Non-symbol queries return empty hits with informational note (not isError)

## RESEARCH COMPLETE

All Phase 1 patterns are standard and well-documented. No upstream risk. No blockers beyond B3 (os.dup2 fd redirect) and B6 (Pydantic schema snapshot), both with clear solutions above.
