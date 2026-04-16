"""Version service — lists available documentation versions.

Trivial service that queries doc_sets table. Kept as a class
for symmetry with SearchService and ContentService.
No MCP types imported — dependency rule enforced.
"""
from __future__ import annotations

import sqlite3

from mcp_server_python_docs.models import ListVersionsResult, VersionInfo
from mcp_server_python_docs.services.observability import log_tool_call


class VersionService:
    """Version listing service for list_versions tool.

    Trivial service that queries doc_sets table. Kept as a class
    for symmetry with SearchService and ContentService.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    @log_tool_call("list_versions")
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
