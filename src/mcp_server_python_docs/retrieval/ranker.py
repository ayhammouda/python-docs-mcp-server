"""BM25 ranking with column weights and FTS5 snippets.

Search functions that execute FTS5 queries with BM25 column weighting
and snippet() excerpts. All functions return list[SymbolHit] with the
locked hit shape (RETR-09).

Receives sqlite3.Connection as parameter -- does not import storage.
"""
from __future__ import annotations

import logging
import sqlite3

from mcp_server_python_docs.models import SymbolHit

logger = logging.getLogger(__name__)


def _normalize_scores(hits: list[SymbolHit]) -> list[SymbolHit]:
    """Normalize BM25 scores to [0.1, 1.0] range.

    BM25 returns negative scores (lower = better). This normalizes
    to a positive range where 1.0 is best match and 0.1 is worst
    in the batch.
    """
    if not hits:
        return hits
    if len(hits) == 1:
        # Single hit always gets score 1.0
        return [hits[0].model_copy(update={"score": 1.0})]

    raw_scores = [h.score for h in hits]
    min_score = min(raw_scores)
    max_score = max(raw_scores)

    if max_score == min_score:
        return [h.model_copy(update={"score": 1.0}) for h in hits]

    normalized = []
    for hit in hits:
        # BM25 scores are negative; lower (more negative) = better
        # Normalize so the best (most negative) gets 1.0
        norm = 0.1 + 0.9 * (max_score - hit.score) / (max_score - min_score)
        normalized.append(hit.model_copy(update={"score": round(norm, 4)}))

    return normalized


def search_sections(
    conn: sqlite3.Connection,
    match_expr: str,
    version: str | None,
    max_results: int,
) -> list[SymbolHit]:
    """Search sections via FTS5 with BM25 column weights (RETR-06, RETR-07).

    Column weights: heading (10.0) > content_text (1.0).
    Returns ~200-char FTS5 snippet() excerpts.

    Args:
        conn: Read-only SQLite connection.
        match_expr: FTS5-safe MATCH expression (from fts5_escape or
            build_match_expression).
        version: Python version filter, or None for all versions.
        max_results: Maximum number of hits.

    Returns:
        List of SymbolHit with kind="section" and FTS5 snippets.
    """
    try:
        cursor = conn.execute(
            """
            SELECT s.id, s.heading, s.uri, s.anchor,
                   d.version, doc.slug,
                   bm25(sections_fts, 10.0, 1.0) as score,
                   snippet(sections_fts, 1, '**', '**', '...', 32) as snippet_text
            FROM sections_fts
            JOIN sections s ON sections_fts.rowid = s.id
            JOIN documents doc ON s.document_id = doc.id
            JOIN doc_sets d ON doc.doc_set_id = d.id
            WHERE sections_fts MATCH ?
              AND (? IS NULL OR d.version = ?)
            ORDER BY bm25(sections_fts, 10.0, 1.0)
            LIMIT ?
            """,
            (match_expr, version, version, max_results),
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        logger.warning("FTS5 query failed for sections: %r", match_expr)
        return []

    hits = [
        SymbolHit(
            uri=row["uri"],
            title=row["heading"],
            kind="section",
            snippet=row["snippet_text"] or "",
            score=row["score"],
            version=row["version"],
            slug=row["slug"],
            anchor=row["anchor"],
        )
        for row in rows
    ]

    return _normalize_scores(hits)


def search_symbols(
    conn: sqlite3.Connection,
    match_expr: str,
    version: str | None,
    max_results: int,
) -> list[SymbolHit]:
    """Search symbols via FTS5 with BM25 column weights (RETR-06).

    Column weights: qualified_name (10.0) > module (1.0).

    Args:
        conn: Read-only SQLite connection.
        match_expr: FTS5-safe MATCH expression.
        version: Python version filter, or None for all versions.
        max_results: Maximum number of hits.

    Returns:
        List of SymbolHit with kind from symbol_type.
    """
    try:
        cursor = conn.execute(
            """
            SELECT sym.id, sym.qualified_name, sym.symbol_type, sym.uri,
                   sym.anchor, sym.module, d.version,
                   bm25(symbols_fts, 10.0, 1.0) as score,
                   snippet(symbols_fts, 0, '**', '**', '...', 32) as snippet_text
            FROM symbols_fts
            JOIN symbols sym ON symbols_fts.rowid = sym.id
            JOIN doc_sets d ON sym.doc_set_id = d.id
            WHERE symbols_fts MATCH ?
              AND (? IS NULL OR d.version = ?)
            ORDER BY bm25(symbols_fts, 10.0, 1.0)
            LIMIT ?
            """,
            (match_expr, version, version, max_results),
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        logger.warning("FTS5 query failed for symbols: %r", match_expr)
        return []

    hits = [
        SymbolHit(
            uri=row["uri"],
            title=row["qualified_name"],
            kind=row["symbol_type"] or "symbol",
            snippet=row["snippet_text"] or "",
            score=row["score"],
            version=row["version"],
            slug=row["uri"].split("#")[0] if "#" in row["uri"] else row["uri"],
            anchor=row["anchor"] or "",
        )
        for row in rows
    ]

    return _normalize_scores(hits)


def search_examples(
    conn: sqlite3.Connection,
    match_expr: str,
    version: str | None,
    max_results: int,
) -> list[SymbolHit]:
    """Search examples via FTS5 (RETR-06).

    Args:
        conn: Read-only SQLite connection.
        match_expr: FTS5-safe MATCH expression.
        version: Python version filter, or None for all versions.
        max_results: Maximum number of hits.

    Returns:
        List of SymbolHit with kind="example" or "doctest".
    """
    try:
        cursor = conn.execute(
            """
            SELECT e.id, e.code, e.is_doctest,
                   s.heading, s.uri as section_uri, s.anchor,
                   d.version, doc.slug,
                   bm25(examples_fts) as score,
                   snippet(examples_fts, 0, '**', '**', '...', 32) as snippet_text
            FROM examples_fts
            JOIN examples e ON examples_fts.rowid = e.id
            JOIN sections s ON e.section_id = s.id
            JOIN documents doc ON s.document_id = doc.id
            JOIN doc_sets d ON doc.doc_set_id = d.id
            WHERE examples_fts MATCH ?
              AND (? IS NULL OR d.version = ?)
            ORDER BY bm25(examples_fts)
            LIMIT ?
            """,
            (match_expr, version, version, max_results),
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        logger.warning("FTS5 query failed for examples: %r", match_expr)
        return []

    hits = [
        SymbolHit(
            uri=row["section_uri"],
            title=row["heading"],
            kind="doctest" if row["is_doctest"] else "example",
            snippet=row["snippet_text"] or "",
            score=row["score"],
            version=row["version"],
            slug=row["slug"],
            anchor=row["anchor"] or "",
        )
        for row in rows
    ]

    return _normalize_scores(hits)


def lookup_symbols_exact(
    conn: sqlite3.Connection,
    query: str,
    version: str | None,
    max_results: int,
) -> list[SymbolHit]:
    """Direct symbol table lookup for the symbol fast-path (RETR-04).

    Bypasses FTS5 entirely. Uses exact match first, then LIKE prefix
    match. Scores: exact match = 1.0, prefix match = 0.8.

    Args:
        conn: Read-only SQLite connection.
        query: Symbol name to look up.
        version: Python version filter, or None for all versions.
        max_results: Maximum number of hits.

    Returns:
        List of SymbolHit with kind from symbol_type.
    """
    # Escape LIKE wildcards in user input
    escaped_query = (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )

    cursor = conn.execute(
        """
        SELECT s.qualified_name, s.symbol_type, s.uri, s.anchor,
               s.module, d.version
        FROM symbols s
        JOIN doc_sets d ON s.doc_set_id = d.id
        WHERE (s.qualified_name = ? OR s.qualified_name LIKE ? ESCAPE '\\')
          AND (? IS NULL OR d.version = ?)
        ORDER BY CASE WHEN s.qualified_name = ? THEN 0 ELSE 1 END
        LIMIT ?
        """,
        (query, f"%{escaped_query}%", version, version, query, max_results),
    )
    rows = cursor.fetchall()

    return [
        SymbolHit(
            uri=row["uri"],
            title=row["qualified_name"],
            kind=row["symbol_type"] or "symbol",
            snippet="",
            score=1.0 if row["qualified_name"] == query else 0.8,
            version=row["version"],
            slug=row["uri"].split("#")[0] if "#" in row["uri"] else row["uri"],
            anchor=row["anchor"] or "",
        )
        for row in rows
    ]
