# Phase 2: Schema & Storage - Research

**Researched:** 2026-04-16
**Status:** RESEARCH COMPLETE

## 1. Current State Analysis

### Phase 1 Artifacts Already in Place

Phase 1 established these storage artifacts that Phase 2 must extend (not replace):

- **`storage/db.py`** — Connection factory with `get_readonly_connection()`, `get_readwrite_connection()`, `assert_fts5_available()`, `get_cache_dir()`, `get_index_path()`, and `bootstrap_schema()`. The `bootstrap_schema()` function currently creates only `doc_sets` + `symbols` + `symbols_fts` (Phase 1 minimal schema).
- **`ingestion/inventory.py`** — Uses `bootstrap_schema(conn)` before ingestion. Inserts into `doc_sets` and `symbols`. Rebuilds `symbols_fts` via the `INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')` pattern for external-content FTS5.
- **`errors.py`** — `FTS5UnavailableError` already defined.
- **`app_context.py`** — `AppContext` dataclass with `db: sqlite3.Connection`, `index_path: Path`, `synonyms: dict[str, list[str]]`.

### Critical Changes Required

1. **`symbols` UNIQUE constraint change**: Phase 1 has `UNIQUE(doc_set_id, qualified_name)`. Phase 2 changes this to `UNIQUE(doc_set_id, qualified_name, symbol_type)` per STOR-03. This is a DDL-level change — the `CREATE TABLE IF NOT EXISTS` won't alter existing tables.

2. **`symbols_fts` tokenizer fix**: Phase 1 has `tokenize='unicode61'` on `symbols_fts`. Phase 2 must change ALL FTS5 tables to `tokenize="unicode61 remove_diacritics 2 tokenchars '._'"` per STOR-02. The build guide originally had `unicode61 porter` on `sections_fts` — the REQUIREMENTS.md and ROADMAP explicitly override this: NO Porter stemming on any FTS5 table.

3. **New tables**: `documents`, `sections`, `examples`, `synonyms`, `redirects`, `ingestion_runs`, plus `sections_fts` and `examples_fts` virtual tables.

4. **`sections.uri` uniqueness change**: Build guide has `UNIQUE(uri)` on sections. Phase 2 drops this per STOR-04, keeping only `UNIQUE(document_id, anchor)` for cross-version URI safety.

## 2. FTS5 Tokenizer Configuration

### Corrected Tokenizer String

All three FTS5 virtual tables use identical tokenizer:
```
tokenize="unicode61 remove_diacritics 2 tokenchars '._'"
```

**Key decisions:**
- **No Porter stemming** — The build guide §7 had `tokenize='unicode61 porter'` on `sections_fts`. The REQUIREMENTS.md STOR-02 explicitly overrides this: "Porter stemming is NOT applied (preserves Python identifier search)." This was identified as research blocker B1.
- **`remove_diacritics 2`** — The unicode61 tokenizer's default is `remove_diacritics 1` (remove). Setting `2` maps diacritical characters to ASCII equivalents while keeping the originals indexed too. This is the correct choice for searching technical documentation with occasional non-ASCII identifiers.
- **`tokenchars '._'`** — By default, `.` and `_` are separator characters in unicode61. Adding them to `tokenchars` means `asyncio.TaskGroup` is indexed as a single token `asyncio.TaskGroup`, not three separate tokens `asyncio`, `Task`, `Group`. This is essential for exact Python identifier search.

### SQLite FTS5 Tokenizer Syntax Verification

The correct SQLite FTS5 tokenizer declaration uses double quotes around the tokenizer string:
```sql
CREATE VIRTUAL TABLE t USING fts5(
    col1, col2,
    tokenize="unicode61 remove_diacritics 2 tokenchars '._'"
);
```

The single quotes around `'._'` are nested inside the double-quoted tokenizer string — this is standard SQLite FTS5 syntax.

### Impact on Existing symbols_fts

Phase 1's `symbols_fts` uses `tokenize='unicode61'`. Changing the tokenizer requires dropping and recreating the virtual table — FTS5 tokenizer cannot be altered after creation. The schema bootstrap must handle this transition by using `DROP TABLE IF EXISTS` before `CREATE VIRTUAL TABLE`.

## 3. Schema Design Decisions

### documents.uri UNIQUE Constraint

The build guide has `uri TEXT NOT NULL UNIQUE` on `documents`. This is correct for Phase 2 — document URIs are unique within a doc_set (enforced by `UNIQUE(doc_set_id, slug)`). However, `UNIQUE(uri)` on `documents` creates a cross-version collision if two versions have the same URI string (e.g., `library/json.html`). 

**Decision:** Keep the build guide's `UNIQUE(doc_set_id, slug)` but remove the standalone `UNIQUE(uri)` on `documents` for the same cross-version safety reason as sections.

### sections.uri Change (STOR-04)

The build guide has `uri TEXT NOT NULL UNIQUE` on sections. Phase 2 drops `UNIQUE(uri)` per STOR-04, keeping only `UNIQUE(document_id, anchor)`. This allows the same URI to appear for different versions (e.g., `library/json.html#json.dumps` exists in both 3.12 and 3.13).

### symbols UNIQUE Constraint Change (STOR-03)

Phase 1: `UNIQUE(doc_set_id, qualified_name)` — deduplicates by name only.
Phase 2: `UNIQUE(doc_set_id, qualified_name, symbol_type)` — allows same name with different types.

This means `json.dumps` can exist as both `function` and `method` in the same doc_set. The ingestion code in `inventory.py` currently groups by qualified_name and picks the highest-priority type. Phase 2 changes the UNIQUE constraint so the DB allows both; the ingestion dedup logic may need updating in a later phase or may remain as-is (ingestion still picks the best match, but the DB no longer enforces uniqueness on name alone).

### doc_sets.language Default (STOR-05)

Already present in Phase 1's schema: `language TEXT NOT NULL DEFAULT 'en'`. No change needed — just verify it's preserved in the full schema.

## 4. Schema Bootstrap Idempotency (STOR-09)

### Strategy: schema.sql as External File + IF NOT EXISTS

The schema should live in a separate `src/mcp_server_python_docs/storage/schema.sql` file loaded via `importlib.resources`, not inline Python strings. This makes the schema:
- Readable and diffable
- Usable by external SQL tools
- Testable independently

**Idempotency approach:**
- All `CREATE TABLE` statements use `IF NOT EXISTS`
- All `CREATE VIRTUAL TABLE` statements use `IF NOT EXISTS`
- PRAGMAs are set at connection open time (already done in `db.py`)
- Running `bootstrap_schema()` twice is a no-op

### FTS5 Virtual Table Recreation

The FTS5 tokenizer change from Phase 1 (`unicode61`) to Phase 2 (`unicode61 remove_diacritics 2 tokenchars '._'`) requires special handling:
- `CREATE VIRTUAL TABLE IF NOT EXISTS` will NOT recreate a table with a different tokenizer — it will simply skip creation if the table exists
- Solution: The Phase 2 `bootstrap_schema()` must `DROP TABLE IF EXISTS` all FTS5 virtual tables before recreating them with the corrected tokenizer
- This is acceptable because FTS5 external-content tables are derived data — they can be rebuilt from the canonical tables at any time via the `INSERT INTO fts(fts) VALUES('rebuild')` command

### Schema Version Tracking

For v0.1.0, a simple `user_version` PRAGMA is sufficient:
```sql
PRAGMA user_version = 2;  -- Phase 2 schema version
```

This allows future phases to detect schema version and migrate if needed.

## 5. File Organization

### schema.sql Location

Per the build guide §13 package structure:
```
src/mcp_server_python_docs/
    storage/
        __init__.py
        db.py
        schema.sql    <-- NEW: full DDL
```

The `schema.sql` file should be loadable via `importlib.resources`:
```python
ref = importlib.resources.files("mcp_server_python_docs.storage") / "schema.sql"
```

### bootstrap_schema() Refactoring

The current `bootstrap_schema()` in `db.py` has inline SQL for Phase 1's minimal schema. Phase 2 replaces this with:
1. Load `schema.sql` via `importlib.resources`
2. Execute the full DDL via `conn.executescript()`
3. The function signature stays the same: `bootstrap_schema(conn: sqlite3.Connection) -> None`

## 6. Testing Strategy

### FTS5 Tokenizer Regression Fixture (Success Criterion 1)

Test creates an in-memory database, bootstraps schema, inserts test data into `sections`, `symbols`, and `examples`, rebuilds FTS indexes, then queries for:
- `asyncio.TaskGroup` — exact match as single token
- `json.dumps` — exact match with `.` as token character
- `collections.OrderedDict` — exact match with compound name

Each query must return results via `MATCH` on the FTS table. If Porter stemming were applied, `dumps` might be stemmed to `dump`, which would break exact identifier search.

### Composite Symbol Uniqueness (Success Criterion 2)

Test inserts `json.dumps` as both `function` and `method` into `symbols` with the same `doc_set_id` and `qualified_name` but different `symbol_type`. Both inserts must succeed. A third insert with the same `(doc_set_id, qualified_name, symbol_type)` triple must fail with `IntegrityError`.

### Cross-Version URI Collision (Success Criterion 3)

Test creates two doc_sets (3.12 and 3.13), two documents with the same slug but different doc_set_ids, then inserts sections with the same URI string (e.g., `library/json.html#json.dumps`) for both versions. Both inserts must succeed because `UNIQUE(uri)` is gone — only `UNIQUE(document_id, anchor)` applies.

### Idempotent Bootstrap (Success Criterion 4)

Test calls `bootstrap_schema(conn)` twice on the same connection. The second call must not raise any errors. After both calls, `doc_sets.language` must default to `'en'` (verified by inserting a row without specifying language and reading it back).

### platformdirs Verification (Success Criterion 5)

Not a unit test — this is a grep audit: `rg '~/.cache'` in the source tree must return zero hits. All cache paths must flow through `platformdirs.user_cache_dir()`.

## 7. Impact on Existing Code

### inventory.py Compatibility

The `ingest_inventory()` function calls `bootstrap_schema(conn)`. When Phase 2 replaces the inline schema with the full `schema.sql`, this call will create ALL tables (not just doc_sets + symbols). This is fine — the function only INSERT/SELECT from `doc_sets` and `symbols`, so extra tables are harmless.

The UNIQUE constraint change on `symbols` (`qualified_name` -> `qualified_name, symbol_type`) means the `INSERT OR REPLACE` in `ingest_inventory()` needs updating. Currently it replaces by `(doc_set_id, qualified_name)`. After Phase 2, it should replace by `(doc_set_id, qualified_name, symbol_type)`. However, since Phase 1's dedup logic already picks one type per qualified_name, the practical impact is nil for v0.1.0 — the old INSERT OR REPLACE just won't fire the REPLACE because there's no conflict on the new three-column unique index when only one type per name is inserted. The ingestion code can remain as-is.

### server.py Compatibility

No changes needed. The server opens a read-only connection and queries `symbols`. New tables are present but unused until later phases.

## 8. External-Content FTS5 Rebuild Pattern

For external-content FTS5 tables, the content is NOT stored in the FTS table itself — it references the canonical table. The rebuild command re-reads all content:

```sql
-- After modifying canonical table data:
INSERT INTO sections_fts(sections_fts) VALUES('rebuild');
INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild');
INSERT INTO examples_fts(examples_fts) VALUES('rebuild');
```

This is already used in `inventory.py` for `symbols_fts`. Phase 2 adds the pattern for `sections_fts` and `examples_fts`, though they won't have data until Phase 4.

## Validation Architecture

### Dimension 1: Functional Completeness
- All 8 tables from build guide §7 exist
- All 3 FTS5 virtual tables with corrected tokenizer exist
- Uniqueness constraints match STOR-03 and STOR-04 specifications

### Dimension 2: Structural Integrity
- Schema bootstrap is idempotent (run twice = no error)
- FTS5 external-content tables reference correct canonical tables
- Foreign key constraints are enforced (PRAGMA foreign_keys = ON)

### Dimension 3: Behavioral Correctness
- FTS5 tokenizer indexes Python identifiers as single tokens
- Porter stemming is NOT applied (exact token search works)
- Cross-version URI insertion succeeds

### Dimension 4: Edge Cases
- Empty database bootstrap works
- Re-bootstrap after data insertion works without data loss (IF NOT EXISTS)
- FTS5 rebuild on empty tables works without error

---

## RESEARCH COMPLETE
