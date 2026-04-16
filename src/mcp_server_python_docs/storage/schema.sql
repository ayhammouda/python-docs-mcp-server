-- schema.sql — mcp-server-python-docs storage schema
-- Version: Phase 2 (2026-04-16)
--
-- Design decisions:
--   - FTS5 tokenizer: unicode61 remove_diacritics 2 tokenchars '._'
--     Porter stemming is deliberately NOT applied (STOR-02). Preserving
--     dots and underscores as token characters ensures Python identifiers
--     like asyncio.TaskGroup and json.dumps are indexed as single tokens.
--   - sections.uri has NO standalone UNIQUE constraint (STOR-04). Cross-version
--     URI overlap is safe; uniqueness is enforced only by UNIQUE(document_id, anchor).
--   - symbols uses UNIQUE(doc_set_id, qualified_name, symbol_type) (STOR-03)
--     instead of UNIQUE(doc_set_id, qualified_name), allowing the same name
--     to appear as both a function and a method.
--   - doc_sets.language defaults to 'en' (STOR-05), reserving space for
--     future i18n without a migration.

-- ────────────────────────────────────────────────────
-- Canonical tables (source of truth)
-- ────────────────────────────────────────────────────

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

CREATE TABLE IF NOT EXISTS examples (
    id               INTEGER PRIMARY KEY,
    section_id       INTEGER REFERENCES sections(id) ON DELETE CASCADE,
    code             TEXT NOT NULL,
    language         TEXT NOT NULL DEFAULT 'python',
    is_doctest       INTEGER NOT NULL DEFAULT 0,
    ordinal          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS synonyms (
    id               INTEGER PRIMARY KEY,
    concept          TEXT NOT NULL,
    expansion        TEXT NOT NULL,
    UNIQUE(concept)
);

CREATE TABLE IF NOT EXISTS redirects (
    id               INTEGER PRIMARY KEY,
    doc_set_id       INTEGER NOT NULL REFERENCES doc_sets(id) ON DELETE CASCADE,
    old_uri          TEXT NOT NULL,
    new_uri          TEXT NOT NULL,
    UNIQUE(doc_set_id, old_uri)
);

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

-- ────────────────────────────────────────────────────
-- FTS5 virtual tables (retrieval aid, not source of truth)
--
-- All use external content mode: data lives in canonical tables,
-- FTS indexes are rebuilt via INSERT INTO <fts>(fts) VALUES('rebuild').
--
-- Tokenizer: unicode61 remove_diacritics 2 tokenchars '._'
--   - unicode61: Unicode-aware tokenization
--   - remove_diacritics 2: map diacritical chars to ASCII equivalents
--   - tokenchars '._': treat dots and underscores as token characters
--     (not separators), so asyncio.TaskGroup is one token
--   - NO porter stemming: preserves exact Python identifier search
-- ────────────────────────────────────────────────────

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
