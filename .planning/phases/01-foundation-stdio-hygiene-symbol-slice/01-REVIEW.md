---
status: findings
phase: "01"
phase_name: foundation-stdio-hygiene-symbol-slice
depth: standard
files_reviewed: 17
findings:
  critical: 1
  warning: 5
  info: 4
  total: 10
reviewed_at: "2026-04-15"
---

# Phase 01 Code Review

**Depth:** standard
**Files reviewed:** 17 source/test files
**Scope:** git diff (4bb90bf..HEAD), excluding planning artifacts

## Critical

### CR-01: FTS5 check fails on read-only connection (server.py:72)

**File:** `src/mcp_server_python_docs/server.py` line 72
**Impact:** Server startup always crashes with misleading error when index.db exists

`_assert_fts5(db)` calls `assert_fts5_available()` which attempts `CREATE VIRTUAL TABLE` on a read-only SQLite connection (`?mode=ro`). This always throws `OperationalError: attempt to write a readonly database`, which is then caught and re-raised as `FTS5UnavailableError` -- telling the user FTS5 is missing when the real problem is the connection is read-only.

**Reproduction:**
```python
conn_ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
conn_ro.execute("CREATE VIRTUAL TABLE _fts5_check USING fts5(x)")
# -> OperationalError: attempt to write a readonly database
```

**Fix:** Use `PRAGMA compile_options` to check for FTS5 on read-only connections:
```python
def assert_fts5_available(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_check USING fts5(x)")
        conn.execute("DROP TABLE _fts5_check")
    except sqlite3.OperationalError as e:
        if "readonly" in str(e).lower():
            # Read-only connection: check compile_options instead
            opts = [r[0] for r in conn.execute("PRAGMA compile_options")]
            if "ENABLE_FTS5" in opts:
                return  # FTS5 is available
        # Fall through to error
        raise FTS5UnavailableError(...) from e
```

## Warnings

### WR-01: search_docs ignores version parameter in SQL query (server.py:122-129)

**File:** `src/mcp_server_python_docs/server.py` lines 122-129
**Impact:** Returns symbols from all indexed versions when multiple versions exist

The SQL `WHERE` clause does not filter by `doc_set_id` based on the `version` parameter. If both 3.12 and 3.13 are indexed, `search_docs(query="json.dumps", version="3.13")` returns symbols from both versions. The version is only used in the output label (line 152: `hit_version = version or "3.13"`).

**Fix:** Join with `doc_sets` and filter by version:
```python
cursor = db.execute(
    "SELECT s.qualified_name, s.symbol_type, s.uri, s.anchor, d.version "
    "FROM symbols s JOIN doc_sets d ON s.doc_set_id = d.id "
    "WHERE (s.qualified_name = ? OR s.qualified_name LIKE ?) "
    "AND (? IS NULL OR d.version = ?) "
    "ORDER BY CASE WHEN s.qualified_name = ? THEN 0 ELSE 1 END "
    "LIMIT ?",
    (query, f"%{query}%", version, version, query, max_results),
)
```

### WR-02: LIKE wildcards in user input not escaped (server.py:126)

**File:** `src/mcp_server_python_docs/server.py` line 126
**Impact:** User queries containing `%` or `_` produce unexpected LIKE matches

The pattern `f"%{query}%"` passes user input directly into a LIKE pattern. While the parameterized query prevents SQL injection, the `%` and `_` characters in user input act as LIKE wildcards. A query for `os_path` would match `os.path`, `osXpath`, etc.

**Fix:** Escape LIKE wildcards before interpolation:
```python
escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
cursor = db.execute(
    "... WHERE qualified_name LIKE ? ESCAPE '\\' ...",
    (f"%{escaped}%",),
)
```

### WR-03: ToolAnnotations passed as dict instead of Pydantic model (server.py:98-102)

**File:** `src/mcp_server_python_docs/server.py` lines 98-102
**Impact:** Pyright type error; may break with stricter validation in future mcp SDK versions

The `annotations` parameter expects a `ToolAnnotations` Pydantic model, but a plain dict is passed. While this works at runtime (Pydantic coercion), pyright flags it as `reportArgumentType` and it could break if the SDK adds runtime type validation.

**Fix:**
```python
from mcp.types import ToolAnnotations

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False,
    )
)
```

### WR-04: Unused imports in server.py (F401)

**File:** `src/mcp_server_python_docs/server.py` line 19
**Impact:** Dead code; ruff reports `F401`

`FTS5UnavailableError` and `IndexNotBuiltError` are imported but never used directly in `server.py`. The FTS5 check uses a deferred import inside `_assert_fts5()`.

**Fix:** Remove the unused imports:
```python
# Remove this line:
from mcp_server_python_docs.errors import FTS5UnavailableError, IndexNotBuiltError
```

### WR-05: build-index accepts empty version strings (__main__.py:85)

**File:** `src/mcp_server_python_docs/__main__.py` line 85
**Impact:** Empty strings passed to `ingest_inventory()` cause HTTP 404 or malformed URL

`--versions "3.13, , "` splits to `["3.13", "", ""]`. The empty strings propagate to `ingest_inventory()` which constructs `https://docs.python.org//objects.inv`.

**Fix:** Filter empty strings:
```python
version_list = [v.strip() for v in versions.split(",") if v.strip()]
if not version_list:
    logger.error("No valid versions specified")
    raise SystemExit(1)
```

## Info

### IR-01: Import ordering violations (ruff E402, I001)

**Files:** `src/mcp_server_python_docs/__main__.py`, `src/mcp_server_python_docs/models.py`
**Impact:** Style; ruff reports E402 (module-level import not at top) and I001 (unsorted imports)

The E402 in `__main__.py` is intentional (stdio hygiene requires early fd redirect before imports). Should be suppressed with `# noqa: E402`. The I001 in `build_index` and `models.py` are fixable with `ruff --fix`.

### IR-02: Unused imports in test files (ruff F401)

**Files:** `tests/test_phase1_integration.py` (sqlite3), `tests/test_stdio_hygiene.py` (pytest), `tests/test_synonyms.py` (pytest)
**Impact:** Style; dead imports

### IR-03: Line too long in models.py (ruff E501)

**File:** `src/mcp_server_python_docs/models.py` line 28
**Impact:** Style; 111 chars exceeds configured 100-char line length

### IR-04: Pyright type errors in inventory.py (DataObjStr attributes)

**File:** `src/mcp_server_python_docs/ingestion/inventory.py` lines 34-43, 114
**Impact:** 6 pyright errors; `sphobjinv` lacks type stubs so pyright infers `None` for `DataObjStr.uri` and `DataObjStr.dispname`

The `_expand_uri` and `_get_display_name` functions operate on `soi.DataObjStr` attributes which have no type annotations. Pyright reports `reportOperatorIssue`, `reportAttributeAccessIssue`, and `reportReturnType`. Additionally, `soi.Inventory(url=...)` triggers `reportCallIssue` because the constructor signature is not typed.

**Fix:** Add type: ignore comments or create a stub file for sphobjinv.

## Summary

| Severity | Count | Auto-fixable |
|----------|-------|-------------|
| Critical | 1 | Yes |
| Warning  | 5 | Yes |
| Info     | 4 | Yes (ruff --fix for IR-01/02/03) |
| **Total** | **10** | |

**Verdict:** 1 critical blocker (CR-01: FTS5 check on read-only connection) must be fixed before Phase 2. The server literally cannot start when an index.db exists. Warning-level items (WR-01 through WR-05) should be fixed but are not blocking -- WR-01 and WR-02 only manifest with multi-version indexes which are Phase 6 scope.
