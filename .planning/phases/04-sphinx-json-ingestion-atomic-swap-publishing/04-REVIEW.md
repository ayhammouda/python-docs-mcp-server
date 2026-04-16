---
status: findings
phase: "04"
depth: standard
files_reviewed: 9
findings:
  critical: 1
  warning: 5
  info: 3
  total: 9
reviewed_at: "2026-04-15"
---

# Phase 04 Code Review: Sphinx JSON Ingestion & Atomic Swap Publishing

## Files Reviewed

- `src/mcp_server_python_docs/ingestion/sphinx_json.py`
- `src/mcp_server_python_docs/ingestion/publish.py`
- `src/mcp_server_python_docs/__main__.py`
- `pyproject.toml`
- `tests/conftest.py`
- `tests/test_ingestion.py`
- `tests/test_publish.py`
- `tests/fixtures/sample_library.fjson`
- `tests/fixtures/sample_broken.fjson`

---

## Critical

### CR-01: beautifulsoup4 used as undeclared dependency

**File:** `src/mcp_server_python_docs/ingestion/sphinx_json.py:18`, `pyproject.toml`
**Impact:** Build/install failure in constrained environments

`sphinx_json.py` imports `from bs4 import BeautifulSoup, Tag` directly, but `beautifulsoup4` is not declared in `pyproject.toml` dependencies. It works today only because `markdownify` pulls it in as a transitive dependency. If `markdownify` ever drops that dependency, or if a resolver installs a fork without it, the import will fail at runtime. The project imports and uses `bs4` as a first-class API (parsing HTML, finding tags), not just indirectly through markdownify.

**Fix:** Add `beautifulsoup4>=4.12,<5.0` to the `[project] dependencies` list in `pyproject.toml`.

---

## Warnings

### WR-01: Hardcoded Unix venv paths ("bin/pip", "bin/sphinx-build")

**File:** `src/mcp_server_python_docs/__main__.py:168,190`
**Impact:** `build-index` CLI will fail on Windows

```python
pip_path = os.path.join(venv_dir, "bin", "pip")
sphinx_build = os.path.join(venv_dir, "bin", "sphinx-build")
```

On Windows, venv scripts live under `Scripts/`, not `bin/`. While the project targets macOS/Linux, the `build-index` command silently fails on Windows with a confusing "file not found" subprocess error. The CLAUDE.md constraints do not exclude Windows from the build CLI.

**Fix:** Use `sysconfig.get_path("scripts", vars={"base": venv_dir})` or `Path(venv_dir) / ("Scripts" if sys.platform == "win32" else "bin")`.

### WR-02: Connection leak in publish_index on exceptions

**File:** `src/mcp_server_python_docs/ingestion/publish.py:282-317`
**Impact:** Resource leak if DB operations raise unexpected exceptions

`publish_index()` opens three separate `get_readwrite_connection()` calls and closes them inline. If any `conn.execute()` or `conn.commit()` raises an unexpected exception between open and close, the connection leaks. The pattern repeats three times in the function.

**Fix:** Use context manager pattern or try/finally for each connection:
```python
conn = get_readwrite_connection(build_db_path)
try:
    # ... operations ...
    conn.commit()
finally:
    conn.close()
```

### WR-03: Connection leak in __main__.py build_index on early exit

**File:** `src/mcp_server_python_docs/__main__.py:119-271`
**Impact:** Connection leak on FTS5 check failure or unexpected exception

The main `conn` is opened at line 119 and closed at line 271, but `assert_fts5_available(conn)` at line 121 can raise `FTS5UnavailableError`, and other exceptions in the version loop could propagate past the `except Exception` at line 252. In both cases, `conn.close()` is never reached.

**Fix:** Wrap the main connection in try/finally or use a context manager.

### WR-04: Line too long -- ruff E501 violation

**File:** `src/mcp_server_python_docs/ingestion/publish.py:170`
**Impact:** Lint failure in CI

Line 170 is 113 characters, exceeding the 100-character limit set in `pyproject.toml`. The message string `"WARN: fts5: sections_fts has no asyncio matches (may be OK for partial builds)"` overflows.

**Fix:** Break the string across lines or shorten the message.

### WR-05: Unused imports and import ordering in test files

**File:** `tests/test_ingestion.py:10,12,27`, `tests/test_publish.py:12`
**Impact:** Lint failure in CI (ruff F401, I001)

- `test_ingestion.py`: `json`, `Path`, `bootstrap_schema`, `get_readwrite_connection` all imported but unused.
- `test_publish.py`: `pytest` imported but unused; import blocks unsorted in both files.

**Fix:** Remove unused imports and run `ruff check --fix` to auto-sort.

---

## Info

### IR-01: Timestamp collision possible in generate_build_path

**File:** `src/mcp_server_python_docs/ingestion/publish.py:37-38`
**Impact:** Low -- only if build-index runs twice within the same second

`generate_build_path()` uses `%Y%m%d-%H%M%S` (second-level granularity). Two calls within the same second produce the same path. The test `TestBuildPath::test_unique` has a `time.sleep(0.01)` but the format has no sub-second component, so same-second calls would collide.

**Suggestion:** Add microseconds: `datetime.now().strftime("%Y%m%d-%H%M%S-%f")`, or accept this as a non-issue since `build-index` is a manual CLI command unlikely to be invoked concurrently.

### IR-02: atomic_swap not truly atomic on all filesystems

**File:** `src/mcp_server_python_docs/ingestion/publish.py:184-220`
**Impact:** Low -- theoretical data loss window on non-POSIX

The docstring correctly notes POSIX atomicity requirements. The two-step rename (old to .previous, then new to target) has a window where neither file exists at the target path. On POSIX, `os.rename` is atomic per-rename, but the two-rename sequence is not atomic as a whole. If the process crashes between line 213 and line 217, the target is gone and the new file is not yet in place. This matches the docstring's documented behavior and is acceptable for a CLI-driven build tool, but worth noting for awareness.

### IR-03: ingest_fjson_file commits per file, not per batch

**File:** `src/mcp_server_python_docs/ingestion/sphinx_json.py:327`
**Impact:** Performance -- slower ingestion for large doc sets

`ingest_fjson_file` calls `conn.commit()` after every file. For a full CPython doc build (~500+ fjson files), this means ~500 fsync operations. Batching commits (e.g., every 50-100 files) in `ingest_sphinx_json_dir` would improve throughput without losing failure isolation, since the rollback in the except block only affects the current file's operations.

**Suggestion:** Move the commit to `ingest_sphinx_json_dir` and call it every N files or at the end.

---

## Summary

The Phase 4 implementation is solid. All 110 tests pass (38 new + 72 prior). The ingestion pipeline correctly handles per-document failure isolation, HTML-to-markdown conversion, code block extraction with doctest classification, FTS5 index rebuilding, synonym population, and atomic-swap publishing with rollback support. The PUBL-06 regression test demonstrates server survival during rebuilds.

The one critical finding (CR-01: undeclared beautifulsoup4 dependency) should be fixed before shipping -- it will cause install failures in fresh environments where markdownify's transitive deps are not guaranteed. The warnings are all standard code quality issues (connection safety, lint violations, Windows path compatibility).
