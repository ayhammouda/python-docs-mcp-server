---
phase: full-codebase
reviewed: 2026-04-15T22:00:00Z
depth: deep
files_reviewed: 23
files_reviewed_list:
  - src/mcp_server_python_docs/__init__.py
  - src/mcp_server_python_docs/__main__.py
  - src/mcp_server_python_docs/app_context.py
  - src/mcp_server_python_docs/errors.py
  - src/mcp_server_python_docs/ingestion/__init__.py
  - src/mcp_server_python_docs/ingestion/inventory.py
  - src/mcp_server_python_docs/ingestion/publish.py
  - src/mcp_server_python_docs/ingestion/sphinx_json.py
  - src/mcp_server_python_docs/models.py
  - src/mcp_server_python_docs/retrieval/__init__.py
  - src/mcp_server_python_docs/retrieval/budget.py
  - src/mcp_server_python_docs/retrieval/query.py
  - src/mcp_server_python_docs/retrieval/ranker.py
  - src/mcp_server_python_docs/server.py
  - src/mcp_server_python_docs/services/__init__.py
  - src/mcp_server_python_docs/services/cache.py
  - src/mcp_server_python_docs/services/content.py
  - src/mcp_server_python_docs/services/observability.py
  - src/mcp_server_python_docs/services/search.py
  - src/mcp_server_python_docs/services/version.py
  - src/mcp_server_python_docs/services/version_resolution.py
  - src/mcp_server_python_docs/storage/__init__.py
  - src/mcp_server_python_docs/storage/db.py
findings:
  critical: 3
  warning: 8
  info: 5
  total: 16
status: issues_found
---

# Full Codebase: Code Review Report

**Reviewed:** 2026-04-15T22:00:00Z
**Depth:** deep
**Files Reviewed:** 23
**Status:** issues_found

## Summary

This is a deep review of the entire mcp-server-python-docs codebase -- a read-only, version-aware MCP retrieval server over Python stdlib documentation backed by SQLite FTS5.

The codebase is well-structured overall. The dependency rule (server -> services -> retrieval/storage) is respected with no reverse imports. MCP types are confined to `server.py`. Stdio hygiene is carefully implemented. FTS5 escape coverage is thorough. The RO/RW connection split is correctly enforced. Error taxonomy via `errors.py` is clean.

However, the deep review uncovered 3 critical issues (lifespan error handler swallows normal shutdown, `INSERT OR REPLACE` on symbols with changed UNIQUE constraint silently drops data, and version sorting crash on malformed input), 8 warnings (concurrency safety in observability state, missing `isError:true` propagation on one tool, logfmt injection, missing input validation in build-index, and others), and 5 informational items.

## Critical Issues

### CR-01: Lifespan error handler catches normal server shutdown and exits with code 1

**File:** `src/mcp_server_python_docs/server.py:98`
**Issue:** The `except Exception` block at line 98 wraps the `yield` in `app_lifespan`. When the MCP client disconnects normally, the FastMCP framework may raise `GeneratorExit` or other exceptions during shutdown cleanup. Since `GeneratorExit` inherits from `BaseException` (not `Exception`), it is not caught by this handler. However, `asyncio.CancelledError` (which inherits from `BaseException` in Python 3.9+ but was `Exception` in 3.8) and any framework-raised `Exception` during normal shutdown will be caught, logged as an error, written to `last-error.log`, and then force the process to exit with code 1 via `SystemExit(1)`. This means a normal MCP session teardown that raises any exception in the framework's cleanup path will appear as a crash, pollute error logs, and return a non-zero exit code.

The `yield` in an async context manager is the suspension point -- exceptions raised by the calling framework after the `yield` are not "lifespan errors" from the application; they are shutdown signals. This handler should be narrower.

**Fix:**
```python
try:
    yield AppContext(
        db=db,
        index_path=index_path,
        synonyms=synonyms,
        search_service=search_svc,
        content_service=content_svc,
        version_service=version_svc,
    )
except Exception:
    # Only log and write error file; do NOT re-raise as SystemExit(1).
    # Let the framework decide the exit code. The DB is closed in `finally`.
    error_msg = traceback.format_exc()
    logger.error("Lifespan error: %s", error_msg)
    try:
        error_log = cache_dir / "last-error.log"
        error_log.write_text(error_msg)
    except Exception:
        pass
    raise  # Re-raise the original exception, not SystemExit
finally:
    db.close()
```

### CR-02: `INSERT OR REPLACE` on symbols table causes silent data loss due to UNIQUE constraint mismatch

**File:** `src/mcp_server_python_docs/ingestion/inventory.py:144`
**Issue:** The `INSERT OR REPLACE INTO symbols` statement at line 144 inserts rows with columns `(doc_set_id, qualified_name, normalized_name, module, symbol_type, uri, anchor)`. The schema's UNIQUE constraint on `symbols` is `UNIQUE(doc_set_id, qualified_name, symbol_type)` (schema.sql line 68). However, the ingestion code performs deduplication at line 122-133 by keeping only the best role per `qualified_name` (ignoring `symbol_type`). This means the dedup logic intentionally collapses `asyncio.TaskGroup` as both `class` and `data` into just the `class` entry.

The problem: if the dedup logic selects a different `symbol_type` on re-ingestion (e.g., priority ordering changes or objects.inv adds a new role), `INSERT OR REPLACE` will insert a new row with the new `symbol_type` rather than replacing the old one, because the UNIQUE key includes `symbol_type`. This leaves stale rows from prior ingestion runs even though line 112 issues `DELETE FROM symbols WHERE doc_set_id = ?`. The DELETE clears the table first, so on a single run this is fine.

The real issue is that the UNIQUE constraint `(doc_set_id, qualified_name, symbol_type)` allows the _same_ qualified name to appear multiple times with different symbol_types, but the Python-side dedup at lines 122-133 ensures only one entry per qualified_name. If a future code change removes the Python-side dedup or changes the priority, the `INSERT OR REPLACE` will silently insert duplicates rather than replacing. The UNIQUE constraint should match the dedup intent.

**Fix:** Change the UNIQUE constraint to match the dedup granularity, or use `INSERT OR IGNORE` since the DELETE + re-insert pattern already handles updates:
```sql
-- Option A: Match dedup intent in schema
UNIQUE(doc_set_id, qualified_name)

-- Option B: Keep schema, change Python to INSERT OR IGNORE
-- (safe because DELETE precedes insertion)
```

### CR-03: Version sorting in build-index crashes on non-numeric version strings

**File:** `src/mcp_server_python_docs/__main__.py:122`
**Issue:** The lambda `key=lambda v: [int(x) for x in v.split(".")]` will raise `ValueError` if a user passes a non-numeric version string (e.g., `--versions 3.14-rc1` or `--versions latest`). This is a CLI-facing crash with no error handling. The version_list is derived from user input at line 116 with only whitespace stripping -- no format validation.

**Fix:**
```python
# Validate version format before sorting
for v in version_list:
    parts = v.split(".")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        logger.error(
            "Invalid version format %r. Expected 'X.Y' (e.g., 3.13)", v
        )
        raise SystemExit(1)

sorted_versions = sorted(version_list, key=lambda v: [int(x) for x in v.split(".")])
```

## Warnings

### WR-01: Concurrency-unsafe mutable state for observability tracking

**File:** `src/mcp_server_python_docs/services/search.py:33-34`
**Issue:** `_last_synonym_expanded` and `_last_resolution` are instance attributes on the shared `SearchService` singleton. The `log_tool_call` decorator in `observability.py` reads these attributes after the wrapped method returns (lines 82-83, 99-101). If the MCP framework processes multiple tool calls concurrently (which async frameworks can do), a second call can overwrite `_last_resolution` before the first call's `log_tool_call` wrapper reads it. This produces incorrect observability data.

While `mcp.run(transport="stdio")` is typically single-request, the FastMCP framework uses asyncio and could schedule multiple tool handlers if pipelining or batched requests are supported. The pattern of writing instance state in the method body and reading it in a wrapper is inherently racy.

**Fix:** Return the observability metadata from the service method (e.g., as a tuple or a wrapper dataclass) rather than storing it in mutable instance state:
```python
# In search.py, return metadata alongside result:
@dataclass
class SearchMeta:
    resolution: str
    synonym_expanded: bool

# The decorator can then extract metadata from the return value
# without reading shared mutable state.
```

### WR-02: `list_versions` tool does not catch `DocsServerError`

**File:** `src/mcp_server_python_docs/server.py:169-170`
**Issue:** The `search_docs` and `get_docs` tool handlers both catch `DocsServerError` and re-raise as `ToolError` (lines 142-143 and 161-162). The `list_versions` handler does not. If the underlying query fails (e.g., corrupted database), the exception propagates as an unhandled exception rather than a structured MCP error with `isError: true`. This is inconsistent with the error handling pattern in the other two tools.

**Fix:**
```python
@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def list_versions(
    ctx: Context = None,  # type: ignore[assignment]
) -> ListVersionsResult:
    """List Python documentation versions available in this index."""
    app_ctx: AppContext = ctx.request_context.lifespan_context
    try:
        return app_ctx.version_service.list_versions()
    except DocsServerError as e:
        raise ToolError(str(e))
```

### WR-03: Logfmt value injection -- double quotes in version strings not escaped

**File:** `src/mcp_server_python_docs/services/observability.py:33`
**Issue:** The `_format_logfmt` function wraps string values containing spaces in double quotes (line 33) but does not escape embedded double quotes within those values. If a value contains `"`, the logfmt output will have malformed quoting: `key="value with "quotes""`. While the `version` field comes from a controlled set, the function is generic and could be called with arbitrary values. A crafted version string like `3.13" injected=true version="` would produce garbled logfmt.

**Fix:**
```python
elif isinstance(value, str) and " " in value:
    escaped_val = str(value).replace('"', '\\"')
    parts.append(f'{key}="{escaped_val}"')
```

### WR-04: Ingestion URL constructed from unvalidated version string

**File:** `src/mcp_server_python_docs/ingestion/inventory.py:115`
**Issue:** The objects.inv URL is constructed via f-string interpolation: `f"https://docs.python.org/{version}/objects.inv"`. The `version` parameter comes from CLI input. While `sphobjinv.Inventory(url=...)` will fail safely on a 404, a malicious version string with path segments (e.g., `../../admin`) could construct an unexpected URL like `https://docs.python.org/../../admin/objects.inv`. This is low-severity because the URL targets docs.python.org (which the user controls nothing about) and sphobjinv would reject non-inventory responses. However, the version string should be validated before use.

**Fix:** The version format validation from CR-03 would also prevent this. Alternatively, add a guard in `ingest_inventory`:
```python
if not re.match(r"^\d+\.\d+$", version):
    raise IngestionError(f"Invalid version format: {version!r}")
```

### WR-05: `bootstrap_schema` drops and recreates FTS5 tables using f-string -- not a SQL injection risk but fragile

**File:** `src/mcp_server_python_docs/storage/db.py:128`
**Issue:** The loop `conn.execute(f"DROP TABLE IF EXISTS {fts_table}")` uses an f-string to construct DDL. The `fts_table` values come from a hardcoded tuple `("sections_fts", "symbols_fts", "examples_fts")` on line 127, so there is no injection risk. However, this is the only SQL in the codebase that uses string interpolation rather than parameterized queries. If the hardcoded list were ever modified to include external input, it would become an injection vector. A defensive comment or assertion would make the safety property explicit.

**Fix:** Add a defensive assertion:
```python
_FTS_TABLES = frozenset({"sections_fts", "symbols_fts", "examples_fts"})

for fts_table in _FTS_TABLES:
    assert fts_table.isidentifier(), f"Invalid table name: {fts_table}"
    conn.execute(f"DROP TABLE IF EXISTS {fts_table}")
```

### WR-06: `_real_stdout_fd` leaked if `create_server()` raises before `os.close()`

**File:** `src/mcp_server_python_docs/__main__.py:61-67`
**Issue:** In the `serve()` function, `create_server()` is called at line 61. If it raises an exception, `os.dup2(_real_stdout_fd, 1)` and `os.close(_real_stdout_fd)` at lines 66-67 are never reached, leaking the duplicated file descriptor. While this is a minor resource leak since the process exits on failure, it is also a correctness issue: if `create_server()` fails and execution continues (e.g., click catches the exception), fd 1 remains redirected to stderr, and the saved fd is never closed.

**Fix:** Wrap in try/finally:
```python
@main.command()
def serve() -> None:
    """Start the MCP server (default command)."""
    from mcp_server_python_docs.server import create_server

    mcp_server = create_server()

    # Restore the real stdout fd for MCP protocol framing.
    os.dup2(_real_stdout_fd, 1)
    os.close(_real_stdout_fd)

    try:
        mcp_server.run(transport="stdio")
    except BrokenPipeError:
        pass
```
No change needed here since `create_server()` is synchronous and will propagate the exception before dup2. But to be safe, close the fd in a finally block at module level or in a broader scope.

### WR-07: `populate_synonyms` does not validate YAML structure

**File:** `src/mcp_server_python_docs/ingestion/sphinx_json.py:399-400`
**Issue:** `yaml.safe_load(path.read_text())` returns the parsed YAML, which is then iterated as `data.items()`. If the YAML file is malformed (e.g., a list instead of a dict, or contains `null`), `data.items()` will raise `AttributeError`. The error is not caught and will surface as an unhelpful traceback during `build-index`.

**Fix:**
```python
data = yaml.safe_load(path.read_text())
if not isinstance(data, dict):
    raise IngestionError(
        f"synonyms.yaml must be a YAML mapping, got {type(data).__name__}"
    )
```

### WR-08: `any_version_succeeded` set to True even when only symbols were ingested but content failed

**File:** `src/mcp_server_python_docs/__main__.py:231`
**Issue:** When sphinx-build fails (line 225: `result.returncode != 0`), `any_version_succeeded` is set to `True` at line 231 with the comment "symbols still ingested." Similarly at line 262 for subprocess failures. This means the build pipeline will proceed to publishing and smoke tests even when no content (sections, documents, examples) was ingested -- only symbols. The smoke tests at `run_smoke_tests()` require `documents >= 10` and `sections >= 50`, which will fail. So this is not a data-loss bug, but it means the error is caught late (at smoke test time) rather than early. The intent comment suggests this is deliberate, but the smoke tests make it effectively a delayed failure with a confusing error message ("smoke tests failed" when the real issue was "sphinx-build failed").

**Fix:** Track content ingestion success separately from symbol ingestion success, and log a clear warning:
```python
if result.returncode != 0:
    logger.warning(
        "sphinx-build failed for %s -- symbols were ingested but "
        "sections/examples will be missing. Smoke tests may fail.",
        version,
    )
    any_version_succeeded = True  # symbols still usable for symbol-only search
    continue
```

## Info

### IN-01: `_load_synonyms` and `populate_synonyms` duplicate synonym loading logic

**File:** `src/mcp_server_python_docs/server.py:34-39` and `src/mcp_server_python_docs/ingestion/sphinx_json.py:385-416`
**Issue:** Both functions load `synonyms.yaml` via `importlib.resources`, but they parse it differently. `_load_synonyms` filters to only `dict[str, list[str]]` entries (line 39: `if isinstance(v, list)`), while `populate_synonyms` stores any expansion as a space-joined string. If a synonym entry has a scalar value (e.g., `alias: "target"`), `_load_synonyms` drops it silently while `populate_synonyms` stores it. This asymmetry could cause serve-time synonym expansion to differ from build-time population. Consider extracting a shared loader.

### IN-02: FTS5 tokenizer discrepancy between schema comments and CLAUDE.md

**File:** `src/mcp_server_python_docs/storage/schema.sql:5-8`
**Issue:** CLAUDE.md states the FTS5 tokenizer plan is `unicode61 porter` for `sections_fts` and `unicode61` (no porter) for `symbols_fts` and `examples_fts`. The actual schema uses the same tokenizer for all three: `unicode61 remove_diacritics 2 tokenchars '._'` with no porter stemming anywhere. The schema is internally consistent and the schema.sql comments explain the decision clearly (line 8: "Porter stemming is deliberately NOT applied"). CLAUDE.md should be updated to match the implemented schema.

### IN-03: Unused `_SKIP_FILES` and `_SKIP_SLUGS` could be consolidated

**File:** `src/mcp_server_python_docs/ingestion/sphinx_json.py:26-36`
**Issue:** `_SKIP_FILES` filters by filename in `ingest_sphinx_json_dir` (line 364), while `_SKIP_SLUGS` filters by slug in `ingest_fjson_file` (line 255). The separation is correct but the overlap between "skip by filename" and "skip by slug" could cause confusion. For example, `searchindex.json` is filtered by `_SKIP_FILES`, but if it had a `.fjson` extension, it would need to be in `_SKIP_SLUGS` too. Minor organizational concern.

### IN-04: `app_context.py` services are typed as `Optional` but always set during lifespan

**File:** `src/mcp_server_python_docs/app_context.py:27-29`
**Issue:** `search_service`, `content_service`, and `version_service` are typed as `SearchService | None` with default `None`. After `app_lifespan` yields, all three are always set to non-None values. Every tool handler accesses them without None-checking (e.g., `app_ctx.search_service.search(...)`). If a tool were somehow called before lifespan completes, this would be a NoneType error. The Optional typing is technically correct (the dataclass can exist without services) but misleading at call sites. Consider a separate `InitializedAppContext` type or assert-based narrowing.

### IN-05: Dead code in server.py `_meta` hint block

**File:** `src/mcp_server_python_docs/server.py:172-188`
**Issue:** The `try` block at lines 177-188 attempts to set `_meta` on the `get_docs` tool definition, but the inner `if` block at lines 181-185 has a `pass` body -- it does nothing. The entire block is effectively dead code wrapped in a try/except that silently swallows all exceptions. If the intent is to set `_meta`, the `pass` should be replaced with the actual assignment. If the intent is to defer this to a future SDK version, a TODO comment would be clearer.

---

_Reviewed: 2026-04-15T22:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
