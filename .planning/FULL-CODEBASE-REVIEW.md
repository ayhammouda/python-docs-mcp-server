---
phase: full-codebase
reviewed: 2026-04-16T12:00:00Z
depth: deep
files_reviewed: 25
files_reviewed_list:
  - src/mcp_server_python_docs/__init__.py
  - src/mcp_server_python_docs/__main__.py
  - src/mcp_server_python_docs/app_context.py
  - src/mcp_server_python_docs/errors.py
  - src/mcp_server_python_docs/models.py
  - src/mcp_server_python_docs/server.py
  - src/mcp_server_python_docs/storage/__init__.py
  - src/mcp_server_python_docs/storage/db.py
  - src/mcp_server_python_docs/storage/schema.sql
  - src/mcp_server_python_docs/services/__init__.py
  - src/mcp_server_python_docs/services/search.py
  - src/mcp_server_python_docs/services/content.py
  - src/mcp_server_python_docs/services/version.py
  - src/mcp_server_python_docs/services/version_resolution.py
  - src/mcp_server_python_docs/services/cache.py
  - src/mcp_server_python_docs/services/observability.py
  - src/mcp_server_python_docs/retrieval/__init__.py
  - src/mcp_server_python_docs/retrieval/budget.py
  - src/mcp_server_python_docs/retrieval/query.py
  - src/mcp_server_python_docs/retrieval/ranker.py
  - src/mcp_server_python_docs/ingestion/__init__.py
  - src/mcp_server_python_docs/ingestion/inventory.py
  - src/mcp_server_python_docs/ingestion/sphinx_json.py
  - src/mcp_server_python_docs/ingestion/publish.py
  - pyproject.toml
findings:
  critical: 2
  warning: 6
  info: 5
  total: 13
status: issues_found
---

# Full Codebase: Code Review Report

**Reviewed:** 2026-04-16T12:00:00Z
**Depth:** deep
**Files Reviewed:** 25
**Status:** issues_found

## Summary

Deep review of all 25 source files in mcp-server-python-docs, covering the server layer (FastMCP + lifespan DI), service layer (search, content, version), retrieval layer (FTS5 query processing, ranking, budget), ingestion layer (objects.inv, Sphinx JSON, publish pipeline), and storage layer (SQLite connection management, schema DDL).

Overall the codebase is well-structured with clear layer separation, proper stdio hygiene, and correct FTS5 escaping on the user-input path. Two critical issues were found: (1) synonym expansion uses substring matching that causes false positive query expansion for short concept keys, and (2) tool handlers only catch `DocsServerError`, allowing unexpected exceptions to propagate as JSON-RPC protocol errors instead of tool errors. Six warnings cover ordering bugs, missing input bounds, connection threading safety, and error handling gaps.

## Critical Issues

### CR-01: Synonym Substring Matching Causes False Positive Query Expansion

**File:** `src/mcp_server_python_docs/retrieval/query.py:122-124`
**Issue:** `expand_synonyms()` uses `concept in query_lower` (Python substring containment) to match multi-word concepts against the user query. Because `synonyms.yaml` contains short concept keys like "os" (2 chars), "set" (3 chars), "url" (3 chars), "gc" (2 chars), "dis" (3 chars), "ast" (3 chars), and "ftp" (3 chars), many unrelated queries trigger false expansion. For example:
- Query "purpose" contains "os" -- expands with `[os, os.path, os.environ, os.getcwd, operating system]`
- Query "reset" or "offset" contains "set" -- expands with `[set, frozenset, intersection, union, ...]`
- Query "distance" contains "dis" -- expands with `[dis, disassemble, bytecode, opcode, instruction]`
- Query "disaster" contains "dis" and "ast" -- double false expansion

This corrupts search results for a significant fraction of natural-language queries, which is the primary use case for concept search. Since LLMs are the clients, degraded search quality directly undermines the tool's core value proposition.

**Fix:** Use word-boundary matching instead of substring containment. Replace the substring check with a regex or token-level comparison:

```python
import re

# Pre-compile word-boundary patterns for all concepts at init time
_concept_patterns: dict[str, re.Pattern] = {}
for concept in synonyms:
    if " " in concept:
        # Multi-word: match as exact phrase with word boundaries
        _concept_patterns[concept] = re.compile(
            r"\b" + re.escape(concept) + r"\b"
        )

# In expand_synonyms, replace lines 121-124 with:
query_lower = query.lower()
for concept, expansions in synonyms.items():
    if " " not in concept:
        continue  # Single-word concepts already handled by token match above
    pattern = _concept_patterns.get(concept)
    if pattern and pattern.search(query_lower):
        expanded.update(expansions)
```

Single-word concepts are already handled correctly by the per-token exact match at line 116-118. Only the multi-word concept matching (lines 121-124) needs the word-boundary fix. However, even the multi-word substring approach has issues: "file io" would match inside "profile io" -- the word-boundary regex fixes this too.

### CR-02: Tool Handlers Only Catch DocsServerError, Non-Domain Exceptions Become Protocol Errors

**File:** `src/mcp_server_python_docs/server.py:140-143, 157-162, 170-173`
**Issue:** All three tool handlers (`search_docs`, `get_docs`, `list_versions`) wrap `DocsServerError` into `ToolError` but do not catch other exception types. If the SQLite connection becomes corrupted, if an `sqlite3.OperationalError` escapes the ranker's catch block (e.g., during `lookup_symbols_exact` which has no try/except), if an `AttributeError` occurs because a service field is `None` (the type signature allows it via `AppContext.search_service: SearchService | None = None`), or if any unexpected runtime error occurs, it will propagate as a JSON-RPC protocol error.

Per the MCP specification, tool execution failures MUST return `isError: true` in the tool result, not as protocol-level errors. Protocol errors indicate a broken transport or invalid request, not a failed tool execution. An LLM client receiving a protocol error may retry indefinitely or disconnect, whereas a tool error allows it to report the failure and move on.

**Fix:** Add a broad `Exception` catch after the `DocsServerError` catch in each tool handler:

```python
@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def search_docs(
    query: str,
    version: str | None = None,
    kind: Literal["auto", "page", "symbol", "section", "example"] = "auto",
    max_results: int = 5,
    ctx: Context = None,  # type: ignore[assignment]
) -> SearchDocsResult:
    """..."""
    app_ctx: AppContext = ctx.request_context.lifespan_context
    try:
        return app_ctx.search_service.search(query, version, kind, max_results)
    except DocsServerError as e:
        raise ToolError(str(e))
    except Exception as e:
        logger.exception("Unexpected error in search_docs")
        raise ToolError(f"Internal error: {type(e).__name__}")
```

Note the `except Exception` catch logs the full traceback to stderr (for debugging) but only sends the exception type name to the client (to avoid leaking internal paths or stack traces).

## Warnings

### WR-01: Missing Input Length Constraints on String Fields

**File:** `src/mcp_server_python_docs/models.py:18-20, 72`
**Issue:** The `query` field in `SearchDocsInput` and the `slug` field in `GetDocsInput` have no `max_length` constraint. An LLM client (or a malicious caller) could send a multi-megabyte query string that would be tokenized, synonym-expanded, and passed to FTS5 MATCH. While FTS5 has its own internal limits, processing an extremely long query string wastes CPU and memory in the Python layer (tokenization, escaping, synonym expansion loop over the full synonyms dict). Similarly, a very long `slug` string would be passed directly to a SQL query.

**Fix:** Add `max_length` constraints to string input fields:

```python
query: str = Field(
    max_length=500,
    description="Search query - Python symbol (asyncio.TaskGroup) or concept (parse json)"
)

slug: str = Field(
    max_length=500,
    description="Page slug (e.g. 'library/asyncio-task.html')"
)
```

### WR-02: Version Validation Ordering Bug in inventory.py

**File:** `src/mcp_server_python_docs/ingestion/inventory.py:92-120`
**Issue:** The version format validation (`re.match(r"^\d+\.\d+$", version)`) at line 117 runs AFTER the `doc_sets` upsert at line 92. If an invalid version string bypasses the CLI validation (e.g., if `ingest_inventory` is called programmatically), the invalid version is written to `doc_sets` before the validation check rejects it. The version is also used in a URL f-string at line 123, but validation happens at line 117 before that.

The CLI (`__main__.py:121-128`) validates version format before calling `ingest_inventory`, so this is not exploitable through the normal CLI path. However, the function's internal ordering is wrong -- validation should precede side effects.

**Fix:** Move the version format validation to the top of the function, before any database writes:

```python
def ingest_inventory(
    conn: sqlite3.Connection, version: str, *, is_default: bool = False
) -> int:
    # Validate version format first, before any DB writes
    import re
    if not re.match(r"^\d+\.\d+$", version):
        from mcp_server_python_docs.errors import IngestionError
        raise IngestionError(f"Invalid version format: {version!r}")

    bootstrap_schema(conn)
    # ... rest of function
```

### WR-03: SQLite Connection Not Thread-Safe for Concurrent Async Tool Calls

**File:** `src/mcp_server_python_docs/server.py:75, 85-87`
**Issue:** The lifespan opens a single `sqlite3.Connection` (line 75) that is shared across all three service instances (lines 85-87). FastMCP runs synchronous tool handlers in a thread pool (via `anyio.to_thread.run_sync`). When multiple tool calls arrive concurrently, they execute in different threads sharing the same `sqlite3.Connection`. CPython's default `sqlite3.connect()` sets `check_same_thread=True`, but the URI-mode connection (`sqlite3.connect(f"file:...?mode=ro", uri=True)`) also defaults to `check_same_thread=True`. This means concurrent tool calls from different threads would raise `ProgrammingError: SQLite objects created in a thread can only be used in that same thread`.

In practice, FastMCP 1.27 may or may not use threading for sync handlers (it depends on the transport and server implementation). If it does, this is a crash. If it serializes sync handlers on the event loop thread, this works but blocks the event loop.

**Fix:** Pass `check_same_thread=False` when opening the read-only connection. This is safe for read-only SQLite access (no write contention):

```python
db = sqlite3.connect(
    f"file:{index_path}?mode=ro",
    uri=True,
    check_same_thread=False,
)
```

Also apply the same fix in `storage/db.py:get_readonly_connection()`:

```python
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
```

### WR-04: Lifespan Error Log Can Suppress Original Exception

**File:** `src/mcp_server_python_docs/server.py:98-107`
**Issue:** In `app_lifespan`, the `except Exception` block at line 98 catches exceptions during tool execution (between `yield` and the `finally`). It attempts to write an error log file at `cache_dir / "last-error.log"`. If the `cache_dir` does not exist (e.g., user deleted it while server was running) and the `write_text` call at line 104 raises an `OSError`, the inner `except Exception: pass` at lines 105-106 swallows that secondary error. This is correct -- the original exception is re-raised by the bare `raise` at line 107. However, the `traceback.format_exc()` at line 101 captures the traceback of the current exception context, which is fine.

The actual concern is that the `except Exception` after `yield` catches exceptions from tool handlers, but tool handlers should have their own error handling (CR-02 above). The lifespan error handler here becomes a safety net, but it writes to a file on the filesystem during what might be a disk-full condition, adding fragility.

**Fix:** This is acceptable as-is since the inner `except Exception: pass` correctly protects the re-raise. The primary fix is CR-02 (catching all exceptions in tool handlers so they rarely reach the lifespan).

### WR-05: Observability Decorator Uses Fragile Positional Argument Indexing

**File:** `src/mcp_server_python_docs/services/observability.py:70-76`
**Issue:** The `log_tool_call` decorator extracts the `version` parameter by checking `args[1]` (the second positional argument after `self`). This works for the current method signatures of `SearchService.search(query, version, kind, max_results)` and `ContentService.get_docs(slug, version, anchor, ...)`, but will silently extract the wrong value if method signatures are reordered. For `VersionService.list_versions()`, which takes no arguments, `args` is empty so the fallback to `"default"` works correctly.

The `_last_resolution` and `_last_synonym_expanded` state access via `hasattr(self, ...)` is similarly fragile -- it couples the decorator to internal state of the decorated class. If these attributes are renamed, logging degrades silently.

**Fix:** Use `inspect.signature` to bind arguments by name rather than position:

```python
import inspect

sig = inspect.signature(fn)
bound = sig.bind(self, *args, **kwargs)
bound.apply_defaults()
version_val = bound.arguments.get("version")
```

### WR-06: Ingestion Marks Version as Succeeded Even When Content Ingestion Fails

**File:** `src/mcp_server_python_docs/__main__.py:244-246, 275-276`
**Issue:** In the `build-index` command, if `sphinx-build` fails (line 234, `result.returncode != 0`), the code sets `any_version_succeeded = True` at line 245 and continues. Similarly, if a `CalledProcessError` occurs during the subprocess pipeline (line 271), it still sets `any_version_succeeded = True` at line 276. This means a version that only has symbols (from objects.inv) but no sections, examples, or full content is considered "succeeded." The smoke test at publish time checks for `sections >= 50` and `documents >= 10`, which would catch a complete failure -- but a partial failure (one version succeeds fully, another only gets symbols) would pass smoke tests while leaving one version degraded.

**Fix:** Track per-version success state more granularly. At minimum, log a clear warning that distinguishes "symbols only" from "full ingestion":

```python
if result.returncode != 0:
    logger.warning(
        "Version %s has SYMBOLS ONLY (sphinx-build failed). "
        "search_docs will work but get_docs will return empty pages.",
        version,
    )
    any_version_succeeded = True  # symbols still usable
    continue
```

The current code already logs a warning but the `any_version_succeeded = True` flag conflates "partially usable" with "fully succeeded."

## Info

### IN-01: f-string Logging in ingestion/inventory.py

**File:** `src/mcp_server_python_docs/ingestion/inventory.py:124, 126, 177`
**Issue:** Three `logger.info()` calls use f-strings instead of lazy `%`-style formatting. F-string arguments are always evaluated, even when the log level is higher than INFO. For ingestion code that runs infrequently this has negligible impact, but it violates Python logging best practices.

**Fix:**
```python
logger.info("Downloading %s...", url)
logger.info("Downloaded %d inventory objects", len(inv.objects))
logger.info("Ingested %d symbols for Python %s", count, version)
```

### IN-02: Missing idempotentHint in ToolAnnotations

**File:** `src/mcp_server_python_docs/server.py:113-117`
**Issue:** The shared `_TOOL_ANNOTATIONS` includes `readOnlyHint=True`, `destructiveHint=False`, and `openWorldHint=False`, but omits `idempotentHint=True`. All three tools are read-only queries against an immutable index and are idempotent by definition. Adding this hint allows MCP clients to optimize retry behavior.

**Fix:**
```python
_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
    idempotentHint=True,
)
```

### IN-03: Missing py.typed Marker File

**File:** `src/mcp_server_python_docs/` (directory)
**Issue:** The package does not include a `py.typed` marker file (PEP 561). Without it, downstream type checkers (mypy, pyright) will not analyze the package's type annotations. All benchmark MCP server projects include this marker.

**Fix:** Create an empty `src/mcp_server_python_docs/py.typed` file and ensure it is included in the wheel via `pyproject.toml`.

### IN-04: services/__init__.py Docstring Inaccuracy

**File:** `src/mcp_server_python_docs/services/__init__.py:5`
**Issue:** The docstring states "No service touches SQL directly (except through storage/retrieval functions)." but all three services execute SQL directly via `self._db.execute()`: `SearchService._symbol_exists` (search.py:51), `ContentService.get_docs` (content.py:51, 72, 92), and `VersionService.list_versions` (version.py:28). The parenthetical "(except through storage/retrieval functions)" was likely added as a retroactive qualifier but the main claim is still misleading.

**Fix:** Update the docstring to accurately reflect the architecture:

```python
"""Service layer -- SearchService, ContentService, VersionService.

Services sit between FastMCP tool handlers and the retrieval/storage layers.
Dependency rule: server -> services -> retrieval/storage.
Services receive sqlite3.Connection via constructor and execute queries directly.
No service imports MCP types.
"""
```

### IN-05: AppContext Service Fields Typed as Optional but Never Actually None at Runtime

**File:** `src/mcp_server_python_docs/app_context.py:27-29`
**Issue:** `search_service`, `content_service`, and `version_service` are typed as `ServiceType | None = None`. The lifespan in `server.py:85-97` always sets all three before yielding, so they are never `None` at runtime. The `Optional` typing adds unnecessary `None` checks to static analysis and creates a false impression that callers should handle `None`. Tool handlers at `server.py:139-141` access `app_ctx.search_service.search(...)` without `None` checks, relying on the runtime invariant.

**Fix:** Either make the fields non-optional (requires changing the dataclass to accept services in the constructor), or add a comment explaining the invariant. A cleaner approach:

```python
@dataclass
class AppContext:
    db: sqlite3.Connection
    index_path: Path
    search_service: SearchService
    content_service: ContentService
    version_service: VersionService
    synonyms: dict[str, list[str]] = field(default_factory=dict)
```

This requires the TYPE_CHECKING imports to become real imports (since the fields are no longer Optional and the class must be constructible), but it would make the type system enforce the runtime invariant.

---

_Reviewed: 2026-04-16T12:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
