"""Ingest symbols from Python docs objects.inv via sphobjinv."""
from __future__ import annotations

import logging
import sqlite3

import sphobjinv as soi

from mcp_server_python_docs.storage.db import bootstrap_schema

logger = logging.getLogger(__name__)

# Priority ordering for duplicate symbol resolution (INGR-I-05)
# Lower number = higher priority
ROLE_PRIORITY: dict[str, int] = {
    "class": 0,
    "exception": 1,
    "function": 2,
    "method": 3,
    "attribute": 4,
    "data": 5,
    "module": 6,
}


def _expand_uri(obj: soi.DataObjStr) -> str:
    """Expand $ shorthand in URI to full qualified name (INGR-I-03).

    objects.inv uses $ as shorthand for the object name.
    E.g., uri="library/asyncio-task.html#$" with name="asyncio.TaskGroup"
    becomes "library/asyncio-task.html#asyncio.TaskGroup"
    """
    uri: str = obj.uri  # type: ignore[assignment]  # sphobjinv lacks type stubs
    if "$" in uri:
        uri = uri.replace("$", obj.name)  # type: ignore[arg-type]
    return uri


def _get_display_name(obj: soi.DataObjStr) -> str:
    """Get display name, falling back to obj.name when dispname is '-' (INGR-I-04)."""
    if obj.dispname == "-":  # type: ignore[comparison-overlap]  # sphobjinv lacks stubs
        return obj.name  # type: ignore[return-value]
    return obj.dispname  # type: ignore[return-value]


def _extract_module(qualified_name: str) -> str | None:
    """Extract module from qualified name.

    'asyncio.TaskGroup' -> 'asyncio'
    'json.dumps' -> 'json'
    'os.path.join' -> 'os.path'
    """
    parts = qualified_name.rsplit(".", 1)
    if len(parts) > 1:
        return parts[0]
    return None


def _normalize_name(name: str) -> str:
    """Normalize a symbol name for search.

    Lowercases and strips leading/trailing whitespace.
    Preserves dots and underscores for identifier matching.
    """
    return name.strip().lower()


def ingest_inventory(
    conn: sqlite3.Connection, version: str, *, is_default: bool = False
) -> int:
    """Download objects.inv for a Python version and populate symbols.

    Args:
        conn: Read-write SQLite connection
        version: Python version string (e.g., "3.13")
        is_default: Whether this version is the default for queries (MVER-02)

    Returns:
        Number of symbols inserted

    Steps:
    1. Bootstrap schema (idempotent)
    2. Insert/update doc_set for this version
    3. Download objects.inv via sphobjinv
    4. Parse each DataObjStr into a symbol row
    5. Handle duplicates via priority ordering (INGR-I-05)
    6. Populate symbols_fts in same transaction (INGR-I-06)
    """
    bootstrap_schema(conn)

    # Upsert doc_set for this version
    conn.execute(
        "INSERT OR REPLACE INTO doc_sets "
        "(source, version, language, label, is_default, base_url) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "python-docs",
            version,
            "en",
            f"Python {version}",
            1 if is_default else 0,
            f"https://docs.python.org/{version}/",
        ),
    )
    doc_set_id = conn.execute(
        "SELECT id FROM doc_sets "
        "WHERE source = 'python-docs' AND version = ? AND language = 'en'",
        (version,),
    ).fetchone()[0]

    # Clear existing symbols for this doc_set (re-ingestion support)
    conn.execute("DELETE FROM symbols WHERE doc_set_id = ?", (doc_set_id,))

    # Validate version format before URL construction (WR-04)
    import re

    if not re.match(r"^\d+\.\d+$", version):
        from mcp_server_python_docs.errors import IngestionError

        raise IngestionError(f"Invalid version format: {version!r}")

    # Download and parse objects.inv (INGR-I-01)
    url = f"https://docs.python.org/{version}/objects.inv"
    logger.info(f"Downloading {url}...")
    inv = soi.Inventory(url=url)  # type: ignore[call-arg]  # sphobjinv lacks type stubs
    logger.info(f"Downloaded {len(inv.objects)} inventory objects")

    # Filter to Python domain objects and collect by qualified_name
    # for duplicate resolution (INGR-I-05)
    best_symbols: dict[str, tuple[soi.DataObjStr, int]] = {}

    for obj in inv.objects:
        if obj.domain != "py":
            continue

        name = obj.name
        role = obj.role
        priority = ROLE_PRIORITY.get(role, 99)

        key = name  # Group by qualified_name for dedup
        if key not in best_symbols or priority < best_symbols[key][1]:
            best_symbols[key] = (obj, priority)

    # Insert symbols (INGR-I-02)
    count = 0
    for name, (obj, _priority) in best_symbols.items():
        uri = _expand_uri(obj)
        module = _extract_module(name)
        anchor_part = uri.split("#", 1)[1] if "#" in uri else None

        conn.execute(
            "INSERT OR IGNORE INTO symbols "
            "(doc_set_id, qualified_name, normalized_name, module, "
            "symbol_type, uri, anchor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                doc_set_id,
                name,
                _normalize_name(name),
                module,
                obj.role,
                uri,
                anchor_part,
            ),
        )
        count += 1

    conn.commit()

    # Rebuild symbols_fts from symbols table (INGR-I-06)
    # For external-content FTS5 tables, use the 'rebuild' command instead of
    # DELETE + INSERT. The rebuild command re-reads all content from the
    # content table and repopulates the FTS index atomically.
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.commit()

    logger.info(f"Ingested {count} symbols for Python {version}")
    return count
