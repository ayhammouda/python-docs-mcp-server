"""Search service — handles search_docs for all kind values.

Extracts the search logic from server.py into a service class.
Receives sqlite3.Connection and synonyms dict via constructor.
No MCP types imported — dependency rule enforced.
"""
from __future__ import annotations

import sqlite3

from mcp_server_python_docs.models import SearchDocsResult
from mcp_server_python_docs.retrieval.query import (
    build_match_expression,
    classify_query,
    expand_synonyms,
)
from mcp_server_python_docs.retrieval.ranker import (
    lookup_symbols_exact,
    search_examples,
    search_sections,
    search_symbols,
)
from mcp_server_python_docs.services.observability import log_tool_call
from mcp_server_python_docs.services.version_resolution import resolve_version_permissive


class SearchService:
    """Search service dispatching queries through classifier, synonym expansion,
    and FTS5/symbol fast-path."""

    def __init__(self, db: sqlite3.Connection, synonyms: dict[str, list[str]]) -> None:
        self._db = db
        self._synonyms = synonyms
        self._last_synonym_expanded: bool = False
        self._last_resolution: str = "none"
        # Note: _symbol_exists uses direct SQL (not the version-scoped symbol
        # cache) because classify_query's callback has no version context.

    def _resolve_version(self, version: str | None) -> str | None:
        """Resolve and validate version using shared resolution logic.

        Returns None if version was None -- search is intentionally
        cross-version so LLMs see results from all versions and can
        compare (CR-01 documented design decision).

        Raises VersionNotFoundError for unknown versions (MVER-03).
        """
        return resolve_version_permissive(self._db, version)

    def _symbol_exists(self, name: str) -> bool:
        """Check if a symbol name exists in the symbols table."""
        row = self._db.execute(
            "SELECT 1 FROM symbols WHERE qualified_name = ? LIMIT 1",
            (name,),
        ).fetchone()
        return row is not None

    @log_tool_call("search_docs")
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
        # Validate version if explicitly provided (MVER-03)
        resolved_version = self._resolve_version(version)

        # Track synonym expansion for observability (OPS-01)
        original_tokens = set(query.lower().split()) if query.strip() else set()
        expanded = expand_synonyms(query, self._synonyms)
        self._last_synonym_expanded = expanded != original_tokens

        # Classify query for routing (RETR-04)
        query_type = classify_query(query, self._symbol_exists)

        # Symbol fast-path: skip FTS5 entirely
        if kind == "symbol" or (kind == "auto" and query_type == "symbol"):
            hits = lookup_symbols_exact(self._db, query, resolved_version, max_results)
            if hits:
                self._last_resolution = "exact"
                return SearchDocsResult(hits=hits)
            # Fall through to FTS if symbol lookup found nothing and kind is auto
            if kind == "symbol":
                self._last_resolution = "exact"
                return SearchDocsResult(hits=[], note=None)

        # FTS5 path: build match expression with synonym expansion (RETR-05)
        match_expr = build_match_expression(query, self._synonyms)

        # Route to appropriate FTS5 table based on kind
        if kind == "section":
            hits = search_sections(self._db, match_expr, resolved_version, max_results)
        elif kind == "example":
            hits = search_examples(self._db, match_expr, resolved_version, max_results)
        elif kind == "page":
            # Page search uses sections with broader matching
            hits = search_sections(self._db, match_expr, resolved_version, max_results)
        else:
            # kind == "auto": try sections first, fall back to symbols FTS
            hits = search_sections(self._db, match_expr, resolved_version, max_results)
            if not hits:
                hits = search_symbols(self._db, match_expr, resolved_version, max_results)

        self._last_resolution = "fts"
        return SearchDocsResult(hits=hits)
