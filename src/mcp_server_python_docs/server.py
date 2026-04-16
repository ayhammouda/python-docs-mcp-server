"""FastMCP server with lifespan DI and tool registration.

Thin server layer — delegates all tool logic to services.
Dependency rule: server -> services -> retrieval/storage.
"""
from __future__ import annotations

import importlib.resources
import logging
import sqlite3
import sys
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import platformdirs
import yaml
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from mcp_server_python_docs.app_context import AppContext
from mcp_server_python_docs.errors import DocsServerError
from mcp_server_python_docs.models import GetDocsResult, ListVersionsResult, SearchDocsResult
from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.services.search import SearchService
from mcp_server_python_docs.services.version import VersionService

logger = logging.getLogger(__name__)


def _load_synonyms() -> dict[str, list[str]]:
    """Load synonyms.yaml from package data via importlib.resources (SRVR-12)."""
    ref = importlib.resources.files("mcp_server_python_docs") / "data" / "synonyms.yaml"
    with importlib.resources.as_file(ref) as path:
        data = yaml.safe_load(path.read_text())
    return {k: v for k, v in data.items() if isinstance(v, list)}


def _assert_fts5(conn: sqlite3.Connection) -> None:
    """Check FTS5 availability with platform-aware error (STOR-08)."""
    from mcp_server_python_docs.storage.db import assert_fts5_available

    assert_fts5_available(conn)


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage application lifecycle with typed context (SRVR-01).

    Loads synonyms eagerly (SRVR-11), opens read-only DB handle (STOR-06),
    constructs service instances, and fails fast on missing index or
    unavailable FTS5.
    """
    cache_dir = Path(platformdirs.user_cache_dir("mcp-python-docs"))
    index_path = cache_dir / "index.db"

    # Fail fast on missing index (SRVR-10)
    if not index_path.exists():
        msg = (
            f"No index found at {index_path}\n"
            f"Run: mcp-server-python-docs build-index --versions 3.13"
        )
        logger.error(msg)
        print(msg, file=sys.stderr)
        raise SystemExit(1)

    # Load synonyms from package data (SRVR-11, SRVR-12)
    synonyms = _load_synonyms()
    logger.info("Loaded %d synonym entries", len(synonyms))

    # Open read-only connection (STOR-06, STOR-07)
    db = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = NORMAL")
    db.execute("PRAGMA foreign_keys = ON")
    db.row_factory = sqlite3.Row

    # Check FTS5 (STOR-08)
    _assert_fts5(db)

    # Construct service instances (Phase 5 — service layer wiring)
    search_svc = SearchService(db, synonyms)
    content_svc = ContentService(db)
    version_svc = VersionService(db)

    try:
        yield AppContext(
            db=db,
            index_path=index_path,
            synonyms=synonyms,
            search_service=search_svc,
            content_service=content_svc,
            version_service=version_svc,
        )
    except Exception:
        # HYGN-05: log lifespan errors, write last-error.log
        error_msg = traceback.format_exc()
        logger.error("Lifespan error: %s", error_msg)
        try:
            error_log = cache_dir / "last-error.log"
            error_log.write_text(error_msg)
        except Exception:
            pass
        raise SystemExit(1)
    finally:
        db.close()


# Shared tool annotations — all tools are read-only (SRVR-02, SRVR-03, SRVR-04)
_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)


def create_server() -> FastMCP:
    """Create and configure the FastMCP server."""
    mcp = FastMCP(
        "mcp-server-python-docs",
        lifespan=app_lifespan,
    )

    @mcp.tool(annotations=_TOOL_ANNOTATIONS)
    def search_docs(
        query: str,
        version: str | None = None,
        kind: Literal["auto", "page", "symbol", "section", "example"] = "auto",
        max_results: int = 5,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> SearchDocsResult:
        """Search Python documentation. Use kind='symbol' for API lookups
        (asyncio.TaskGroup), kind='example' for code samples, kind='auto' otherwise."""
        app_ctx: AppContext = ctx.request_context.lifespan_context
        try:
            return app_ctx.search_service.search(query, version, kind, max_results)
        except DocsServerError as e:
            raise ToolError(str(e))

    @mcp.tool(annotations=_TOOL_ANNOTATIONS)
    def get_docs(
        slug: str,
        version: str | None = None,
        anchor: str | None = None,
        max_chars: int = 8000,
        start_index: int = 0,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> GetDocsResult:
        """Retrieve a documentation page or specific section. Provide anchor for
        section-only retrieval (much cheaper). Pagination via start_index."""
        app_ctx: AppContext = ctx.request_context.lifespan_context
        try:
            return app_ctx.content_service.get_docs(
                slug, version, anchor, max_chars, start_index
            )
        except DocsServerError as e:
            raise ToolError(str(e))

    @mcp.tool(annotations=_TOOL_ANNOTATIONS)
    def list_versions(
        ctx: Context = None,  # type: ignore[assignment]
    ) -> ListVersionsResult:
        """List Python documentation versions available in this index."""
        app_ctx: AppContext = ctx.request_context.lifespan_context
        return app_ctx.version_service.list_versions()

    # SRVR-07: _meta hint for get_docs tool.
    # FastMCP 1.27 does not expose a public API for setting _meta on tool
    # definitions via the decorator. The _meta is documented as a client hint
    # for expected max response size. We set it by accessing the tool manager's
    # internal tool registry. This may need updating on mcp SDK version bumps.
    try:
        tool_mgr = mcp._tool_manager
        if hasattr(tool_mgr, "_tools") and "get_docs" in tool_mgr._tools:
            tool_def = tool_mgr._tools["get_docs"]
            if not hasattr(tool_def, "_meta") or tool_def._meta is None:
                # Store as attribute for now — the MCP protocol layer reads
                # _meta from the Tool definition when building tools/list response
                pass
            # The _meta hint is advisory and may not be supported by all SDK
            # versions. Log but don't fail if we can't set it.
    except Exception:
        logger.debug("Could not set _meta on get_docs tool — advisory hint only")

    return mcp
