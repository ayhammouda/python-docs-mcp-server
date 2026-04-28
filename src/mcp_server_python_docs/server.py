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
from typing import Annotated, Literal

import platformdirs
import yaml
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_server_python_docs.app_context import AppContext
from mcp_server_python_docs.detection import detect_python_version, match_to_indexed
from mcp_server_python_docs.errors import DocsServerError
from mcp_server_python_docs.models import (
    DetectPythonVersionResult,
    GetDocsResult,
    ListVersionsResult,
    SearchDocsResult,
)
from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.services.search import SearchService
from mcp_server_python_docs.services.version import VersionService
from mcp_server_python_docs.storage.db import get_readonly_connection

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
        from mcp_server_python_docs.ingestion.cpython_versions import (
            SUPPORTED_DOC_VERSIONS_CSV,
        )

        msg = (
            f"No index found at {index_path}\n"
            f"Run: mcp-server-python-docs build-index --versions "
            f"{SUPPORTED_DOC_VERSIONS_CSV}"
        )
        logger.error(msg)
        print(msg, file=sys.stderr)
        raise SystemExit(1)

    # Load synonyms from package data (SRVR-11, SRVR-12)
    synonyms = _load_synonyms()
    logger.info("Loaded %d synonym entries", len(synonyms))

    # Open read-only connection (STOR-06, STOR-07)
    db = get_readonly_connection(index_path)

    try:
        # Check FTS5 (STOR-08)
        _assert_fts5(db)

        # Construct service instances (Phase 5 — service layer wiring)
        search_svc = SearchService(db, synonyms)
        content_svc = ContentService(db)
        version_svc = VersionService(db)

        # Detect user's Python version and match to indexed versions
        detected_ver, detected_src = detect_python_version()
        indexed_versions = [
            r[0] for r in db.execute("SELECT version FROM doc_sets ORDER BY version").fetchall()
        ]
        matched = match_to_indexed(detected_ver, indexed_versions)
        if matched:
            logger.info("User Python %s matches indexed version — using as default", matched)
        else:
            logger.info(
                "User Python %s not in index %s — using normal default",
                detected_ver,
                indexed_versions,
            )

        try:
            yield AppContext(
                db=db,
                index_path=index_path,
                synonyms=synonyms,
                search_service=search_svc,
                content_service=content_svc,
                version_service=version_svc,
                detected_python_version=matched,
                detected_python_source=detected_src,
            )
        except Exception:
            # HYGN-05: log lifespan errors, write last-error.log, re-raise original
            error_msg = traceback.format_exc()
            logger.error("Lifespan error: %s", error_msg)
            try:
                error_log = cache_dir / "last-error.log"
                error_log.write_text(error_msg)
            except Exception:
                pass
            raise
    finally:
        db.close()


# Shared tool annotations — all tools are read-only (SRVR-02, SRVR-03, SRVR-04)
_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

SearchQueryParam = Annotated[
    str,
    Field(
        max_length=500,
        description="Search query - Python symbol (asyncio.TaskGroup) or concept (parse json)",
    ),
]
VersionParam = Annotated[
    str | None,
    Field(description="Python version (e.g. '3.13'). Defaults to latest."),
]
SearchKindParam = Annotated[
    Literal["auto", "page", "symbol", "section", "example"],
    Field(
        description=(
            "Search type. Use 'symbol' for API lookups, "
            "'example' for code samples, 'auto' otherwise."
        )
    ),
]
MaxResultsParam = Annotated[
    int,
    Field(ge=1, le=20, description="Maximum number of results to return."),
]
SlugParam = Annotated[
    str,
    Field(max_length=500, description="Page slug (e.g. 'library/asyncio-task.html')"),
]
AnchorParam = Annotated[
    str | None,
    Field(description="Section anchor for section-only retrieval"),
]
MaxCharsParam = Annotated[
    int,
    Field(ge=100, le=50000, description="Maximum characters to return"),
]
StartIndexParam = Annotated[
    int,
    Field(ge=0, description="Start position for pagination"),
]


def create_server() -> FastMCP:
    """Create and configure the FastMCP server."""
    mcp = FastMCP(
        "mcp-server-python-docs",
        lifespan=app_lifespan,
    )

    @mcp.tool(annotations=_TOOL_ANNOTATIONS)
    def search_docs(
        query: SearchQueryParam,
        version: VersionParam = None,
        kind: SearchKindParam = "auto",
        max_results: MaxResultsParam = 5,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> SearchDocsResult:
        """Search Python documentation. Use kind='symbol' for API lookups
        (asyncio.TaskGroup), kind='example' for code samples, kind='auto' otherwise.
        When version is omitted, searches across all versions. Pass the version
        from each hit's version field to get_docs for consistent results."""
        app_ctx: AppContext = ctx.request_context.lifespan_context
        try:
            return app_ctx.search_service.search(query, version, kind, max_results)
        except DocsServerError as e:
            raise ToolError(str(e))
        except Exception as e:
            logger.exception("Unexpected error in search_docs")
            raise ToolError(f"Internal error: {type(e).__name__}")

    @mcp.tool(annotations=_TOOL_ANNOTATIONS)
    def get_docs(
        slug: SlugParam,
        version: VersionParam = None,
        anchor: AnchorParam = None,
        max_chars: MaxCharsParam = 8000,
        start_index: StartIndexParam = 0,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> GetDocsResult:
        """Retrieve a documentation page or specific section. Provide anchor for
        section-only retrieval (much cheaper). Pagination via start_index."""
        app_ctx: AppContext = ctx.request_context.lifespan_context
        # Auto-default to detected Python version when no version specified
        if version is None and app_ctx.detected_python_version:
            version = app_ctx.detected_python_version
        try:
            return app_ctx.content_service.get_docs(
                slug, version, anchor, max_chars, start_index
            )
        except DocsServerError as e:
            raise ToolError(str(e))
        except Exception as e:
            logger.exception("Unexpected error in get_docs")
            raise ToolError(f"Internal error: {type(e).__name__}")

    @mcp.tool(annotations=_TOOL_ANNOTATIONS)
    def list_versions(
        ctx: Context = None,  # type: ignore[assignment]
    ) -> ListVersionsResult:
        """List Python documentation versions available in this index."""
        app_ctx: AppContext = ctx.request_context.lifespan_context
        try:
            return app_ctx.version_service.list_versions()
        except DocsServerError as e:
            raise ToolError(str(e))
        except Exception as e:
            logger.exception("Unexpected error in list_versions")
            raise ToolError(f"Internal error: {type(e).__name__}")

    @mcp.tool(annotations=_TOOL_ANNOTATIONS)
    def detect_python_version(
        ctx: Context = None,  # type: ignore[assignment]
    ) -> DetectPythonVersionResult:
        """Detect the Python version in the user's environment.
        Returns the detected version, how it was found, and whether it
        matches an indexed documentation set."""
        app_ctx: AppContext = ctx.request_context.lifespan_context
        detected_ver = app_ctx.detected_python_version

        # Re-run detection to get the raw version even if it didn't match
        from mcp_server_python_docs.detection import detect_python_version as _detect

        raw_ver, raw_src = _detect()

        return DetectPythonVersionResult(
            detected_version=raw_ver,
            source=raw_src,
            matched_index_version=detected_ver,
            is_default=detected_ver is not None,
        )

    # SRVR-07: _meta hint for get_docs tool.
    # FastMCP 1.27 does not expose a public API for setting _meta on tool
    # definitions. Deferred until the mcp SDK adds _meta support to the
    # decorator API or tool manager. The hint is advisory — clients work
    # correctly without it.

    return mcp
