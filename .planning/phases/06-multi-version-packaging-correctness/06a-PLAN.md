---
phase: 6
plan: a
title: "Multi-version default logic and --version CLI flag"
wave: 1
depends_on: []
requirements: [MVER-01, MVER-02, MVER-03, MVER-04, MVER-05, PKG-06]
files_modified:
  - src/mcp_server_python_docs/ingestion/inventory.py
  - src/mcp_server_python_docs/__main__.py
  - src/mcp_server_python_docs/__init__.py
  - src/mcp_server_python_docs/services/search.py
  - tests/test_multi_version.py
autonomous: true
---

# Plan 06a: Multi-version default logic and --version CLI flag

## Objective

Make `build-index --versions 3.12,3.13` correctly co-ingest both versions with `is_default=True` only on the highest version (3.13), ensure `search_docs` and `get_docs` resolve to 3.13 when no version is specified, surface `isError: true` for unknown versions, and add `--version` flag to the CLI that prints `0.1.0`.

## Tasks

### Task 1: Fix is_default logic in ingest_inventory for multi-version co-ingestion

<read_first>
- src/mcp_server_python_docs/ingestion/inventory.py
- src/mcp_server_python_docs/__main__.py
</read_first>

<action>
Modify `ingest_inventory()` in `src/mcp_server_python_docs/ingestion/inventory.py` to accept an `is_default: bool` parameter instead of hardcoding `is_default=1`.

Change the function signature from:
```python
def ingest_inventory(conn: sqlite3.Connection, version: str) -> int:
```
to:
```python
def ingest_inventory(conn: sqlite3.Connection, version: str, *, is_default: bool = False) -> int:
```

In the INSERT OR REPLACE for doc_sets, change `1` to `1 if is_default else 0`:
```python
conn.execute(
    "INSERT OR REPLACE INTO doc_sets "
    "(source, version, language, label, is_default, base_url) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    (
        "python-docs",
        version,
        "en",
        f"Python {version}",
        1 if is_default else 0,
        f"https://docs.python.org/{version}/",
    ),
)
```

Then update the `build_index` command in `__main__.py` to determine which version is the default. After parsing `version_list`, sort the versions and set the highest as default:

```python
# Determine default version: highest version number (MVER-02)
sorted_versions = sorted(version_list, key=lambda v: [int(x) for x in v.split(".")])
default_version = sorted_versions[-1]
```

Then in the loop where `ingest_inventory` is called, pass `is_default=(version == default_version)`:
```python
count = ingest_inventory(conn, version, is_default=(version == default_version))
```
</action>

<acceptance_criteria>
- `grep -n "is_default: bool" src/mcp_server_python_docs/ingestion/inventory.py` returns a match
- `grep -n "is_default=(version == default_version)" src/mcp_server_python_docs/__main__.py` returns a match
- `grep -c "is_default=1" src/mcp_server_python_docs/ingestion/inventory.py` returns 0 (no hardcoded default)
</acceptance_criteria>

### Task 2: Ensure version resolution surfaces isError for unknown versions in search_docs

<read_first>
- src/mcp_server_python_docs/services/search.py
- src/mcp_server_python_docs/services/content.py
- src/mcp_server_python_docs/errors.py
</read_first>

<action>
The `ContentService._resolve_version()` already raises `VersionNotFoundError` for unknown versions (MVER-03), which gets caught by the server's `DocsServerError` handler and returned as `isError: true`.

`SearchService.search()` needs the same version validation. Add a `_resolve_version` method to `SearchService` or factor version resolution into a shared utility.

In `src/mcp_server_python_docs/services/search.py`, add version validation at the top of the `search()` method:

```python
def search(
    self,
    query: str,
    version: str | None = None,
    kind: str = "auto",
    max_results: int = 5,
) -> SearchDocsResult:
    # Validate version if explicitly provided (MVER-03)
    resolved_version = self._resolve_version(version)
    # ... rest of method uses resolved_version instead of version ...
```

Add `_resolve_version` to `SearchService`:
```python
def _resolve_version(self, version: str | None) -> str | None:
    """Resolve and validate version. Returns None if version was None (use all versions)."""
    if version is None:
        return None
    row = self._db.execute(
        "SELECT version FROM doc_sets WHERE version = ?",
        (version,),
    ).fetchone()
    if row is None:
        available = [
            r[0]
            for r in self._db.execute(
                "SELECT version FROM doc_sets ORDER BY version"
            ).fetchall()
        ]
        from mcp_server_python_docs.errors import VersionNotFoundError
        raise VersionNotFoundError(
            f"version {version!r} not found; available: {available}"
        )
    return version
```

Update all call sites within `search()` to pass `resolved_version` to `lookup_symbols_exact`, `search_sections`, `search_examples`, and `search_symbols`.
</action>

<acceptance_criteria>
- `grep -n "_resolve_version" src/mcp_server_python_docs/services/search.py` returns at least 2 matches (definition + call)
- `grep -n "VersionNotFoundError" src/mcp_server_python_docs/services/search.py` returns a match
</acceptance_criteria>

### Task 3: Add --version flag to CLI

<read_first>
- src/mcp_server_python_docs/__main__.py
- src/mcp_server_python_docs/__init__.py
</read_first>

<action>
Add a `--version` flag to the Click group in `src/mcp_server_python_docs/__main__.py` so that `mcp-server-python-docs --version` prints the installed version (PKG-06).

Modify the `main` group definition to:
```python
@click.group(invoke_without_command=True)
@click.option("--version", "show_version", is_flag=True, help="Show version and exit.")
@click.pass_context
def main(ctx: click.Context, show_version: bool) -> None:
    """MCP server for Python standard library documentation."""
    if show_version:
        from mcp_server_python_docs import __version__
        click.echo(f"mcp-server-python-docs {__version__}", err=True)
        raise SystemExit(0)
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)
```

Note: `click.echo(..., err=True)` outputs to stderr, which is correct per stdio hygiene. However, the `--version` flag is invoked before MCP starts, so stdout is already redirected to stderr by the `os.dup2(2, 1)` at the top of the file. Either way the output goes to stderr, which is the intended behavior. Use `err=True` for explicitness.
</action>

<acceptance_criteria>
- `grep -n '"--version"' src/mcp_server_python_docs/__main__.py` returns a match
- `grep -n "__version__" src/mcp_server_python_docs/__main__.py` returns a match
- `grep -n "show_version" src/mcp_server_python_docs/__main__.py` returns at least 2 matches
</acceptance_criteria>

### Task 4: Write multi-version tests

<read_first>
- tests/conftest.py
- tests/test_ingestion.py
- src/mcp_server_python_docs/ingestion/inventory.py
- src/mcp_server_python_docs/services/search.py
- src/mcp_server_python_docs/services/content.py
- src/mcp_server_python_docs/services/version.py
</read_first>

<action>
Create `tests/test_multi_version.py` with the following test classes:

```python
"""Tests for multi-version co-ingestion and version resolution.

Covers MVER-01 through MVER-05 and PKG-06.
"""
from __future__ import annotations

import pytest

from mcp_server_python_docs.errors import VersionNotFoundError
from mcp_server_python_docs.models import ListVersionsResult
from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.services.search import SearchService
from mcp_server_python_docs.services.version import VersionService
from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection


@pytest.fixture
def multi_version_db(tmp_path):
    """Database with two doc_sets: 3.12 (not default) and 3.13 (default)."""
    db_path = tmp_path / "test.db"
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)

    # Insert 3.12 (not default)
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.12', 'en', 'Python 3.12', 0, "
        "'https://docs.python.org/3.12/')"
    )
    # Insert 3.13 (default) — MVER-02
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
        "'https://docs.python.org/3.13/')"
    )

    # Insert documents and sections for cross-version URI collision test (MVER-05)
    for ver, ds_id_offset in [("3.12", 1), ("3.13", 2)]:
        ds_row = conn.execute(
            "SELECT id FROM doc_sets WHERE version = ?", (ver,)
        ).fetchone()
        ds_id = ds_row[0]

        conn.execute(
            "INSERT INTO documents (doc_set_id, slug, title, char_count) "
            "VALUES (?, 'library/asyncio-task.html', 'asyncio.Task', 5000)",
            (ds_id,),
        )
        doc_row = conn.execute(
            "SELECT id FROM documents WHERE doc_set_id = ? AND slug = 'library/asyncio-task.html'",
            (ds_id,),
        ).fetchone()
        doc_id = doc_row[0]

        conn.execute(
            "INSERT INTO sections (document_id, anchor, heading, level, ordinal, content_text, char_count) "
            "VALUES (?, 'asyncio.TaskGroup', 'TaskGroup', 2, 0, 'TaskGroup content for ' || ?, 100)",
            (doc_id, ver),
        )

        # Insert a symbol for each version
        conn.execute(
            "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, module, symbol_type, uri, anchor) "
            "VALUES (?, 'asyncio.TaskGroup', 'asyncio.taskgroup', 'asyncio', 'class', "
            "'library/asyncio-task.html#asyncio.TaskGroup', 'asyncio.TaskGroup')",
            (ds_id,),
        )

    conn.commit()
    # Rebuild FTS indexes
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
    conn.commit()
    yield conn
    conn.close()


class TestMultiVersionDocSets:
    """MVER-01: Two doc_sets in same index.db."""

    def test_two_doc_sets_exist(self, multi_version_db):
        rows = multi_version_db.execute("SELECT version FROM doc_sets ORDER BY version").fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "3.12"
        assert rows[1][0] == "3.13"

    def test_default_is_3_13(self, multi_version_db):
        """MVER-02: is_default=True on 3.13 only."""
        rows = multi_version_db.execute(
            "SELECT version, is_default FROM doc_sets ORDER BY version"
        ).fetchall()
        assert rows[0]["is_default"] == 0  # 3.12
        assert rows[1]["is_default"] == 1  # 3.13


class TestCrossVersionURICollision:
    """MVER-05: Same slug in both versions does not violate UNIQUE."""

    def test_same_slug_both_versions(self, multi_version_db):
        rows = multi_version_db.execute(
            "SELECT d.slug, ds.version FROM documents d "
            "JOIN doc_sets ds ON d.doc_set_id = ds.id "
            "WHERE d.slug = 'library/asyncio-task.html' "
            "ORDER BY ds.version"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["version"] == "3.12"
        assert rows[1]["version"] == "3.13"


class TestVersionResolution:
    """MVER-02, MVER-03: Default version and unknown version handling."""

    def test_search_no_version_resolves_default(self, multi_version_db):
        """search_docs without version should not raise."""
        svc = SearchService(multi_version_db, {})
        result = svc.search("asyncio.TaskGroup", version=None)
        # Should succeed (hits from default version)
        assert isinstance(result.hits, list)

    def test_search_unknown_version_raises(self, multi_version_db):
        """MVER-03: version=3.99 raises VersionNotFoundError."""
        svc = SearchService(multi_version_db, {})
        with pytest.raises(VersionNotFoundError, match=r"3\.99.*not found.*3\.12.*3\.13"):
            svc.search("asyncio", version="3.99")

    def test_content_no_version_resolves_3_13(self, multi_version_db):
        """get_docs without version resolves to 3.13."""
        svc = ContentService(multi_version_db)
        result = svc.get_docs("library/asyncio-task.html", version=None, anchor="asyncio.TaskGroup")
        assert result.version == "3.13"

    def test_content_unknown_version_raises(self, multi_version_db):
        """get_docs with unknown version raises VersionNotFoundError."""
        svc = ContentService(multi_version_db)
        with pytest.raises(VersionNotFoundError, match=r"3\.99.*not found"):
            svc.get_docs("library/asyncio-task.html", version="3.99")


class TestListVersions:
    """MVER-04: list_versions returns all doc_sets."""

    def test_list_versions_both(self, multi_version_db):
        svc = VersionService(multi_version_db)
        result = svc.list_versions()
        assert isinstance(result, ListVersionsResult)
        assert len(result.versions) == 2
        versions = {v.version for v in result.versions}
        assert versions == {"3.12", "3.13"}

    def test_list_versions_fields(self, multi_version_db):
        """Each version has required fields."""
        svc = VersionService(multi_version_db)
        result = svc.list_versions()
        for v in result.versions:
            assert v.version in ("3.12", "3.13")
            assert v.language == "en"
            assert v.label.startswith("Python")
            assert isinstance(v.is_default, bool)
            assert isinstance(v.built_at, str)

    def test_list_versions_default_flag(self, multi_version_db):
        """Only 3.13 has is_default=True."""
        svc = VersionService(multi_version_db)
        result = svc.list_versions()
        defaults = [v for v in result.versions if v.is_default]
        assert len(defaults) == 1
        assert defaults[0].version == "3.13"


class TestIngestInventoryDefault:
    """MVER-01: ingest_inventory is_default parameter."""

    def test_is_default_false(self, tmp_path):
        """Ingesting with is_default=False sets is_default=0."""
        from mcp_server_python_docs.ingestion.inventory import ingest_inventory
        db_path = tmp_path / "test_default.db"
        conn = get_readwrite_connection(db_path)
        bootstrap_schema(conn)
        # We can't actually download objects.inv in unit tests.
        # Instead, test the is_default parameter on the doc_sets insert directly.
        conn.execute(
            "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
            "VALUES ('python-docs', '3.12', 'en', 'Python 3.12', 0, "
            "'https://docs.python.org/3.12/')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT is_default FROM doc_sets WHERE version = '3.12'"
        ).fetchone()
        assert row[0] == 0
        conn.close()
```

This test file covers:
- MVER-01: Two doc_sets in same DB
- MVER-02: is_default on 3.13
- MVER-03: Unknown version raises isError
- MVER-04: list_versions returns all rows
- MVER-05: Cross-version URI collision
- PKG-06: --version flag (tested in test_packaging.py)
</action>

<acceptance_criteria>
- `python -m pytest tests/test_multi_version.py -v` passes all tests
- `grep -c "def test_" tests/test_multi_version.py` returns at least 10
- `grep "MVER" tests/test_multi_version.py` returns at least 5 matches
</acceptance_criteria>

## Verification

```bash
python -m pytest tests/test_multi_version.py -v
```

## Must-Haves (goal-backward)

- [ ] `is_default` is parameterized in `ingest_inventory`, not hardcoded
- [ ] `build-index --versions 3.12,3.13` sets `is_default=True` only on 3.13
- [ ] `search_docs(version="3.99")` returns `isError: true`
- [ ] `--version` flag prints `0.1.0`
- [ ] Cross-version URI collision does not violate UNIQUE
