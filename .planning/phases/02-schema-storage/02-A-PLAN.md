---
phase: 2
plan_id: 02-A
title: "Full schema.sql with corrected FTS5 tokenizer and uniqueness constraints"
wave: 1
depends_on: []
files_modified:
  - src/mcp_server_python_docs/storage/schema.sql
requirements:
  - STOR-01
  - STOR-02
  - STOR-03
  - STOR-04
  - STOR-05
autonomous: true
---

<objective>
Create the complete `schema.sql` file at `src/mcp_server_python_docs/storage/schema.sql` containing all 8 canonical tables and 3 FTS5 virtual tables defined in build guide §7, with the corrected tokenizer (`unicode61 remove_diacritics 2 tokenchars '._'` — NO Porter stemming), the composite symbol uniqueness constraint, cross-version URI safety, and the `doc_sets.language` column.
</objective>

<tasks>

<task id="1">
<title>Create schema.sql with all canonical tables</title>
<read_first>
- python-docs-mcp-server-build-guide.md (section 7 — schema DDL starting at line 171)
- src/mcp_server_python_docs/storage/db.py (current bootstrap_schema inline SQL)
- .planning/REQUIREMENTS.md (STOR-01 through STOR-05)
</read_first>
<action>
Create file `src/mcp_server_python_docs/storage/schema.sql` with the following complete DDL.

**All 8 canonical tables** (per build guide §7 STOR-01):

1. `doc_sets` — exactly as build guide §7:
```sql
CREATE TABLE IF NOT EXISTS doc_sets (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL DEFAULT 'python-docs',
    version     TEXT NOT NULL,
    language    TEXT NOT NULL DEFAULT 'en',
    label       TEXT NOT NULL,
    is_default  INTEGER NOT NULL DEFAULT 0,
    base_url    TEXT,
    built_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, version, language)
);
```
Note: `language` column with DEFAULT 'en' satisfies STOR-05.

2. `documents` — from build guide §7, but with `UNIQUE(uri)` REMOVED for cross-version safety (same reasoning as STOR-04 for sections). Keep `UNIQUE(doc_set_id, slug)`:
```sql
CREATE TABLE IF NOT EXISTS documents (
    id               INTEGER PRIMARY KEY,
    doc_set_id       INTEGER NOT NULL REFERENCES doc_sets(id) ON DELETE CASCADE,
    uri              TEXT NOT NULL,
    slug             TEXT NOT NULL,
    title            TEXT NOT NULL,
    content_text     TEXT NOT NULL,
    char_count       INTEGER NOT NULL,
    UNIQUE(doc_set_id, slug)
);
```

3. `sections` — from build guide §7, with `UNIQUE(uri)` DROPPED per STOR-04. Only `UNIQUE(document_id, anchor)` remains:
```sql
CREATE TABLE IF NOT EXISTS sections (
    id               INTEGER PRIMARY KEY,
    document_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    uri              TEXT NOT NULL,
    anchor           TEXT NOT NULL,
    heading          TEXT NOT NULL,
    level            INTEGER NOT NULL,
    ordinal          INTEGER NOT NULL,
    content_text     TEXT NOT NULL,
    char_count       INTEGER NOT NULL,
    UNIQUE(document_id, anchor)
);
```

4. `symbols` — from build guide §7, with UNIQUE changed from `UNIQUE(doc_set_id, qualified_name)` to `UNIQUE(doc_set_id, qualified_name, symbol_type)` per STOR-03. Also add `document_id` and `section_id` FK columns per build guide:
```sql
CREATE TABLE IF NOT EXISTS symbols (
    id               INTEGER PRIMARY KEY,
    doc_set_id       INTEGER NOT NULL REFERENCES doc_sets(id) ON DELETE CASCADE,
    qualified_name   TEXT NOT NULL,
    normalized_name  TEXT NOT NULL,
    module           TEXT,
    symbol_type      TEXT,
    document_id      INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    section_id       INTEGER REFERENCES sections(id) ON DELETE SET NULL,
    uri              TEXT NOT NULL,
    anchor           TEXT,
    UNIQUE(doc_set_id, qualified_name, symbol_type)
);
```

5. `examples` — exactly as build guide §7:
```sql
CREATE TABLE IF NOT EXISTS examples (
    id               INTEGER PRIMARY KEY,
    section_id       INTEGER REFERENCES sections(id) ON DELETE CASCADE,
    code             TEXT NOT NULL,
    language         TEXT NOT NULL DEFAULT 'python',
    is_doctest       INTEGER NOT NULL DEFAULT 0,
    ordinal          INTEGER NOT NULL DEFAULT 0
);
```

6. `synonyms` — exactly as build guide §7:
```sql
CREATE TABLE IF NOT EXISTS synonyms (
    id               INTEGER PRIMARY KEY,
    concept          TEXT NOT NULL,
    expansion        TEXT NOT NULL,
    UNIQUE(concept)
);
```

7. `redirects` — exactly as build guide §7:
```sql
CREATE TABLE IF NOT EXISTS redirects (
    id               INTEGER PRIMARY KEY,
    doc_set_id       INTEGER NOT NULL REFERENCES doc_sets(id) ON DELETE CASCADE,
    old_uri          TEXT NOT NULL,
    new_uri          TEXT NOT NULL,
    UNIQUE(doc_set_id, old_uri)
);
```

8. `ingestion_runs` — exactly as build guide §7:
```sql
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id               INTEGER PRIMARY KEY,
    source           TEXT NOT NULL,
    version          TEXT NOT NULL,
    status           TEXT NOT NULL,
    started_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at      TEXT,
    artifact_hash    TEXT,
    notes            TEXT
);
```

**3 FTS5 virtual tables** (STOR-02 — all use corrected tokenizer, NO Porter stemming):

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
    heading, content_text,
    content='sections', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2 tokenchars '._'"
);

CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    qualified_name, module,
    content='symbols', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2 tokenchars '._'"
);

CREATE VIRTUAL TABLE IF NOT EXISTS examples_fts USING fts5(
    code,
    content='examples', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2 tokenchars '._'"
);
```

The file should have a header comment noting:
- Schema version, date, and the project name
- That FTS5 tokenizer is deliberately NOT using Porter stemming (STOR-02)
- That sections.uri has no standalone UNIQUE (STOR-04, cross-version safety)
- That symbols uses composite UNIQUE(doc_set_id, qualified_name, symbol_type) (STOR-03)
</action>
<acceptance_criteria>
- `src/mcp_server_python_docs/storage/schema.sql` exists
- File contains `CREATE TABLE IF NOT EXISTS doc_sets`
- File contains `CREATE TABLE IF NOT EXISTS documents`
- File contains `CREATE TABLE IF NOT EXISTS sections`
- File contains `CREATE TABLE IF NOT EXISTS symbols`
- File contains `CREATE TABLE IF NOT EXISTS examples`
- File contains `CREATE TABLE IF NOT EXISTS synonyms`
- File contains `CREATE TABLE IF NOT EXISTS redirects`
- File contains `CREATE TABLE IF NOT EXISTS ingestion_runs`
- File contains `CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts`
- File contains `CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts`
- File contains `CREATE VIRTUAL TABLE IF NOT EXISTS examples_fts`
- All three FTS5 tables contain `tokenize="unicode61 remove_diacritics 2 tokenchars '._'"`
- `sections_fts` does NOT contain the string `porter`
- `symbols` table contains `UNIQUE(doc_set_id, qualified_name, symbol_type)`
- `symbols` table does NOT contain a standalone `UNIQUE(doc_set_id, qualified_name)` (without symbol_type)
- `sections` table does NOT contain a standalone `UNIQUE(uri)` constraint (only `UNIQUE(document_id, anchor)`)
- `documents` table does NOT contain a standalone `UNIQUE(uri)` constraint (only `UNIQUE(doc_set_id, slug)`)
- `doc_sets` table contains `language    TEXT NOT NULL DEFAULT 'en'`
- `symbols` table contains `document_id` and `section_id` columns with FK references
</acceptance_criteria>
</task>

</tasks>

<verification>
- [ ] schema.sql contains all 8 canonical tables from build guide §7
- [ ] schema.sql contains all 3 FTS5 virtual tables
- [ ] FTS5 tokenizer is `unicode61 remove_diacritics 2 tokenchars '._'` on ALL three tables
- [ ] No Porter stemming anywhere in schema.sql
- [ ] symbols UNIQUE constraint includes symbol_type (STOR-03)
- [ ] sections has no standalone UNIQUE(uri) (STOR-04)
- [ ] doc_sets.language defaults to 'en' (STOR-05)
- [ ] All CREATE statements use IF NOT EXISTS for idempotency
</verification>

<must_haves>
- Complete DDL with corrected FTS5 tokenizer (B1 blocker resolution)
- Composite symbol uniqueness constraint STOR-03
- Cross-version URI safety (no standalone UNIQUE(uri) on sections or documents) STOR-04
- doc_sets.language column with DEFAULT 'en' STOR-05
</must_haves>
