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


def _build_concept_patterns(
    synonyms: dict[str, list[str]],
) -> dict[str, re.Pattern[str]]:
    """Pre-compile word-boundary regex patterns for multi-word synonym concepts."""
    patterns: dict[str, re.Pattern[str]] = {}
    for concept in synonyms:
        if " " in concept:
            patterns[concept] = re.compile(r"\b" + re.escape(concept) + r"\b")
    return patterns


def expand_synonyms(
    query: str,
    synonyms: dict[str, list[str]],
    *,
    _concept_patterns: dict[str, re.Pattern[str]] | None = None,
) -> set[str]:
    """Expand query using synonym table for concept search (RETR-05).

    Checks each token and multi-word phrases against the synonym table.
    Single-word concepts use exact token matching. Multi-word concepts
    use word-boundary regex to avoid substring false positives (CR-01).

    Args:
        query: User search query.
        synonyms: Mapping of concept -> list of expansion terms.
            Loaded from synonyms.yaml at startup.
        _concept_patterns: Pre-compiled regex patterns for multi-word
            concepts. Built lazily on first call if not provided.

    Returns:
        Set of terms (original + expansions). Empty set if query
        is empty.
    """
    query = query.strip()
    if not query:
        return set()

    tokens = query.lower().split()
    expanded = set(tokens)

    # Single-word concepts: exact token match (no substring false positives)
    for token in tokens:
        if token in synonyms:
            expanded.update(synonyms[token])

    # Multi-word concepts: word-boundary regex match (CR-01 fix)
    if _concept_patterns is None:
        _concept_patterns = _build_concept_patterns(synonyms)
    query_lower = query.lower()
    for concept, expansions in synonyms.items():
        if " " not in concept:
            continue
        pattern = _concept_patterns.get(concept)
        if pattern and pattern.search(query_lower):
            expanded.update(expansions)

    return expanded


def build_match_expression(
    query: str,
    synonyms: dict[str, list[str]],
    *,
    _concept_patterns: dict[str, re.Pattern[str]] | None = None,
) -> str:
    """Build a complete FTS5 MATCH expression with synonym expansion.

    If synonyms match, produces an OR-joined expression of escaped terms.
    If no synonyms match, produces the plain escaped query (implicit AND).

    Args:
        query: User search query.
        synonyms: Synonym mapping for expansion.
        _concept_patterns: Pre-compiled regex patterns for multi-word concepts.

    Returns:
        FTS5-safe MATCH expression string.
    """
    query = query.strip()
    if not query:
        return '""'

    expanded = expand_synonyms(query, synonyms, _concept_patterns=_concept_patterns)
    original_tokens = set(query.lower().split())

    # If expansion added new terms, OR-join all terms
    if expanded != original_tokens:
        escaped_terms = [fts5_escape(term) for term in sorted(expanded)]
        return " OR ".join(escaped_terms)

    # No expansion -- use plain escaped query (implicit AND)
    return fts5_escape(query)
