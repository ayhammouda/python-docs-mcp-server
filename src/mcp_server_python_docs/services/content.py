"""Content service — handles get_docs for page and section retrieval.

When anchor is provided, returns just that section.
When omitted, returns the full page with truncation/pagination.
No MCP types imported — dependency rule enforced.
"""
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
                    r[0]
                    for r in self._db.execute(
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
