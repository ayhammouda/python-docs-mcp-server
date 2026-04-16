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

from mcp_server_python_docs.app_context import AppContext
from mcp_server_python_docs.errors import FTS5UnavailableError, IndexNotBuiltError
from mcp_server_python_docs.models import SearchDocsResult, SymbolHit

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
    logger.info(f"Loaded {len(synonyms)} synonym entries")

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
        logger.error(f"Lifespan error: {error_msg}")
        try:
            error_log = cache_dir / "last-error.log"
            error_log.write_text(error_msg)
        except Exception:
            pass
        raise SystemExit(1)
    finally:
        db.close()


def create_server() -> FastMCP:
    """Create and configure the FastMCP server."""
    mcp = FastMCP(
        "mcp-server-python-docs",
        lifespan=app_lifespan,
    )

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        }
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

        # Phase 1: symbol fast-path only (D-03)
        if kind not in ("symbol", "auto"):
            logger.info(
                f"search_docs: kind={kind} routes to symbol fast-path in Phase 1"
            )

        # Query the symbols table directly
        db = app_ctx.db
        cursor = db.execute(
            "SELECT qualified_name, symbol_type, uri, anchor FROM symbols "
            "WHERE qualified_name = ? OR qualified_name LIKE ? "
            "ORDER BY CASE WHEN qualified_name = ? THEN 0 ELSE 1 END "
            "LIMIT ?",
            (query, f"%{query}%", query, max_results),
        )
        rows = cursor.fetchall()

        if not rows:
            # D-01: non-matching queries return empty hits with note
            note = None
            if "." not in query:
                note = (
                    "Full-text search available after content ingestion. "
                    "For now, search_docs resolves Python identifiers "
                    "like asyncio.TaskGroup."
                )
            return SearchDocsResult(hits=[], note=note)

        hits = []
        for row in rows:
            qualified_name = row["qualified_name"]
            symbol_type = row["symbol_type"]
            uri = row["uri"]
            anchor = row["anchor"]

            # Determine version from doc_sets join (simplified for Phase 1)
            hit_version = version or "3.13"
            hits.append(
                SymbolHit(
                    uri=uri,
                    title=qualified_name,
                    kind=symbol_type or "symbol",
                    snippet="",
                    score=1.0 if qualified_name == query else 0.5,
                    version=hit_version,
                    slug=uri.split("#")[0] if "#" in uri else uri,
                    anchor=anchor or "",
                )
            )

        return SearchDocsResult(hits=hits)

    return mcp
