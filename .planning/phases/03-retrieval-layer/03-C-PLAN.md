---
phase: 3
plan: C
title: "Unicode-Safe Budget Enforcement"
wave: 1
depends_on: []
files_modified:
  - src/mcp_server_python_docs/retrieval/budget.py
  - tests/test_retrieval.py
requirements:
  - RETR-08
autonomous: true
---

# Plan C: Unicode-Safe Budget Enforcement

<objective>
Create `retrieval/budget.py` with `apply_budget()` — the single function that enforces truncation and pagination for every content-returning tool. Truncation is Unicode-safe: never splits a codepoint, never splits a combining character sequence (RETR-08). Includes comprehensive tests with 4-byte emoji, combining characters, and edge cases.
</objective>

<must_haves>
- apply_budget never splits a Unicode codepoint
- apply_budget never splits a base character from its combining marks
- Returns (text, truncated_flag, next_start_index)
- Handles start_index beyond text length gracefully
- Handles empty text, zero max_chars edge cases
</must_haves>

## Tasks

### Task 03-C-01: Create budget.py with apply_budget

<read_first>
- src/mcp_server_python_docs/models.py (GetDocsResult — truncated, next_start_index fields)
- python-docs-mcp-server-build-guide.md lines 134-141 (§5 token efficiency, budget enforcement)
</read_first>

<action>
Create `src/mcp_server_python_docs/retrieval/budget.py` with:

```python
"""Unicode-safe budget enforcement for content truncation.

The single function apply_budget() handles all truncation and pagination
for content-returning tools. It guarantees:
- Never splits a Unicode codepoint (Python 3 str slicing is codepoint-safe)
- Never splits a base character from its combining marks
- Returns truncation flag and next_start_index for pagination
"""
from __future__ import annotations

import unicodedata


def apply_budget(
    text: str,
    max_chars: int,
    start_index: int = 0,
) -> tuple[str, bool, int | None]:
    """Apply character budget with Unicode-safe truncation.

    Args:
        text: Full text content to truncate.
        max_chars: Maximum characters to return.
        start_index: Starting position for pagination (codepoint offset).

    Returns:
        Tuple of (truncated_text, is_truncated, next_start_index).
        next_start_index is None when not truncated (all remaining text fits).
    """
    if not text or max_chars <= 0:
        return ("", len(text) > 0, 0 if len(text) > 0 else None)

    if start_index >= len(text):
        return ("", False, None)

    remaining = text[start_index:]

    if len(remaining) <= max_chars:
        return (remaining, False, None)

    # Truncate at max_chars boundary
    end = start_index + max_chars

    # Back up past any combining marks (category M: Mn, Mc, Me)
    # so we don't separate a base character from its diacritics
    while end > start_index and unicodedata.category(text[end - 1]).startswith("M"):
        end -= 1

    # If we backed up to start_index (all combining marks), advance to
    # include at least the base char + its marks
    if end == start_index and start_index < len(text):
        end = start_index + 1
        while end < len(text) and unicodedata.category(text[end]).startswith("M"):
            end += 1

    result = text[start_index:end]
    truncated = end < len(text)
    next_idx = end if truncated else None

    return (result, truncated, next_idx)
```

Key design points:
- Python 3 `str` is a sequence of Unicode codepoints, so `text[i:j]` never splits a codepoint (unlike UTF-8 byte slicing)
- Combining marks (Unicode category `M*`) follow their base character. If the cut point lands on a combining mark, back up to include the full grapheme cluster
- Edge case: if everything from start_index is combining marks, include at least the base char + all following marks
- Empty text returns empty string with no truncation
- start_index beyond text length returns empty with no truncation
</action>

<acceptance_criteria>
- `src/mcp_server_python_docs/retrieval/budget.py` contains `def apply_budget(`
- Function signature is `(text: str, max_chars: int, start_index: int = 0) -> tuple[str, bool, int | None]`
- `apply_budget("hello", 3)` returns `("hel", True, 3)`
- `apply_budget("hello", 10)` returns `("hello", False, None)`
- `apply_budget("hello", 3, 3)` returns `("lo", False, None)`
- `apply_budget("", 5)` returns `("", False, None)`
- No imports from `mcp_server_python_docs.storage` or `mcp_server_python_docs.server`
</acceptance_criteria>

### Task 03-C-02: Unicode edge case tests for apply_budget

<read_first>
- src/mcp_server_python_docs/retrieval/budget.py (just created)
- tests/test_retrieval.py (add to existing test file)
</read_first>

<action>
Add tests to `tests/test_retrieval.py`:

1. **`test_budget_basic_truncation`**: `apply_budget("hello world", 5)` -> `("hello", True, 5)`. Verify text, flag, and next_index.

2. **`test_budget_no_truncation`**: `apply_budget("short", 100)` -> `("short", False, None)`.

3. **`test_budget_exact_boundary`**: `apply_budget("12345", 5)` -> `("12345", False, None)`. Text exactly fits budget.

4. **`test_budget_pagination`**: Chain two calls:
   - `apply_budget("hello world!", 5, 0)` -> `("hello", True, 5)`
   - `apply_budget("hello world!", 5, 5)` -> `(" worl", True, 10)`
   - `apply_budget("hello world!", 5, 10)` -> `("d!", False, None)`

5. **`test_budget_emoji_4byte`**: Text = `"Hello 🎉 World"`. Budget=7. Assert emoji is not split. 🎉 is a single codepoint in Python 3 so `text[:7]` = `"Hello 🎉"` is safe. Result should include the emoji if budget allows, exclude cleanly if not.

6. **`test_budget_combining_character`**: Text = `"caf\u0301e"` (café with combining acute accent). Budget=3. The `\u0301` combining mark must not be separated from `f`. Expected: backs up past the combining mark, returns `"ca"` (truncated at 2), not `"caf"` which would orphan the combining mark on next page.

7. **`test_budget_combining_at_boundary`**: Text = `"na\u0308ive"` (naïve with combining diaeresis). Test various budget sizes to ensure combining mark stays with base char.

8. **`test_budget_empty_text`**: `apply_budget("", 5)` -> `("", False, None)`.

9. **`test_budget_start_beyond_length`**: `apply_budget("hello", 5, 100)` -> `("", False, None)`.

10. **`test_budget_zero_max_chars`**: `apply_budget("hello", 0)` -> handles gracefully (returns empty with truncation flag).

11. **`test_budget_single_char`**: `apply_budget("x", 1)` -> `("x", False, None)`.

12. **`test_budget_cjk_characters`**: Text with CJK characters `"日本語テスト"`. Budget=3. Returns first 3 CJK chars without splitting.

13. **`test_budget_flag_emoji_sequence`**: Text with flag emoji (multiple codepoints for country flags like 🇺🇸). Verify no crash on boundary.
</action>

<acceptance_criteria>
- `tests/test_retrieval.py` contains `test_budget_basic_truncation`
- `tests/test_retrieval.py` contains `test_budget_emoji_4byte`
- `tests/test_retrieval.py` contains `test_budget_combining_character`
- `tests/test_retrieval.py` contains `test_budget_pagination`
- `uv run pytest tests/test_retrieval.py -x -q -k "budget" 2>&1` exits 0
- Combining character test verifies the combining mark is not separated from its base character
</acceptance_criteria>

<verification>
```bash
uv run pytest tests/test_retrieval.py -x -q -k "budget" 2>&1
```
</verification>
