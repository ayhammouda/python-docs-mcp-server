"""Unicode-safe budget enforcement for content truncation.

The single function apply_budget() handles all truncation and pagination
for content-returning tools. It guarantees:
- Never splits a Unicode codepoint (Python 3 str slicing is codepoint-safe)
- Never splits a base character from its combining marks
- Returns truncation flag and next_start_index for pagination

Pure logic -- no MCP types, no storage imports.
"""
from __future__ import annotations

import unicodedata


def apply_budget(
    text: str,
    max_chars: int,
    start_index: int = 0,
) -> tuple[str, bool, int | None]:
    """Apply character budget with Unicode-safe truncation (RETR-08).

    Args:
        text: Full text content to truncate.
        max_chars: Maximum characters to return. Must be positive for
            meaningful results.
        start_index: Starting position for pagination (codepoint offset).

    Returns:
        Tuple of (truncated_text, is_truncated, next_start_index).
        next_start_index is None when not truncated (all remaining text fits).
    """
    if not text or max_chars <= 0:
        return ("", bool(text), 0 if text else None)

    if start_index >= len(text):
        return ("", False, None)

    remaining = text[start_index:]

    if len(remaining) <= max_chars:
        return (remaining, False, None)

    # Truncate at max_chars boundary
    end = start_index + max_chars

    # Back up past any combining marks (Unicode category M: Mn, Mc, Me)
    # so we don't separate a base character from its diacritics
    while end > start_index and unicodedata.category(text[end - 1]).startswith("M"):
        end -= 1

    # If we backed up to start_index (edge case: all combining marks),
    # advance to include at least the base char + its marks
    if end == start_index and start_index < len(text):
        end = start_index + 1
        while end < len(text) and unicodedata.category(text[end]).startswith("M"):
            end += 1

    result = text[start_index:end]
    truncated = end < len(text)
    next_idx = end if truncated else None

    return (result, truncated, next_idx)
