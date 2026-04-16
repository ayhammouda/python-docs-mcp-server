# Phase 4 — Pattern Map

## Files to Create/Modify

### NEW: `src/mcp_server_python_docs/ingestion/sphinx_json.py`
**Role:** Sphinx JSON build orchestration + fjson parsing + HTML-to-markdown conversion
**Analog:** `src/mcp_server_python_docs/ingestion/inventory.py` (same package, same patterns)
**Key patterns from analog:**
- Module-level logger: `logger = logging.getLogger(__name__)`
- Function signature: receives `sqlite3.Connection` + config params, returns count
- Uses `bootstrap_schema(conn)` before inserts
- Uses `conn.execute()` with parameterized queries
- Uses `conn.commit()` after batch inserts
- FTS rebuild via `INSERT INTO fts_table(fts_table) VALUES('rebuild')`
- Imports from `mcp_server_python_docs.storage.db`

### NEW: `src/mcp_server_python_docs/ingestion/publish.py`
**Role:** Atomic swap protocol, smoke tests, SHA256 hashing, rollback management
**Analog:** No direct analog. Follow patterns from `storage/db.py`:
- Module-level logger
- Functions receive paths as `str | Path`
- Uses `Path(path)` for normalization
- `get_cache_dir()` from `storage.db` for path resolution

### MODIFY: `src/mcp_server_python_docs/__main__.py`
**Role:** Enhanced `build-index` CLI with content ingestion
**Current state:** `build_index()` function only calls `ingest_inventory()`
**Modification:** Add sphinx_json + publish imports, orchestrate full pipeline

### NEW: `tests/test_ingestion.py`
**Role:** Unit tests for fjson parsing, HTML-to-markdown, code block extraction
**Analog:** `tests/test_retrieval.py`, `tests/test_schema.py`
**Key patterns:**
- Uses `tmp_path` fixture for temp databases
- Uses `get_readwrite_connection(tmp_path / "test.db")` for test DBs
- Uses `bootstrap_schema(conn)` in setup
- Tests are simple assert-based, no complex fixtures

### NEW: `tests/test_publish.py`
**Role:** Atomic swap tests, smoke test validation
**Analog:** `tests/test_schema.py` (DB-based tests with tmp_path)

### NEW: `tests/fixtures/` directory
**Role:** Sample fjson files for testing
**No analog — new directory**

### MODIFY: `pyproject.toml`
**Role:** Add `markdownify` to dependencies
**Current state:** Dependencies list in `[project] dependencies`

## Data Flow

```
CLI (build-index --versions 3.13)
  → ingestion/sphinx_json.py (clone, build, parse fjson)
    → storage/db.py (get_readwrite_connection, bootstrap_schema)
    → INSERT documents, sections, examples, synonyms
    → FTS rebuild (sections_fts, examples_fts)
  → ingestion/publish.py (SHA256, smoke test, atomic swap)
  → ingestion/inventory.py (existing: objects.inv symbols)
```

## Import Rules
- `ingestion/sphinx_json.py` imports: `storage.db`, `markdownify`, `json`, `bs4`, `subprocess`
- `ingestion/publish.py` imports: `storage.db`, `hashlib`, `os`, `shutil`
- Neither imports `server.py`, `mcp.*`, or `retrieval.*`
