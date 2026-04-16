# Phase 2: Schema & Storage - Context

**Gathered:** 2026-04-16
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

Locked-in SQLite schema (schema.sql) with corrected FTS5 tokenizer (`unicode61 remove_diacritics 2 tokenchars '._'` — no Porter stemming), composite symbol uniqueness constraint, cross-version URI collision safety, `doc_sets.language` column for future i18n, and idempotent bootstrap — all verified before Phase 4 content ingestion so the tokenizer choice never triggers a full rebuild.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — pure infrastructure phase. Use ROADMAP phase goal, success criteria, build guide §7 (schema), and codebase conventions established in Phase 1 to guide decisions.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Schema & Storage
- `python-docs-mcp-server-build-guide.md` §7 — Complete schema DDL with all tables, FTS virtual tables, tokenizer configuration
- `.planning/REQUIREMENTS.md` — STOR-01 through STOR-05, STOR-09

### Prior Phase
- `.planning/phases/01-foundation-stdio-hygiene-symbol-slice/01-CONTEXT.md` — Phase 1 decisions (connection factory, platformdirs, FTS5 check already landed)

</canonical_refs>

<code_context>
## Existing Code Insights

Codebase context will be gathered during plan-phase research. Phase 1 establishes: `storage/db.py` (connection factory), `storage/schema.sql` (file location).

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. Refer to ROADMAP phase description, success criteria, and build guide §7 schema.

</specifics>

<deferred>
## Deferred Ideas

None — infrastructure phase, no discussion.

</deferred>

---

*Phase: 02-schema-storage*
*Context gathered: 2026-04-16*
