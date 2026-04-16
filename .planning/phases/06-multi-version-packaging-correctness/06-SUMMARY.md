# Phase 6: Multi-Version & Packaging Correctness - Summary

**Completed:** 2026-04-15
**Plans:** 3/3 complete
**Tests added:** 28 (20 multi-version + 8 packaging)
**Total test suite:** 172 tests, all passing

## What Was Built

### Multi-Version Co-Ingestion (MVER-01, MVER-02)
- `ingest_inventory()` now accepts `is_default: bool` parameter instead of hardcoding `is_default=1`
- `build-index --versions 3.12,3.13` sorts versions numerically and sets highest (3.13) as default
- Two `doc_sets` rows coexist in a single `index.db` without conflicts

### Default Version Resolution (MVER-02, MVER-03)
- `SearchService._resolve_version()` validates explicit versions against `doc_sets` table
- Unknown version (e.g., `version="3.99"`) raises `VersionNotFoundError` with available versions list
- Both `search_docs` and `get_docs` return `isError: true` for unknown versions
- Omitting version resolves to 3.13 (the `is_default=True` row)

### Cross-Version URI Collision Safety (MVER-05)
- Same slug, anchor, and symbol name can exist in both 3.12 and 3.13 without violating any UNIQUE constraint
- `UNIQUE(doc_set_id, slug)` on documents and `UNIQUE(document_id, anchor)` on sections scope uniqueness per version
- Third-version (3.14) insertion test proves extensibility

### list_versions (MVER-04)
- `list_versions()` returns all doc_sets rows with `{version, language, label, is_default, built_at}`
- Already implemented in Phase 5; Phase 6 tests verify multi-version behavior

### Packaging (PKG-01, PKG-02, PKG-03, PKG-04, PKG-06)
- `pyproject.toml` entry-point verified: `mcp-server-python-docs = "mcp_server_python_docs.__main__:main"`
- All runtime deps pinned and verified present
- Wheel content test uses `uv build` + `zipfile` to assert `mcp_server_python_docs/data/synonyms.yaml` is in the wheel
- `--version` flag added to CLI, prints `mcp-server-python-docs 0.1.0` to stderr
- Entry-point module importability verified via subprocess test

## Files Modified

- `src/mcp_server_python_docs/ingestion/inventory.py` -- `is_default` parameter
- `src/mcp_server_python_docs/__main__.py` -- default version logic, `--version` flag
- `src/mcp_server_python_docs/services/search.py` -- `_resolve_version()` method
- `tests/test_multi_version.py` -- 20 tests (new file)
- `tests/test_packaging.py` -- 8 tests (new file)

## Requirements Coverage

| Requirement | Status | Implementation |
|-------------|--------|----------------|
| MVER-01 | Done | `is_default` param in `ingest_inventory`, comma-separated parsing |
| MVER-02 | Done | Highest version is default, `is_default=True` on 3.13 |
| MVER-03 | Done | `_resolve_version` raises `VersionNotFoundError` with available list |
| MVER-04 | Done | `list_versions()` returns all doc_sets (Phase 5 impl, Phase 6 tests) |
| MVER-05 | Done | Cross-version URI collision tests pass cleanly |
| PKG-01 | Done | Entry-point verified in pyproject.toml and wheel metadata |
| PKG-02 | Done | All 7 runtime deps verified present with correct pins |
| PKG-03 | Done | Module runnable and entry-point importable |
| PKG-04 | Done | `synonyms.yaml` present in wheel (uv build + zipfile test) |
| PKG-06 | Done | `--version` flag prints `0.1.0` |
