# Plan 02-A Summary: Full schema.sql with corrected FTS5 tokenizer

**Status:** Complete
**Started:** 2026-04-16
**Completed:** 2026-04-16

## What Was Built

Complete `schema.sql` DDL file at `src/mcp_server_python_docs/storage/schema.sql` containing all 8 canonical tables and 3 FTS5 virtual tables from build guide section 7, with the following corrections from the build guide defaults:

- FTS5 tokenizer: `unicode61 remove_diacritics 2 tokenchars '._'` on all three FTS tables (sections_fts, symbols_fts, examples_fts) -- NO Porter stemming (STOR-02)
- symbols: `UNIQUE(doc_set_id, qualified_name, symbol_type)` instead of `UNIQUE(doc_set_id, qualified_name)` (STOR-03)
- sections: dropped standalone `UNIQUE(uri)`, keeping only `UNIQUE(document_id, anchor)` (STOR-04)
- documents: dropped standalone `UNIQUE(uri)`, keeping only `UNIQUE(doc_set_id, slug)` (cross-version safety)
- doc_sets.language: `DEFAULT 'en'` preserved (STOR-05)

## Self-Check: PASSED

- [x] All 8 canonical tables present
- [x] All 3 FTS5 virtual tables present
- [x] No Porter stemming in any tokenizer directive
- [x] Composite UNIQUE on symbols includes symbol_type
- [x] No standalone UNIQUE(uri) on sections or documents

## Key Files

### Created
- `src/mcp_server_python_docs/storage/schema.sql`

## Deviations

- Also dropped `UNIQUE(uri)` from `documents` table (not just sections) for consistent cross-version safety. The build guide had `UNIQUE(uri)` on documents, but the same rationale as STOR-04 applies: two versions can have the same document URI.
