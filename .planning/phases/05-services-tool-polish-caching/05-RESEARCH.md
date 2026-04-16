# Phase 5: Services, Tool Polish & Caching - Research

**Researched:** 2026-04-16
**Status:** Complete

## Research Questions

### RQ-1: How should the 3-service layer (SearchService, ContentService, VersionService) be structured?

**Finding:** The build guide (section 4) prescribes a 3-service architecture:
- `services/search.py` — handles `search_docs` for all `kind` values
- `services/content.py` — handles `get_docs` for page and section retrieval
- `services/version.py` — trivial, lists `doc_sets` rows

**Current state:** No `services/` directory exists. The search logic currently lives inline in `server.py` as `_do_search()` (lines 116-159). The tool handler for `search_docs` directly calls retrieval functions. Phase 5 must extract this into `SearchService`, add `ContentService` and `VersionService`, and have `server.py` delegate to services.

**Dependency rule (section 4):** `server -> services -> retrieval/storage`. No service touches SQL directly. No tool handler bypasses services. No storage code imports MCP types.

**Implication:** Services receive `sqlite3.Connection` and `synonyms` dict via constructor (from AppContext). They call retrieval functions and storage queries. Tool handlers in `server.py` become thin wrappers that extract context and delegate.

### RQ-2: What is the current state of tool registration and what needs to change?

**Finding:** Currently only `search_docs` is registered as an MCP tool in `server.py`. Phase 5 must register:
- `get_docs` — with same `ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)` + `_meta = {"anthropic/maxResultSizeChars": 16000}` (SRVR-03, SRVR-07)
- `list_versions` — with same annotations (SRVR-04)

**Models already exist:** `GetDocsInput`, `GetDocsResult`, `ListVersionsResult`, `VersionInfo` are all defined in `models.py`.

**_meta pattern:** The MCP SDK supports `_meta` in tool definitions. Per build guide and SRVR-07, `get_docs` needs `_meta = {"anthropic/maxResultSizeChars": 16000}`. This is passed to the client so it knows the expected max size. The FastMCP `@mcp.tool()` decorator accepts a `metadata` parameter.

### RQ-3: How should structured logging (OPS-01, OPS-02) be implemented?

**Requirements:**
- Every tool call logs to stderr: tool name, version, latency_ms, result_count, truncation flag, symbol-resolution path (exact/fuzzy/fts), synonym_expansion (yes/no)
- Logs are structured key=value (logfmt) per Phase 1 auto-decision D-10
- Implemented as per-service-method decorators (OPS-03), NOT FastMCP middleware

**Design:** Create a decorator in a new `observability.py` module (or within services) that:
1. Records start time
2. Calls the wrapped service method
3. Logs structured key=value line to stderr with all fields
4. Returns the result

The decorator wraps service methods like `SearchService.search()`, `ContentService.get_docs()`, `VersionService.list_versions()`.

**Logfmt format example:**
```
tool=search_docs version=3.13 latency_ms=12 result_count=5 truncated=false resolution=fts synonym_expansion=yes
```

### RQ-4: How should LRU caching (OPS-04, OPS-05) be implemented?

**Build guide section 12 prescribes:**
```python
from functools import lru_cache

@lru_cache(maxsize=512)
def get_section_cached(section_id: int) -> Section: ...

@lru_cache(maxsize=128)
def resolve_symbol_cached(qualified_name: str, version: str) -> Symbol: ...
```

**Key constraints:**
- Process-lifetime scoped (OPS-05) — no TTL, no invalidation
- Users restart on rebuild (documented in PUBL-05)
- `get_section_cached(section_id)` with maxsize=512 — caches content retrieval
- `resolve_symbol_cached(qualified_name, version)` with maxsize=128 — caches symbol lookups

**Implementation:** Place cached functions in a module accessible to services. They take simple hashable args (int, str) and return data. The `ContentService` calls `get_section_cached()` for repeat section reads. `SearchService` calls `resolve_symbol_cached()` for repeat symbol lookups.

**Note:** `lru_cache` requires hashable arguments. `sqlite3.Connection` is not hashable, so the cached functions must accept the connection as module-level state or through a closure, not as a parameter. The cleanest pattern is to have the service pass the connection explicitly and the cache wrapper close over it during construction.

### RQ-5: How should validate-corpus CLI work?

**Requirements (PUBL-07):**
- `mcp-server-python-docs validate-corpus` runs the same smoke tests as Phase 4's publish pipeline
- Runs against the currently-live `index.db`
- Exits 0 on pass, non-zero on fail

**Current state:** A stub exists in `__main__.py` (line 281-284):
```python
@main.command("validate-corpus")
def validate_corpus() -> None:
    """Validate the current index (stub for Phase 5)."""
    logger.info("validate-corpus: not yet implemented (Phase 5)")
```

**Implementation:** Import and call `run_smoke_tests()` from `ingestion/publish.py` against `get_index_path()`. Print results to stderr, exit 0/1.

### RQ-6: What is ContentService.get_docs() logic?

**Build guide section 3, Tool 2:**
- When `anchor` is provided, returns just that section
- When omitted, returns the page with truncation/pagination
- Uses `apply_budget()` for truncation

**Required SQL queries:**
1. Resolve version (default to latest if None)
2. Find document by slug + version
3. If anchor: find section by (document_id, anchor), return section content
4. If no anchor: concatenate all sections of the document in ordinal order
5. Apply `apply_budget(text, max_chars, start_index)`
6. Return `GetDocsResult` with truncation info

## Existing Code Patterns

### AppContext Pattern
Services receive dependencies via `AppContext` (dataclass with `db`, `index_path`, `synonyms`). The `ctx.request_context.lifespan_context` accessor in tool handlers provides the AppContext instance.

### Error Handling Pattern
Domain errors (`VersionNotFoundError`, `SymbolNotFoundError`, `PageNotFoundError`) are caught in tool handlers and converted to `ToolError` for MCP `isError: true` responses (SRVR-08).

### Test Pattern
Tests use `test_db` and `populated_db` fixtures from conftest.py. Services tests should follow the same pattern.

## Validation Architecture

### Structural Checks
- Service module files exist at expected paths
- Service classes have expected method signatures
- Tool registration includes all 3 tools with correct annotations
- Logging decorator produces structured output
- LRU cache hit on repeat calls verified via `cache_info()`

### Behavioral Checks
- `get_docs` returns content for a known slug/anchor
- `list_versions` returns doc_sets data
- `validate-corpus` exits 0 against a valid test DB
- Log lines contain all required fields

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `_meta` not supported by FastMCP decorator API | SRVR-07 blocked | Check FastMCP source; may need to set it at the tool registration level or via `mcp.tool()` kwargs |
| `lru_cache` with `sqlite3.Connection` argument | Cache misses on every call (unhashable) | Use closure pattern: cache functions close over the connection, take only hashable keys |
| `get_docs` page-level retrieval requires joining all sections | Slow for large pages | `apply_budget` limits output; the join is only on sections for one document |

## RESEARCH COMPLETE
