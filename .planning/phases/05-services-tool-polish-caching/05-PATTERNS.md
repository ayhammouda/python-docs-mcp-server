# Phase 5: Services, Tool Polish & Caching - Pattern Map

**Mapped:** 2026-04-16

## Files to Create/Modify

### New Files

| File | Role | Closest Analog | Key Pattern |
|------|------|----------------|-------------|
| `src/mcp_server_python_docs/services/__init__.py` | Package init | `storage/__init__.py` | Empty init |
| `src/mcp_server_python_docs/services/search.py` | Search service | `server.py:_do_search()` | Extract `_do_search()` logic into `SearchService` class |
| `src/mcp_server_python_docs/services/content.py` | Content service | `retrieval/budget.py:apply_budget()` | New class using retrieval layer for doc retrieval |
| `src/mcp_server_python_docs/services/version.py` | Version service | `server.py` db queries | New class querying `doc_sets` table |
| `src/mcp_server_python_docs/services/observability.py` | Logging decorator | N/A (new pattern) | Decorator wrapping service methods with logfmt output |
| `src/mcp_server_python_docs/services/cache.py` | LRU cache wrappers | Build guide section 12 | `@lru_cache` on data-fetching functions |
| `tests/test_services.py` | Service tests | `tests/test_retrieval.py` | pytest with test_db/populated_db fixtures |

### Modified Files

| File | Change | Current State |
|------|--------|---------------|
| `src/mcp_server_python_docs/server.py` | Register get_docs + list_versions tools; delegate to services | Has search_docs tool + inline `_do_search()` |
| `src/mcp_server_python_docs/__main__.py` | Implement validate-corpus CLI | Has stub at line 281-284 |
| `src/mcp_server_python_docs/app_context.py` | May add service instances | Currently has db, index_path, synonyms |

## Pattern Excerpts

### Existing Tool Registration Pattern (server.py)
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
    app_ctx: AppContext = ctx.request_context.lifespan_context
    db = app_ctx.db
    try:
        return _do_search(db, app_ctx.synonyms, query, version, kind, max_results)
    except VersionNotFoundError as e:
        raise ToolError(str(e))
```

### Existing Error Handling Pattern
```python
try:
    return _do_search(...)
except VersionNotFoundError as e:
    raise ToolError(str(e))
except SymbolNotFoundError as e:
    raise ToolError(str(e))
except PageNotFoundError as e:
    raise ToolError(str(e))
except DocsServerError as e:
    raise ToolError(str(e))
```

### Smoke Test Pattern (publish.py)
```python
def run_smoke_tests(db_path: Path) -> tuple[bool, list[str]]:
    messages: list[str] = []
    passed = True
    conn = get_readonly_connection(db_path)
    # ... checks ...
    return passed, messages
```

### Test DB Fixture Pattern (conftest.py)
```python
@pytest.fixture
def populated_db(test_db):
    test_db.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, ...)"
    )
    test_db.commit()
    return test_db
```

## Data Flow

```
Tool handler (server.py)
  -> extracts AppContext from ctx.request_context.lifespan_context
  -> calls Service method (SearchService.search / ContentService.get_docs / VersionService.list)
    -> Service calls retrieval functions (query.py, ranker.py, budget.py)
    -> Service calls storage queries (db.py)
    -> Observability decorator logs structured output
    -> Cache decorator wraps hot-read functions
  <- returns Pydantic model result
  -> ToolError on domain errors
```

## PATTERN MAPPING COMPLETE
