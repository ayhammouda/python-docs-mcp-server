---
phase: 3
plan: A
title: "Query Processing: fts5_escape, classifier, synonym expansion"
wave: 1
depends_on: []
files_modified:
  - src/mcp_server_python_docs/retrieval/__init__.py
  - src/mcp_server_python_docs/retrieval/query.py
  - tests/test_retrieval.py
requirements:
  - RETR-01
  - RETR-02
  - RETR-03
  - RETR-04
  - RETR-05
autonomous: true
---

# Plan A: Query Processing — fts5_escape, classifier, synonym expansion

<objective>
Create `retrieval/query.py` with three core functions: `fts5_escape()` that sanitizes arbitrary user input for safe FTS5 MATCH (RETR-01), `classify_query()` that detects symbol-shaped queries for fast-path routing (RETR-04), and `expand_synonyms()` that applies concept expansion before FTS5 (RETR-05). Also creates the `retrieval/__init__.py` package init with public re-exports. Includes 50+ fuzz test inputs for fts5_escape (RETR-03).
</objective>

<must_haves>
- fts5_escape never raises sqlite3.OperationalError on any input
- 50+ adversarial inputs tested end-to-end against real FTS5 MATCH
- classify_query detects dotted names and single-word module names
- expand_synonyms produces OR-joined expansion from synonym dict
- retrieval/ package has clean public API exports
</must_haves>

## Tasks

### Task 03-A-01: Create retrieval package and query.py

<read_first>
- src/mcp_server_python_docs/retrieval/ (verify does not exist yet)
- src/mcp_server_python_docs/errors.py (error classes available)
- src/mcp_server_python_docs/models.py (SymbolHit shape)
- python-docs-mcp-server-build-guide.md lines 134-167 (§5 token efficiency, §6 synonyms)
</read_first>

<action>
1. Create `src/mcp_server_python_docs/retrieval/__init__.py`:
```python
"""Retrieval layer — pure-logic query processing, ranking, and budget enforcement.

No MCP types, no storage imports. Receives connections and data as parameters.
"""
from mcp_server_python_docs.retrieval.budget import apply_budget
from mcp_server_python_docs.retrieval.query import (
    classify_query,
    expand_synonyms,
    fts5_escape,
)
from mcp_server_python_docs.retrieval.ranker import (
    search_examples,
    search_sections,
    search_symbols,
)

__all__ = [
    "apply_budget",
    "classify_query",
    "expand_synonyms",
    "fts5_escape",
    "search_examples",
    "search_sections",
    "search_symbols",
]
```

2. Create `src/mcp_server_python_docs/retrieval/query.py` with:

**`fts5_escape(query: str) -> str`**:
- Strip input, return `'""'` for empty/whitespace-only input
- Split on whitespace to get tokens
- For each token: replace `"` with `""`, wrap in double quotes
- Join tokens with space (FTS5 implicit AND)
- This unconditionally quotes every user token, preventing any FTS5 operator or special character from being interpreted

**`classify_query(query: str, symbol_exists_fn: Callable[[str], bool]) -> Literal["symbol", "fts"]`**:
- Strip input
- If `"."` in query: return `"symbol"`
- If matches regex `^[a-z_][a-z0-9_]*$` AND `symbol_exists_fn(query)` returns True: return `"symbol"`
- Otherwise: return `"fts"`
- The `symbol_exists_fn` callback avoids importing storage — injected by service layer

**`expand_synonyms(query: str, synonyms: dict[str, list[str]]) -> set[str]`**:
- Tokenize query to lowercase tokens
- Start with original tokens in result set
- For each token, if it exists as a key in synonyms dict, add all expansion values
- Also check full query (lowered) against multi-word concept keys
- Return the expanded token set

**`build_match_expression(query: str, synonyms: dict[str, list[str]]) -> str`**:
- Get expanded tokens from `expand_synonyms()`
- Escape each token with `fts5_escape()`
- Join with `" OR "` for OR-expansion
- If no synonyms matched, use the plain escaped query (implicit AND)
- Returns the ready-to-use MATCH expression string

All functions use `from __future__ import annotations`. Module-level `import re` for classify_query. No imports from storage or server packages.
</action>

<acceptance_criteria>
- `src/mcp_server_python_docs/retrieval/__init__.py` exists and contains `from mcp_server_python_docs.retrieval.query import fts5_escape`
- `src/mcp_server_python_docs/retrieval/query.py` contains `def fts5_escape(query: str) -> str:`
- `src/mcp_server_python_docs/retrieval/query.py` contains `def classify_query(`
- `src/mcp_server_python_docs/retrieval/query.py` contains `def expand_synonyms(`
- `src/mcp_server_python_docs/retrieval/query.py` contains `def build_match_expression(`
- `fts5_escape("")` returns `'""'`
- `fts5_escape('asyncio.TaskGroup')` returns `'"asyncio.TaskGroup"'`
- `fts5_escape('c++')` returns `'"c++"'`
- `fts5_escape('AND OR NOT')` returns `'"AND" "OR" "NOT"'`
- `classify_query("asyncio.TaskGroup", lambda q: True)` returns `"symbol"`
- `classify_query("parse json", lambda q: True)` returns `"fts"`
- `expand_synonyms("parallel", {"parallel": ["concurrent", "threading"]})` is a superset of `{"parallel", "concurrent", "threading"}`
- No imports from `mcp_server_python_docs.storage` or `mcp_server_python_docs.server` in query.py
</acceptance_criteria>

### Task 03-A-02: 50+ input fuzz test for fts5_escape (RETR-03)

<read_first>
- src/mcp_server_python_docs/retrieval/query.py (just created)
- tests/test_schema.py (test pattern: in-memory SQLite fixtures)
</read_first>

<action>
Create `tests/test_retrieval.py` with a test class/function for fts5_escape fuzz testing.

The test must:
1. Create an in-memory SQLite database with an FTS5 table:
   ```sql
   CREATE VIRTUAL TABLE test_fts USING fts5(content, tokenize="unicode61 remove_diacritics 2 tokenchars '._'")
   ```
2. Insert a few rows of test data into the FTS5 table
3. Define 50+ adversarial inputs including:
   - Empty string `""`
   - Whitespace only `"   "`, `"\t"`, `"\n"`
   - Single characters: `"*"`, `"("`, `")"`, `":"`, `"-"`, `"+"`, `'"'`
   - FTS5 operators: `"AND"`, `"OR"`, `"NOT"`, `"NEAR"`, `"NEAR/3"`
   - Operator combos: `"AND OR NOT"`, `"NOT AND"`, `"OR OR OR"`
   - Unbalanced quotes: `'"unbalanced'`, `'unbalanced"'`, `'""'`
   - Special combos: `"c++"`, `"C#"`, `"a:b"`, `"(test)"`, `"column:value"`
   - Wildcards: `"test*"`, `"*test"`, `"**"`, `"***"`
   - Unicode: `"🎉"`, `"café"`, `"naïve"`, `"日本語"`
   - Long strings: `"a" * 1000`
   - Dotted identifiers: `"asyncio.TaskGroup"`, `"os.path.join"`
   - Hyphenated: `"built-in"`, `"read-only"`
   - Mixed: `'asyncio AND "evil'`, `"(NOT) OR *"`, `"NEAR(a b)"`, `"a:b AND c*"`
   - Null-like: `"\x00"`, `"\r\n"`
4. For each input, call `fts5_escape()` and execute `SELECT * FROM test_fts WHERE test_fts MATCH ?` with the escaped result
5. Assert NO `sqlite3.OperationalError` is raised for any input

Also add basic unit tests for classify_query and expand_synonyms:
- `test_classify_query_dotted` — dotted names return "symbol"
- `test_classify_query_module` — known module names return "symbol" when symbol_exists_fn returns True
- `test_classify_query_non_module` — regular words return "fts"
- `test_expand_synonyms_match` — matching concept expands correctly
- `test_expand_synonyms_no_match` — non-matching query returns original tokens
- `test_build_match_expression_with_synonyms` — produces OR-joined escaped expression
- `test_build_match_expression_without_synonyms` — produces plain escaped expression
</action>

<acceptance_criteria>
- `tests/test_retrieval.py` exists
- Test file contains at least 50 distinct adversarial input strings in a list/tuple
- Each input is run through fts5_escape then MATCH on a real FTS5 table
- `uv run pytest tests/test_retrieval.py::test_fts5_escape_fuzz -x` exits 0
- `uv run pytest tests/test_retrieval.py -x -q` exits 0
- Test file contains `test_classify_query` and `test_expand_synonyms` tests
</acceptance_criteria>

<verification>
```bash
uv run pytest tests/test_retrieval.py -x -q 2>&1
```
</verification>
