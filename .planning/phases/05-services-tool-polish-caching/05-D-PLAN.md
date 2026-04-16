---
phase: 5
plan_id: 05-D
title: "LRU caching on hot reads (OPS-04, OPS-05)"
wave: 2
depends_on:
  - 05-A
  - 05-B
files_modified:
  - src/mcp_server_python_docs/services/cache.py
  - src/mcp_server_python_docs/services/content.py
  - src/mcp_server_python_docs/services/search.py
requirements:
  - OPS-04
  - OPS-05
autonomous: true
---

<objective>
Add LRU caching on hot reads per build guide section 12. Implement `get_section_cached(section_id)` with maxsize=512 and `resolve_symbol_cached(qualified_name, version)` with maxsize=128. Caches are process-lifetime-scoped with no TTL and no invalidation (user restart on rebuild is documented in PUBL-05).
</objective>

<tasks>

<task id="1">
<title>Create cache module with LRU-wrapped functions</title>
<read_first>
- python-docs-mcp-server-build-guide.md §12 (caching pattern)
- src/mcp_server_python_docs/storage/db.py (connection patterns)
- src/mcp_server_python_docs/retrieval/ranker.py (lookup_symbols_exact — pattern for symbol queries)
</read_first>
<action>
Create `src/mcp_server_python_docs/services/cache.py`:

```python
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
from functools import lru_cache
from typing import NamedTuple


class CachedSection(NamedTuple):
    """Cached section data — lightweight container for LRU cache."""
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


def create_section_cache(db: sqlite3.Connection) -> callable:
    """Create an LRU-cached section lookup function (OPS-04).

    Args:
        db: Read-only SQLite connection (captured by closure).

    Returns:
        A function `get_section(section_id: int) -> CachedSection | None`
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


def create_symbol_cache(db: sqlite3.Connection) -> callable:
    """Create an LRU-cached symbol resolution function (OPS-04).

    Args:
        db: Read-only SQLite connection (captured by closure).

    Returns:
        A function `resolve_symbol(qualified_name: str, version: str) -> CachedSymbol | None`
        with maxsize=128.
    """
    @lru_cache(maxsize=128)
    def resolve_symbol_cached(qualified_name: str, version: str) -> CachedSymbol | None:
        row = db.execute(
            """
            SELECT s.qualified_name, s.symbol_type, s.uri, s.anchor, s.module, d.version
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
```

Key design decisions:
- Closure pattern captures `db` (not hashable) so only hashable args reach `lru_cache`
- `NamedTuple` return types are lightweight and hashable (can be cached themselves)
- maxsize=512 for sections, maxsize=128 for symbols (per build guide)
- No TTL, no invalidation (process lifetime = session lifetime for stdio)
</action>
<acceptance_criteria>
- File exists at `src/mcp_server_python_docs/services/cache.py`
- `create_section_cache(db)` returns a function decorated with `@lru_cache(maxsize=512)`
- `create_symbol_cache(db)` returns a function decorated with `@lru_cache(maxsize=128)`
- `CachedSection` and `CachedSymbol` are `NamedTuple` types
- `python -c "from mcp_server_python_docs.services.cache import create_section_cache, create_symbol_cache"` succeeds
</acceptance_criteria>
</task>

<task id="2">
<title>Wire section cache into ContentService</title>
<read_first>
- src/mcp_server_python_docs/services/content.py (get_docs method — section retrieval path)
- src/mcp_server_python_docs/services/cache.py (create_section_cache)
</read_first>
<action>
Update `src/mcp_server_python_docs/services/content.py`:

1. Add import:
```python
from mcp_server_python_docs.services.cache import create_section_cache
```

2. In `ContentService.__init__`, create the section cache:
```python
def __init__(self, db: sqlite3.Connection) -> None:
    self._db = db
    self._get_section = create_section_cache(db)
```

3. In the section-level retrieval path of `get_docs()`, use the cache when looking up a single section by ID. First find the section ID via the existing query, then use the cache for the content:
```python
# For section retrieval by anchor, look up via the DB to get section_id,
# then use the cache for content
section_row = self._db.execute(
    "SELECT id FROM sections WHERE document_id = ? AND anchor = ? LIMIT 1",
    (doc_id, anchor),
).fetchone()
if section_row is not None:
    cached = self._get_section(section_row["id"])
    if cached is not None:
        full_text = cached.content_text
        title = cached.heading or doc_title
```

This ensures repeat requests for the same section hit the LRU cache.
</action>
<acceptance_criteria>
- `ContentService.__init__` creates a section cache via `create_section_cache(db)`
- Section retrieval path in `get_docs()` uses the cached function
- Repeat calls for the same section_id hit the cache (verifiable via `cache_info().hits`)
</acceptance_criteria>
</task>

<task id="3">
<title>Wire symbol cache into SearchService</title>
<read_first>
- src/mcp_server_python_docs/services/search.py (search method — symbol fast-path)
- src/mcp_server_python_docs/services/cache.py (create_symbol_cache)
</read_first>
<action>
Update `src/mcp_server_python_docs/services/search.py`:

1. Add import:
```python
from mcp_server_python_docs.services.cache import create_symbol_cache
```

2. In `SearchService.__init__`, create the symbol cache:
```python
def __init__(self, db: sqlite3.Connection, synonyms: dict[str, list[str]]) -> None:
    self._db = db
    self._synonyms = synonyms
    self._resolve_symbol = create_symbol_cache(db)
```

3. Use the cache in `_symbol_exists` to check if a symbol is in the cache before hitting the DB:
```python
def _symbol_exists(self, name: str) -> bool:
    # Try cache first (common symbols will be cached)
    cached = self._resolve_symbol(name, "")
    if cached is not None:
        return True
    # Fall back to DB check
    row = self._db.execute(
        "SELECT 1 FROM symbols WHERE qualified_name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None
```

Note: The cache is primarily for `resolve_symbol_cached(name, version)` lookups in future iterations. For v0.1.0, the main benefit is caching symbol existence checks. The cache is process-lifetime-scoped (OPS-05).
</action>
<acceptance_criteria>
- `SearchService.__init__` creates a symbol cache via `create_symbol_cache(db)`
- Symbol resolution uses the cached function where possible
- Repeat lookups for the same (qualified_name, version) hit the cache
</acceptance_criteria>
</task>

</tasks>

<verification>
1. `uv run python -c "from mcp_server_python_docs.services.cache import create_section_cache, create_symbol_cache; import sqlite3; db = sqlite3.connect(':memory:'); sc = create_section_cache(db); print(sc.cache_info())"` shows maxsize=512
2. `uv run python -c "from mcp_server_python_docs.services.cache import create_section_cache, create_symbol_cache; import sqlite3; db = sqlite3.connect(':memory:'); symc = create_symbol_cache(db); print(symc.cache_info())"` shows maxsize=128
3. Repeat calls show `cache_info().hits` incrementing
4. No TTL or invalidation logic exists
5. `uv run pytest tests/ -x -q` passes
</verification>

<must_haves>
- get_section_cached with maxsize=512 (OPS-04)
- resolve_symbol_cached with maxsize=128 (OPS-04)
- Process-lifetime scope, no TTL, no invalidation (OPS-05)
- Cache hit on repeat calls verifiable via cache_info()
</must_haves>
