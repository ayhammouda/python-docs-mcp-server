"""Content service — handles get_docs for page and section retrieval.

When anchor is provided, returns just that section.
When omitted, returns the full page with truncation/pagination.
No MCP types imported — dependency rule enforced.
"""
from __future__ import annotations

import sqlite3

from mcp_server_python_docs.errors import PageNotFoundError
from mcp_server_python_docs.models import GetDocsResult
from mcp_server_python_docs.retrieval.budget import apply_budget
from mcp_server_python_docs.services.cache import create_section_cache
from mcp_server_python_docs.services.observability import log_tool_call
from mcp_server_python_docs.services.version_resolution import resolve_version_strict


class ContentService:
    """Content retrieval service for get_docs tool.

    When anchor is provided, returns just that section.
    When omitted, returns the full page with truncation/pagination.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._get_section = create_section_cache(db)

    def _resolve_version(self, version: str | None) -> str:
        """Resolve version to a concrete version string using shared resolution logic.

        Defaults to the is_default=1 version when None is passed.
        Raises VersionNotFoundError for unknown versions (MVER-03).
        """
        return resolve_version_strict(self._db, version)

    @log_tool_call("get_docs")
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
            # Section-level retrieval — use cache for repeat reads (OPS-04)
            id_row = self._db.execute(
                "SELECT id FROM sections WHERE document_id = ? AND anchor = ? LIMIT 1",
                (doc_id, anchor),
            ).fetchone()

            if id_row is None:
                raise PageNotFoundError(
                    f"Section {anchor!r} not found in {slug!r} v{resolved_version}"
                )

            cached = self._get_section(id_row["id"])
            if cached is not None:
                full_text = cached.content_text
                title = cached.heading or doc_title
            else:
                raise PageNotFoundError(
                    f"Section {anchor!r} not found in {slug!r} v{resolved_version}"
                )
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
