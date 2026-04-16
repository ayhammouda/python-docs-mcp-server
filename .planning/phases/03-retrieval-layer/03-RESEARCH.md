# Phase 3: Retrieval Layer - Research

**Researched:** 2026-04-16
**Phase:** 03-retrieval-layer
**Requirements:** RETR-01 through RETR-09, SRVR-08

## RESEARCH COMPLETE

## 1. FTS5 Escape (RETR-01, RETR-02, RETR-03)

### Problem
FTS5 MATCH expressions accept a query grammar that includes operators (`AND`, `OR`, `NOT`, `NEAR`), prefix tokens (`*`), phrase quotes (`"`), column filters (`:`), grouping (`(`, `)`), and negate (`-`). User-supplied queries can contain any of these characters, causing `sqlite3.OperationalError` on malformed expressions.

### Implementation Approach
`fts5_escape(query: str) -> str` must:
1. **Quote every token individually** — wrap each whitespace-separated token in double quotes to prevent operator interpretation. Inside double quotes, the only special char is `"` itself.
2. **Escape embedded double quotes** — replace `"` with `""` (SQL-style escaping inside FTS5 quoted strings).
3. **Handle empty/whitespace-only input** — return `""` (matches nothing, does not crash).
4. **Handle single special characters** — `*`, `(`, `)`, `:`, `-` as standalone tokens must be quoted.
5. **Collapse FTS5 keywords** — tokens matching `AND`, `OR`, `NOT`, `NEAR` (case-insensitive) must be quoted to prevent operator interpretation.

The pattern:
```python
def fts5_escape(query: str) -> str:
    query = query.strip()
    if not query:
        return '""'
    tokens = query.split()
    escaped = []
    for token in tokens:
        # Escape internal double quotes
        safe = token.replace('"', '""')
        # Wrap every token in double quotes
        escaped.append(f'"{safe}"')
    return " ".join(escaped)
```

This produces queries like `"asyncio" "TaskGroup"` which FTS5 interprets as implicit AND of two quoted tokens. No operator, column filter, or prefix matching can leak through because every user token is unconditionally quoted.

### Verification
- 50+ fuzz inputs including: empty, `""`, `"`, `c++`, `AND OR NOT NEAR`, `(`, `)`, `:`, `*`, `-`, `asyncio.TaskGroup`, single char, emoji, combining characters, `NEAR/3`, `column:value`, `"unbalanced`, `OR AND NOT`, whitespace-only, tab characters, null bytes.
- Each input is escaped and passed to a real FTS5 MATCH on an in-memory table. Test asserts no `sqlite3.OperationalError`.

### RETR-02 Enforcement
A grep-verifiable rule: every `MATCH` query in the codebase MUST call `fts5_escape()`. The test suite includes a `rg` check asserting zero raw MATCH concatenations.

## 2. Query Classifier (RETR-04)

### Symbol Detection Heuristics
A query is "symbol-shaped" if:
1. **Contains a dot**: `asyncio.TaskGroup`, `os.path.join`, `json.dumps` — dotted qualified names
2. **Matches lowercase identifier pattern**: `^[a-z_][a-z0-9_]*$` — single-word module names like `re`, `os`, `sys`, `io`, BUT only if the term exists in the `symbols` table (to avoid false-positives on words like `test`, `list`)

### Implementation
```python
def classify_query(query: str, symbol_exists_fn: Callable[[str], bool]) -> Literal["symbol", "fts"]:
    query = query.strip()
    if "." in query:
        return "symbol"
    if re.match(r'^[a-z_][a-z0-9_]*$', query) and symbol_exists_fn(query):
        return "symbol"
    return "fts"
```

The `symbol_exists_fn` is a callback that checks the `symbols` table: `SELECT 1 FROM symbols WHERE qualified_name = ? LIMIT 1`. This avoids importing storage into retrieval — the callback is injected by the service layer.

### Fast-Path Behavior
When classified as "symbol", the search skips FTS5 entirely and queries the `symbols` table directly (exact match first, then LIKE prefix match). Results are shaped into the same `SymbolHit` schema (RETR-09) regardless of path.

## 3. Synonym Expansion (RETR-05)

### Design from Build Guide §6
The `synonyms` table stores concept-to-expansion mappings:
```
concept: "parallel"
expansion: "concurrent multiprocessing threading asyncio concurrent.futures"
```

### Query Expansion Strategy
Before building the FTS5 MATCH expression:
1. Check if the query (or any token in the query) matches a `concept` in the synonyms table.
2. If matched, expand the query by OR-ing the original tokens with the expansion terms.
3. Build the final FTS5 MATCH with OR operators between expanded terms.

```python
def expand_synonyms(query: str, synonyms: dict[str, list[str]]) -> str:
    tokens = query.lower().split()
    expanded_tokens = set(tokens)
    for token in tokens:
        if token in synonyms:
            expanded_tokens.update(synonyms[token])
    # Also check multi-word concepts
    query_lower = query.lower()
    for concept, expansions in synonyms.items():
        if concept in query_lower:
            expanded_tokens.update(expansions)
    return expanded_tokens
```

The expanded set is then escaped via `fts5_escape()` and joined with `OR`:
```python
escaped_terms = [fts5_escape(term) for term in expanded_tokens]
match_expr = " OR ".join(escaped_terms)
```

### Synonym Loading
Synonyms are loaded once at startup into `AppContext.synonyms` (already implemented in Phase 1's `_load_synonyms()`). The retrieval layer receives them as a parameter, not by importing the server module.

## 4. BM25 Ranking with Column Weights (RETR-06, RETR-07)

### FTS5 BM25 API
SQLite FTS5 provides `bm25(table_name, w1, w2, ...)` as a ranking function. Column weights are passed as arguments — higher (less negative) values mean higher importance.

For `sections_fts(heading, content_text)`:
- `bm25(sections_fts, 10.0, 1.0)` — heading matches weighted 10x over content_text

For `symbols_fts(qualified_name, module)`:
- `bm25(symbols_fts, 10.0, 1.0)` — qualified_name matches weighted 10x over module

### FTS5 snippet() API
`snippet(table_name, column_index, before_match, after_match, trailing, max_tokens)`

For ~200 char excerpts:
```sql
snippet(sections_fts, 1, '**', '**', '...', 32)
```
- Column 1 = content_text
- `**` markers for match highlighting
- `...` as trailing ellipsis
- 32 tokens ≈ 200 chars

### Ranking Query Pattern
```sql
SELECT s.id, s.heading, s.content_text, s.uri, s.anchor,
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
```

BM25 returns negative scores (lower = better match), so `ORDER BY bm25(...)` naturally sorts best-first.

### Score Normalization
For the `SymbolHit.score` field, normalize BM25 scores to [0, 1] range:
- Symbol fast-path exact match: 1.0
- Symbol fast-path prefix match: 0.8
- FTS5 results: normalize `abs(bm25_score)` relative to the batch (best = 1.0, worst in batch = 0.1)

## 5. Budget Enforcement (RETR-08)

### Unicode-Safe Truncation
`apply_budget(text: str, max_chars: int, start_index: int) -> tuple[str, bool, int | None]`

Returns: `(truncated_text, is_truncated, next_start_index)`

Key constraints:
1. **Never split a Unicode codepoint** — Python strings are sequences of codepoints, so slicing by index is already codepoint-safe in Python 3. `text[start_index:start_index + max_chars]` is safe for BMP characters.
2. **Never split a combining character sequence** — A base char + combining mark (e.g., `é` as `e` + `\u0301`) should not be split. Use `unicodedata.category()` to detect combining marks (`Mn`, `Mc`, `Me`) and adjust the boundary.
3. **4-byte emoji handling** — Emoji like 🎉 (U+1F389) are single codepoints in Python 3. Surrogate pairs only exist in UTF-16; Python 3 `str` uses codepoints. No special handling needed beyond combining marks.
4. **Return `next_start_index`** — If truncated, return `start_index + len(result)` for pagination.
5. **Signal truncation** — Return `truncated=True` if the text was cut.

```python
import unicodedata

def apply_budget(text: str, max_chars: int, start_index: int = 0) -> tuple[str, bool, int | None]:
    if start_index >= len(text):
        return ("", False, None)
    
    remaining = text[start_index:]
    if len(remaining) <= max_chars:
        return (remaining, False, None)
    
    # Truncate at max_chars
    end = start_index + max_chars
    # Back up past any combining marks
    while end > start_index and unicodedata.category(text[end - 1]).startswith('M'):
        end -= 1
    
    result = text[start_index:end]
    truncated = end < len(text)
    next_idx = end if truncated else None
    return (result, truncated, next_idx)
```

## 6. Domain Error Surfacing (SRVR-08)

### Error Taxonomy (from build guide §10, already in errors.py)
- `VersionNotFoundError` — requested version not in index
- `SymbolNotFoundError` — no symbol match found
- `PageNotFoundError` — requested slug/page not in index

### MCP isError Routing
These are NOT protocol errors (which would be JSON-RPC errors). They are domain errors that should be returned as tool results with `isError: true`. In FastMCP, this is done by raising `ToolError` (from `mcp.server.fastmcp`), which FastMCP translates to `isError: true` in the tool result.

Alternative: catch `DocsServerError` subtypes in the tool handler and return a result dict with `isError=True` content text. Per FastMCP's API, the recommended pattern is:
```python
from mcp.server.fastmcp import ToolError

@mcp.tool()
def search_docs(...):
    try:
        # ... retrieval logic
    except VersionNotFoundError as e:
        raise ToolError(str(e))
```

However, the build guide specifies `isError: true` with an informative content message. FastMCP's `ToolError` maps to this correctly. The retrieval layer raises the named exceptions; the server/tool layer catches them and raises `ToolError`.

### Separation of Concerns
- **Retrieval layer** (`retrieval/*.py`): raises `VersionNotFoundError`, `SymbolNotFoundError`, `PageNotFoundError`
- **Server layer** (`server.py`): catches these in tool handlers and converts to `ToolError` for MCP isError response

## 7. Module Structure

### New Files
```
src/mcp_server_python_docs/retrieval/
    __init__.py         — package init, public API re-exports
    query.py            — fts5_escape(), classify_query(), expand_synonyms()
    ranker.py           — search_sections(), search_symbols(), search_examples()
    budget.py           — apply_budget()
```

### Dependency Direction
```
server.py → retrieval/ → (pure logic, no imports from server or storage)
                       → storage/ queries passed as callbacks or raw SQL strings
```

The retrieval module does NOT import from `storage` or `server`. It receives database connections and synonym dicts as parameters. This keeps it testable without database setup.

However, the ranker needs to execute SQL queries (FTS5 MATCH, BM25, snippet). Two approaches:
1. **Pass `sqlite3.Connection` to ranker functions** — simplest, the ranker constructs and executes queries
2. **Repository pattern** — storage layer exposes query methods, retrieval calls them

Given the build guide says "Pure-logic module. No MCP types, no SQL", option 2 is more aligned. But constructing FTS5 MATCH expressions IS the retrieval layer's job. Resolution: the retrieval layer builds the MATCH expression (via fts5_escape + synonym expansion), and the ranker executes queries using a connection passed in. The "no SQL" constraint means no schema DDL or storage management — query construction for retrieval is the retrieval layer's core purpose.

## 8. Existing Code Patterns to Follow

From Phase 1/2 codebase analysis:
- **Logging**: `logger = logging.getLogger(__name__)` in each module
- **Type annotations**: `from __future__ import annotations` at top
- **Error handling**: Named exceptions in `errors.py`, caught in server layer
- **Models**: Pydantic `BaseModel` in `models.py` with `Field` descriptions
- **Testing**: pytest with fixtures, in-memory SQLite for storage tests
- **Imports**: Relative imports within the package (`from mcp_server_python_docs.xxx import yyy`)

## 9. Hit Shape Consistency (RETR-09)

Both symbol fast-path and FTS5 results must produce `SymbolHit` with identical schema:
```python
SymbolHit(
    uri=...,       # Full URI path
    title=...,     # Display title (qualified_name for symbols, heading for sections)
    kind=...,      # Hit type (class, function, section, example, etc.)
    snippet=...,   # FTS5 snippet or empty for symbol fast-path
    score=...,     # Normalized relevance score [0, 1]
    version=...,   # Python version string
    slug=...,      # Page slug for get_docs follow-up
    anchor=...,    # Section anchor for get_docs follow-up
)
```

The existing `SymbolHit` model in `models.py` already matches this shape. No model changes needed.

## Validation Architecture

### Dimension 1: Correctness
- 50+ fuzz inputs to fts5_escape, each executed against real FTS5 MATCH
- Symbol classifier tests: dotted names, single-word modules, non-module words
- Synonym expansion tests with known mappings
- Budget truncation with emoji, combining characters, empty strings, exact boundary

### Dimension 2: Integration
- End-to-end search through classifier -> synonym expansion -> FTS5 query -> ranking -> hit shape
- Symbol fast-path produces same SymbolHit shape as FTS5 path
- Error routing: VersionNotFoundError -> isError: true in tool response

### Dimension 3: Edge Cases
- Empty query, whitespace-only query, single character
- Query with all FTS5 operators: `AND OR NOT NEAR * " ( ) : -`
- Unicode: emoji, CJK characters, combining marks, RTL text
- apply_budget at exact boundary, at combining mark boundary, start_index beyond text length

### Dimension 4: Performance
- BM25 column weights produce correct ordering (heading > content_text)
- Symbol fast-path avoids FTS5 entirely (measurable via query classification)

## BLOCKERS

None. All dependencies (schema, models, errors, storage) are in place from Phases 1-2.
