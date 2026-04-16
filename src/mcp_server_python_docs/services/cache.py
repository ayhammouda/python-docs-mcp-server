"""LRU cache wrappers for hot read paths (OPS-04, OPS-05).

Process-lifetime-scoped caches with no TTL and no invalidation.
Users restart the server on rebuild (documented in PUBL-05).

The cached functions are created as closures that capture the
sqlite3.Connection at construction time. This is necessary because
lru_cache requires hashable arguments, and sqlite3.Connection is
not hashable.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from functools import lru_cache
from typing import NamedTuple


class CachedSection(NamedTuple):
    """Cached section data -- lightweight container for LRU cache."""

    heading: str
    content_text: str
    anchor: str
    uri: str
    document_id: int


class CachedSymbol(NamedTuple):
    """Cached symbol resolution result."""

    qualified_name: str
    symbol_type: str
    uri: str
    anchor: str
    module: str
    version: str


def create_section_cache(db: sqlite3.Connection) -> Callable[[int], CachedSection | None]:
    """Create an LRU-cached section lookup function (OPS-04).

    Args:
        db: Read-only SQLite connection (captured by closure).

    Returns:
        A function ``get_section(section_id: int) -> CachedSection | None``
        with maxsize=512.
    """

    @lru_cache(maxsize=512)
    def get_section_cached(section_id: int) -> CachedSection | None:
        row = db.execute(
            """
            SELECT heading, content_text, anchor, uri, document_id
            FROM sections
            WHERE id = ?
            """,
            (section_id,),
        ).fetchone()
        if row is None:
            return None
        return CachedSection(
            heading=row["heading"] or "",
            content_text=row["content_text"] or "",
            anchor=row["anchor"] or "",
            uri=row["uri"] or "",
            document_id=row["document_id"],
        )

    return get_section_cached


def create_symbol_cache(db: sqlite3.Connection) -> Callable[[str, str], CachedSymbol | None]:
    """Create an LRU-cached symbol resolution function (OPS-04).

    Args:
        db: Read-only SQLite connection (captured by closure).

    Returns:
        A function ``resolve_symbol(qualified_name: str, version: str) -> CachedSymbol | None``
        with maxsize=128.
    """

    @lru_cache(maxsize=128)
    def resolve_symbol_cached(
        qualified_name: str, version: str
    ) -> CachedSymbol | None:
        row = db.execute(
            """
            SELECT s.qualified_name, s.symbol_type, s.uri, s.anchor,
                   s.module, d.version
            FROM symbols s
            JOIN doc_sets d ON s.doc_set_id = d.id
            WHERE s.qualified_name = ? AND d.version = ?
            LIMIT 1
            """,
            (qualified_name, version),
        ).fetchone()
        if row is None:
            return None
        return CachedSymbol(
            qualified_name=row["qualified_name"],
            symbol_type=row["symbol_type"] or "symbol",
            uri=row["uri"],
            anchor=row["anchor"] or "",
            module=row["module"] or "",
            version=row["version"],
        )

    return resolve_symbol_cached
