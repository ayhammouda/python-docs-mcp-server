---
phase: 5
plan_id: 05-A
title: "Service layer — SearchService, ContentService, VersionService"
wave: 1
depends_on: []
files_modified:
  - src/mcp_server_python_docs/services/__init__.py
  - src/mcp_server_python_docs/services/search.py
  - src/mcp_server_python_docs/services/content.py
  - src/mcp_server_python_docs/services/version.py
requirements:
  - SRVR-03
  - SRVR-04
autonomous: true
---

<objective>
Create the 3-service layer (SearchService, ContentService, VersionService) per build guide section 4. Extract the inline `_do_search()` logic from `server.py` into `SearchService`. Implement `ContentService` for `get_docs` page/section retrieval with budget enforcement. Implement `VersionService` for `list_versions` query. Services receive `sqlite3.Connection` and `synonyms` dict via constructor, call retrieval/storage functions, and return Pydantic models.
</objective>

<tasks>

<task id="1">
<title>Create services package init</title>
<read_first>
- src/mcp_server_python_docs/storage/__init__.py (analog — empty package init)
</read_first>
<action>
Create `src/mcp_server_python_docs/services/__init__.py` as an empty file (just a module docstring):

```python
"""Service layer — SearchService, ContentService, VersionService.

Services sit between FastMCP tool handlers and the retrieval/storage layers.
Dependency rule: server -> services -> retrieval/storage.
No service touches SQL directly (except through storage/retrieval functions).
No service imports MCP types.
"""
```
</action>
<acceptance_criteria>
- File exists at `src/mcp_server_python_docs/services/__init__.py`
- File contains a module docstring mentioning "Service layer"
- `python -c "import mcp_server_python_docs.services"` succeeds
</acceptance_criteria>
</task>

<task id="2">
<title>Create SearchService in services/search.py</title>
<read_first>
- src/mcp_server_python_docs/server.py (lines 107-159 — _symbol_exists and _do_search to extract)
- src/mcp_server_python_docs/retrieval/query.py (classify_query, build_match_expression)
- src/mcp_server_python_docs/retrieval/ranker.py (lookup_symbols_exact, search_sections, search_symbols, search_examples)
- src/mcp_server_python_docs/models.py (SearchDocsResult, SymbolHit)
</read_first>
<action>
Create `src/mcp_server_python_docs/services/search.py`:

```python
"""Search service — handles search_docs for all kind values."""
from __future__ import annotations

import sqlite3

from mcp_server_python_docs.models import SearchDocsResult
from mcp_server_python_docs.retrieval.query import (
    build_match_expression,
    classify_query,
)
from mcp_server_python_docs.retrieval.ranker import (
    lookup_symbols_exact,
    search_examples,
    search_sections,
    search_symbols,
)


class SearchService:
    """Search service dispatching queries through classifier, synonym expansion, and FTS5/symbol fast-path."""

    def __init__(self, db: sqlite3.Connection, synonyms: dict[str, list[str]]) -> None:
        self._db = db
        self._synonyms = synonyms

    def _symbol_exists(self, name: str) -> bool:
        """Check if a symbol name exists in the symbols table."""
        row = self._db.execute(
            "SELECT 1 FROM symbols WHERE qualified_name = ? LIMIT 1",
            (name,),
        ).fetchone()
        return row is not None

    def search(
        self,
        query: str,
        version: str | None = None,
        kind: str = "auto",
        max_results: int = 5,
    ) -> SearchDocsResult:
        """Execute a search query using the retrieval layer.

        Routes queries through classifier -> synonym expansion -> FTS5 or
        symbol fast-path. Returns SearchDocsResult with hits list.
        """
        # Classify query for routing (RETR-04)
        query_type = classify_query(query, self._symbol_exists)

        # Symbol fast-path: skip FTS5 entirely
        if kind == "symbol" or (kind == "auto" and query_type == "symbol"):
            hits = lookup_symbols_exact(self._db, query, version, max_results)
            if hits:
                return SearchDocsResult(hits=hits)
            # Fall through to FTS if symbol lookup found nothing and kind is auto
            if kind == "symbol":
                return SearchDocsResult(hits=[], note=None)

        # FTS5 path: build match expression with synonym expansion (RETR-05)
        match_expr = build_match_expression(query, self._synonyms)

        # Route to appropriate FTS5 table based on kind
        if kind == "section":
            hits = search_sections(self._db, match_expr, version, max_results)
        elif kind == "example":
            hits = search_examples(self._db, match_expr, version, max_results)
        elif kind == "page":
            hits = search_sections(self._db, match_expr, version, max_results)
        else:
            # kind == "auto": try sections first, fall back to symbols FTS
            hits = search_sections(self._db, match_expr, version, max_results)
            if not hits:
                hits = search_symbols(self._db, match_expr, version, max_results)

        return SearchDocsResult(hits=hits)
```

This is a direct extraction of `_do_search()` and `_symbol_exists()` from `server.py` into a class. No logic changes, just structural refactoring.
</action>
<acceptance_criteria>
- File exists at `src/mcp_server_python_docs/services/search.py`
- `SearchService` class has `__init__(self, db, synonyms)` and `search(self, query, version, kind, max_results)` methods
- `python -c "from mcp_server_python_docs.services.search import SearchService"` succeeds
- No MCP types imported in search.py
</acceptance_criteria>
</task>

<task id="3">
<title>Create ContentService in services/content.py</title>
<read_first>
- src/mcp_server_python_docs/models.py (GetDocsResult — fields: content, slug, title, version, anchor, char_count, truncated, next_start_index)
- src/mcp_server_python_docs/retrieval/budget.py (apply_budget function signature)
- src/mcp_server_python_docs/storage/schema.sql (documents and sections table columns)
- src/mcp_server_python_docs/errors.py (PageNotFoundError, VersionNotFoundError)
- python-docs-mcp-server-build-guide.md §3 Tool 2 description (anchor selects section vs page)
</read_first>
<action>
Create `src/mcp_server_python_docs/services/content.py`:

```python
"""Content service — handles get_docs for page and section retrieval."""
from __future__ import annotations

import sqlite3

from mcp_server_python_docs.errors import PageNotFoundError, VersionNotFoundError
from mcp_server_python_docs.models import GetDocsResult
from mcp_server_python_docs.retrieval.budget import apply_budget


class ContentService:
    """Content retrieval service for get_docs tool.

    When anchor is provided, returns just that section.
    When omitted, returns the full page with truncation/pagination.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def _resolve_version(self, version: str | None) -> str:
        """Resolve version to actual version string. Defaults to latest (is_default=1)."""
        if version is not None:
            row = self._db.execute(
                "SELECT version FROM doc_sets WHERE version = ?",
                (version,),
            ).fetchone()
            if row is None:
                available = [
                    r[0] for r in self._db.execute(
                        "SELECT version FROM doc_sets ORDER BY version"
                    ).fetchall()
                ]
                raise VersionNotFoundError(
                    f"Version {version!r} not found; available: {available}"
                )
            return version
        # Default to latest
        row = self._db.execute(
            "SELECT version FROM doc_sets WHERE is_default = 1 LIMIT 1"
        ).fetchone()
        if row is None:
            row = self._db.execute(
                "SELECT version FROM doc_sets ORDER BY version DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise VersionNotFoundError("No versions available in index")
        return row[0]

    def get_docs(
        self,
        slug: str,
        version: str | None = None,
        anchor: str | None = None,
        max_chars: int = 8000,
        start_index: int = 0,
    ) -> GetDocsResult:
        """Retrieve documentation content by slug, optionally narrowed to a section by anchor."""
        resolved_version = self._resolve_version(version)

        # Find the document
        doc_row = self._db.execute(
            """
            SELECT d.id, d.title, d.slug
            FROM documents d
            JOIN doc_sets ds ON d.doc_set_id = ds.id
            WHERE d.slug = ? AND ds.version = ?
            LIMIT 1
            """,
            (slug, resolved_version),
        ).fetchone()

        if doc_row is None:
            raise PageNotFoundError(
                f"Page {slug!r} not found for version {resolved_version}"
            )

        doc_id = doc_row["id"]
        doc_title = doc_row["title"]

        if anchor is not None:
            # Section-level retrieval
            section_row = self._db.execute(
                """
                SELECT heading, content_text
                FROM sections
                WHERE document_id = ? AND anchor = ?
                LIMIT 1
                """,
                (doc_id, anchor),
            ).fetchone()

            if section_row is None:
                raise PageNotFoundError(
                    f"Section {anchor!r} not found in {slug!r} v{resolved_version}"
                )

            full_text = section_row["content_text"] or ""
            title = section_row["heading"] or doc_title
        else:
            # Page-level retrieval: concatenate all sections in ordinal order
            section_rows = self._db.execute(
                """
                SELECT heading, content_text
                FROM sections
                WHERE document_id = ?
                ORDER BY ordinal
                """,
                (doc_id,),
            ).fetchall()

            if not section_rows:
                full_text = ""
            else:
                parts = []
                for row in section_rows:
                    heading = row["heading"] or ""
                    content = row["content_text"] or ""
                    if heading:
                        parts.append(f"## {heading}\n\n{content}")
                    else:
                        parts.append(content)
                full_text = "\n\n".join(parts)

            title = doc_title

        # Apply budget enforcement (RETR-08)
        truncated_text, is_truncated, next_idx = apply_budget(
            full_text, max_chars, start_index
        )

        return GetDocsResult(
            content=truncated_text,
            slug=slug,
            title=title,
            version=resolved_version,
            anchor=anchor,
            char_count=len(full_text),
            truncated=is_truncated,
            next_start_index=next_idx,
        )
```
</action>
<acceptance_criteria>
- File exists at `src/mcp_server_python_docs/services/content.py`
- `ContentService` class has `__init__(self, db)` and `get_docs(self, slug, version, anchor, max_chars, start_index)` methods
- `get_docs` raises `PageNotFoundError` when slug not found
- `get_docs` raises `VersionNotFoundError` when version not found
- `get_docs` calls `apply_budget()` for truncation
- `python -c "from mcp_server_python_docs.services.content import ContentService"` succeeds
- No MCP types imported in content.py
</acceptance_criteria>
</task>

<task id="4">
<title>Create VersionService in services/version.py</title>
<read_first>
- src/mcp_server_python_docs/models.py (ListVersionsResult, VersionInfo — fields: version, language, label, is_default, built_at)
- src/mcp_server_python_docs/storage/schema.sql (doc_sets table columns)
</read_first>
<action>
Create `src/mcp_server_python_docs/services/version.py`:

```python
"""Version service — lists available documentation versions."""
from __future__ import annotations

import sqlite3

from mcp_server_python_docs.models import ListVersionsResult, VersionInfo


class VersionService:
    """Version listing service for list_versions tool.

    Trivial service that queries doc_sets table. Kept as a class
    for symmetry with SearchService and ContentService.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def list_versions(self) -> ListVersionsResult:
        """List all available documentation versions from doc_sets table."""
        rows = self._db.execute(
            """
            SELECT version, language, label, is_default, built_at
            FROM doc_sets
            ORDER BY version DESC
            """
        ).fetchall()

        versions = [
            VersionInfo(
                version=row["version"],
                language=row["language"],
                label=row["label"],
                is_default=bool(row["is_default"]),
                built_at=row["built_at"] or "",
            )
            for row in rows
        ]

        return ListVersionsResult(versions=versions)
```
</action>
<acceptance_criteria>
- File exists at `src/mcp_server_python_docs/services/version.py`
- `VersionService` class has `__init__(self, db)` and `list_versions(self)` methods
- `list_versions()` returns `ListVersionsResult` with `VersionInfo` items
- `python -c "from mcp_server_python_docs.services.version import VersionService"` succeeds
- No MCP types imported in version.py
</acceptance_criteria>
</task>

</tasks>

<verification>
1. All three service files exist under `src/mcp_server_python_docs/services/`
2. `python -c "from mcp_server_python_docs.services.search import SearchService; from mcp_server_python_docs.services.content import ContentService; from mcp_server_python_docs.services.version import VersionService"` succeeds
3. No service file imports `mcp`, `FastMCP`, `Context`, `ToolError`, or `ToolAnnotations`
4. SearchService.search() matches the logic of the current `_do_search()` in server.py
</verification>

<must_haves>
- SearchService wraps existing _do_search logic without behavior changes
- ContentService implements page-level and section-level retrieval with apply_budget
- VersionService queries doc_sets and returns ListVersionsResult
- Dependency rule enforced: no MCP imports in services
</must_haves>
