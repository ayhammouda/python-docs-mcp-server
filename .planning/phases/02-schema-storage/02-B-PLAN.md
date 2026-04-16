---
phase: 2
plan_id: 02-B
title: "Refactor bootstrap_schema() to load schema.sql and handle FTS5 recreation"
wave: 2
depends_on:
  - 02-A
files_modified:
  - src/mcp_server_python_docs/storage/db.py
requirements:
  - STOR-01
  - STOR-09
autonomous: true
---

<objective>
Refactor `bootstrap_schema()` in `storage/db.py` to load the complete schema DDL from `schema.sql` via `importlib.resources` instead of inline SQL, handle the FTS5 virtual table recreation needed for the tokenizer change, and ensure idempotent execution (running twice on the same file is a no-op).
</objective>

<tasks>

<task id="1">
<title>Replace inline schema SQL with schema.sql file loading</title>
<read_first>
- src/mcp_server_python_docs/storage/db.py (current bootstrap_schema function at line 92)
- src/mcp_server_python_docs/storage/schema.sql (created by plan 02-A)
- src/mcp_server_python_docs/ingestion/inventory.py (calls bootstrap_schema at line 86)
</read_first>
<action>
Replace the `bootstrap_schema()` function in `src/mcp_server_python_docs/storage/db.py` with a new implementation that:

1. Adds `import importlib.resources` to the imports at the top of the file.

2. Replaces the function body:

```python
def bootstrap_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and FTS5 indexes from schema.sql (STOR-01, STOR-09).

    Loads the complete DDL from storage/schema.sql via importlib.resources.
    All CREATE statements use IF NOT EXISTS for idempotency — running this
    twice on the same database is a no-op.

    FTS5 virtual tables are dropped and recreated on every bootstrap to ensure
    the tokenizer configuration matches the current schema.sql. This is safe
    because FTS5 external-content tables are derived data that can be rebuilt
    from canonical tables via the 'rebuild' command.
    """
    # Drop FTS5 virtual tables first so they can be recreated with the
    # correct tokenizer. IF NOT EXISTS would skip recreation if the table
    # exists with a different tokenizer — there is no ALTER for FTS5.
    for fts_table in ("sections_fts", "symbols_fts", "examples_fts"):
        conn.execute(f"DROP TABLE IF EXISTS {fts_table}")

    # Load and execute the full schema DDL
    ref = importlib.resources.files("mcp_server_python_docs.storage") / "schema.sql"
    with importlib.resources.as_file(ref) as schema_path:
        schema_sql = schema_path.read_text()
    conn.executescript(schema_sql)
```

3. Remove the old inline SQL from the function body entirely — no fallback path.

4. Ensure `importlib.resources` is imported at the top of the file (add to the existing imports section, alongside `import logging`, `import platform`, `import sqlite3`).

**Critical detail:** The FTS5 DROP+CREATE pattern is the ONLY way to change a tokenizer on an existing database. `CREATE VIRTUAL TABLE IF NOT EXISTS` silently keeps the old tokenizer. This is why the function drops FTS5 tables unconditionally before running schema.sql.

**Backward compatibility:** `ingest_inventory()` calls `bootstrap_schema(conn)` as its first step. After this change, it will create all 8 tables + 3 FTS5 tables instead of just 2 tables + 1 FTS5 table. This is harmless — ingestion only writes to `doc_sets` and `symbols`.
</action>
<acceptance_criteria>
- `storage/db.py` contains `import importlib.resources`
- `bootstrap_schema()` docstring mentions `schema.sql`, `STOR-01`, and `STOR-09`
- `bootstrap_schema()` calls `conn.execute("DROP TABLE IF EXISTS sections_fts")`
- `bootstrap_schema()` calls `conn.execute("DROP TABLE IF EXISTS symbols_fts")`
- `bootstrap_schema()` calls `conn.execute("DROP TABLE IF EXISTS examples_fts")`
- `bootstrap_schema()` loads `schema.sql` via `importlib.resources.files("mcp_server_python_docs.storage")`
- `bootstrap_schema()` calls `conn.executescript(schema_sql)` with the loaded SQL
- No inline CREATE TABLE or CREATE VIRTUAL TABLE SQL remains in `bootstrap_schema()`
- The old Phase 1 inline SQL (doc_sets + symbols + symbols_fts) is completely removed from the function
- `pyproject.toml` or `hatch` config includes `storage/schema.sql` in wheel (verify `.sql` files are included by hatchling — they should be by default since hatchling includes all files under packages)
</acceptance_criteria>
</task>

</tasks>

<verification>
- [ ] bootstrap_schema() loads schema.sql via importlib.resources
- [ ] FTS5 tables are dropped before recreation (tokenizer change safety)
- [ ] No inline CREATE TABLE SQL in db.py
- [ ] Function is idempotent — calling twice produces no errors
- [ ] Existing ingest_inventory() call still works (no signature change)
</verification>

<must_haves>
- schema.sql loaded via importlib.resources (not inline SQL)
- FTS5 DROP+CREATE for tokenizer migration safety
- Idempotent execution (STOR-09)
- No breaking change to bootstrap_schema() signature
</must_haves>
