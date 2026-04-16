"""Retrieval layer -- pure-logic query processing, ranking, and budget enforcement.

No MCP types, no storage imports. Receives connections and data as parameters.
"""
from __future__ import annotations

from mcp_server_python_docs.retrieval.budget import apply_budget
from mcp_server_python_docs.retrieval.query import (
    build_match_expression,
    classify_query,
    expand_synonyms,
    fts5_escape,
)
from mcp_server_python_docs.retrieval.ranker import (
    lookup_symbols_exact,
    search_examples,
    search_sections,
    search_symbols,
)

__all__ = [
    "apply_budget",
    "build_match_expression",
    "classify_query",
    "expand_synonyms",
    "fts5_escape",
    "lookup_symbols_exact",
    "search_examples",
    "search_sections",
    "search_symbols",
]
