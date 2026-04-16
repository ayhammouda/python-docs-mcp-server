"""Typed application context for FastMCP lifespan DI.

The AppContext dataclass is yielded by app_lifespan and accessed in tool
handlers via ctx.request_context.lifespan_context. This replaces module-level
globals with typed dependency injection (SRVR-01).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.services.search import SearchService
from mcp_server_python_docs.services.version import VersionService


@dataclass
class AppContext:
    """Application context with typed dependencies for lifespan DI."""

    db: sqlite3.Connection
    index_path: Path
    search_service: SearchService
    content_service: ContentService
    version_service: VersionService
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    detected_python_version: str | None = None
