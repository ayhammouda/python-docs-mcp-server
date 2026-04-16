"""FastMCP server with lifespan DI and tool registration."""
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
from mcp_server_python_docs.errors import (
    DocsServerError,
    PageNotFoundError,
    SymbolNotFoundError,
    VersionNotFoundError,
)
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
    and fails fast on missing index or unavailable FTS5.
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

    try:
        yield AppContext(db=db, index_path=index_path, synonyms=synonyms)
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


def _symbol_exists(db: sqlite3.Connection, name: str) -> bool:
    """Check if a symbol name exists in the symbols table."""
    row = db.execute(
        "SELECT 1 FROM symbols WHERE qualified_name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _do_search(
    db: sqlite3.Connection,
    synonyms: dict[str, list[str]],
    query: str,
    version: str | None,
    kind: str,
    max_results: int,
) -> SearchDocsResult:
    """Core search logic using the retrieval layer.

    Routes queries through classifier -> synonym expansion -> FTS5 or
    symbol fast-path. All domain errors (VersionNotFoundError, etc.)
    propagate up to the tool handler for isError routing (SRVR-08).
    """
    # Classify query for routing (RETR-04)
    query_type = classify_query(query, lambda q: _symbol_exists(db, q))

    # Symbol fast-path: skip FTS5 entirely
    if kind == "symbol" or (kind == "auto" and query_type == "symbol"):
        hits = lookup_symbols_exact(db, query, version, max_results)
        if hits:
            return SearchDocsResult(hits=hits)
        # Fall through to FTS if symbol lookup found nothing and kind is auto
        if kind == "symbol":
            return SearchDocsResult(hits=[], note=None)

    # FTS5 path: build match expression with synonym expansion (RETR-05)
    match_expr = build_match_expression(query, synonyms)

    # Route to appropriate FTS5 table based on kind
    if kind == "section":
        hits = search_sections(db, match_expr, version, max_results)
    elif kind == "example":
        hits = search_examples(db, match_expr, version, max_results)
    elif kind == "page":
        # Page search uses sections with broader matching
        hits = search_sections(db, match_expr, version, max_results)
    else:
        # kind == "auto": try sections first, fall back to symbols FTS
        hits = search_sections(db, match_expr, version, max_results)
        if not hits:
            hits = search_symbols(db, match_expr, version, max_results)

    return SearchDocsResult(hits=hits)


def create_server() -> FastMCP:
    """Create and configure the FastMCP server."""
    mcp = FastMCP(
        "mcp-server-python-docs",
        lifespan=app_lifespan,
    )

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=False,
        )
    )
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
        db = app_ctx.db

        try:
            return _do_search(db, app_ctx.synonyms, query, version, kind, max_results)
        except VersionNotFoundError as e:
            raise ToolError(str(e))
        except SymbolNotFoundError as e:
            raise ToolError(str(e))
        except PageNotFoundError as e:
            raise ToolError(str(e))
        except DocsServerError as e:
            raise ToolError(str(e))

    return mcp
