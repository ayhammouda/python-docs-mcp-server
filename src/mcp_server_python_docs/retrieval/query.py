"""Query processing for FTS5 search.

Provides FTS5 escape, query classification, and synonym expansion.
Pure logic -- no MCP types, no storage imports.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

# Pattern for single-word Python module names (e.g., re, os, sys, io)
_MODULE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")


def fts5_escape(query: str) -> str:
    """Escape user input for safe FTS5 MATCH queries (RETR-01).

    Wraps every whitespace-separated token in double quotes to prevent
    FTS5 operators (AND, OR, NOT, NEAR), prefix matching (*), column
    filters (:), grouping (parens), and negation (-) from being
    interpreted.

    Inside FTS5 double quotes, the only special char is the double quote
    itself, which is escaped by doubling ("").

    Args:
        query: Raw user input string.

    Returns:
        Escaped string safe for FTS5 MATCH. Empty/whitespace input
        returns '""' (matches nothing, does not crash).
    """
    # Strip null bytes and other control characters that crash FTS5
    query = query.replace("\x00", "").strip()
    if not query:
        return '""'
    tokens = query.split()
    escaped = []
    for token in tokens:
        # Strip null bytes from individual tokens too
        token = token.replace("\x00", "")
        if not token:
            continue
        # Escape internal double quotes by doubling them
        safe = token.replace('"', '""')
        # Wrap every token in double quotes
        escaped.append(f'"{safe}"')
    return " ".join(escaped) if escaped else '""'


def classify_query(
    query: str,
    symbol_exists_fn: Callable[[str], bool],
) -> Literal["symbol", "fts"]:
    """Classify whether a query should use symbol fast-path or FTS5 (RETR-04).

    Symbol-shaped queries are routed to the objects.inv symbol table
    for direct lookup, skipping FTS5 entirely.

    A query is symbol-shaped if:
    1. It contains a dot (e.g., asyncio.TaskGroup, os.path.join)
    2. It matches the lowercase identifier pattern AND exists in
       the symbol table (prevents false positives on words like
       "test", "list")

    Args:
        query: User search query.
        symbol_exists_fn: Callback that checks if a name exists in the
            symbols table. Injected by the service layer to avoid
            importing storage.

    Returns:
        "symbol" for symbol fast-path, "fts" for full-text search.
    """
    query = query.strip()
    if not query:
        return "fts"
    # Dotted names are always symbol-shaped
    if "." in query:
        return "symbol"
    # Single-word module names (re, os, sys) -- only if they exist
    # in the symbol table to avoid false positives
    if _MODULE_PATTERN.match(query) and symbol_exists_fn(query):
        return "symbol"
    return "fts"


def expand_synonyms(
    query: str,
    synonyms: dict[str, list[str]],
) -> set[str]:
    """Expand query using synonym table for concept search (RETR-05).

    Checks each token and the full query against the synonym table.
    Returns a set of terms including the original tokens plus any
    expansion values.

    Args:
        query: User search query.
        synonyms: Mapping of concept -> list of expansion terms.
            Loaded from synonyms.yaml at startup.

    Returns:
        Set of terms (original + expansions). Empty set if query
        is empty.
    """
    query = query.strip()
    if not query:
        return set()

    tokens = query.lower().split()
    expanded = set(tokens)

    # Check individual tokens against synonym keys
    for token in tokens:
        if token in synonyms:
            expanded.update(synonyms[token])

    # Check multi-word concepts (e.g., "http requests", "file io")
    query_lower = query.lower()
    for concept, expansions in synonyms.items():
        if concept in query_lower:
            expanded.update(expansions)

    return expanded


def build_match_expression(
    query: str,
    synonyms: dict[str, list[str]],
) -> str:
    """Build a complete FTS5 MATCH expression with synonym expansion.

    If synonyms match, produces an OR-joined expression of escaped terms.
    If no synonyms match, produces the plain escaped query (implicit AND).

    Args:
        query: User search query.
        synonyms: Synonym mapping for expansion.

    Returns:
        FTS5-safe MATCH expression string.
    """
    query = query.strip()
    if not query:
        return '""'

    expanded = expand_synonyms(query, synonyms)
    original_tokens = set(query.lower().split())

    # If expansion added new terms, OR-join all terms
    if expanded != original_tokens:
        escaped_terms = [fts5_escape(term) for term in sorted(expanded)]
        return " OR ".join(escaped_terms)

    # No expansion -- use plain escaped query (implicit AND)
    return fts5_escape(query)
