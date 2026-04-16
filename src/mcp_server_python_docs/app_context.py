"""Typed application context for FastMCP lifespan DI.

The AppContext dataclass is yielded by app_lifespan and accessed in tool
handlers via ctx.request_context.lifespan_context. This replaces module-level
globals with typed dependency injection (SRVR-01).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_server_python_docs.services.content import ContentService
    from mcp_server_python_docs.services.search import SearchService
    from mcp_server_python_docs.services.version import VersionService


@dataclass
class AppContext:
    """Application context with typed dependencies for lifespan DI."""

    db: sqlite3.Connection
    index_path: Path
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    search_service: SearchService | None = None
    content_service: ContentService | None = None
    version_service: VersionService | None = None
