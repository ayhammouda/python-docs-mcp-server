# Phase 3: Retrieval Layer - Context

**Gathered:** 2026-04-16
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

A pure-logic retrieval module (no MCP types, no SQL) that: (1) guards all FTS5 MATCH queries via fts5_escape() so adversarial input never crashes SQLite, (2) classifies symbol-shaped queries to fast-path via objects.inv symbol table before FTS, (3) expands synonym queries via the Phase 1 synonym table (space-separated OR expansion), (4) ranks via BM25 with column weights (heading > content_text, qualified_name > module) + FTS5 snippet() excerpts (~200 chars), and (5) enforces Unicode-safe budget truncation via apply_budget(). All domain errors (VersionNotFoundError, SymbolNotFoundError, PageNotFoundError) surface as isError: true content, not protocol errors.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — fully pre-specified phase. The build guide (§5 token efficiency, §6 synonym table, §10 error taxonomy) and RETR-01 through RETR-09 requirements provide complete specifications for all retrieval behaviors. Key implementation-level decisions:

- BM25 column weight multiplier values (direction locked: heading > content_text, qualified_name > module; exact multipliers are tuning knobs)
- fts5_escape implementation approach (escape chars from RETR-01 list, collapse FTS5 keywords)
- Symbol classifier edge cases (e.g., single-word module names like `re`, `os` — should check symbol table existence before classifying)
- Synonym expansion FTS5 integration (OR expansion per build guide §6 "space-separated terms" convention)
- isError routing architecture (named exceptions from §10 raised in retrieval, caught in service layer)
- apply_budget boundary behavior (truncate at max_chars, signal truncated: true, never split codepoint)

### Carrying Forward from Phase 1
- **D-01 (Phase 1):** Non-symbol queries return empty hits + note when only symbol table exists. Phase 3 replaces this with full FTS5 retrieval — the note is no longer needed.
- **D-03 (Phase 1):** `kind` parameter accepted all values in Phase 1 routing to symbol only. Phase 3 wires up `kind="auto"` intent router, `kind="section"`, `kind="example"`, `kind="page"` to their respective FTS tables.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Retrieval Design
- `python-docs-mcp-server-build-guide.md` §5 — Token efficiency: symbol fast-path, section windowing, budget enforcement
- `python-docs-mcp-server-build-guide.md` §6 — Synonym table: curated expansion, FTS5 integration
- `python-docs-mcp-server-build-guide.md` §10 — Error taxonomy: named exceptions, MCP error responses

### Requirements
- `.planning/REQUIREMENTS.md` — RETR-01 through RETR-09 (retrieval layer), SRVR-08 (isError routing)

### Prior Phases
- `.planning/phases/01-foundation-stdio-hygiene-symbol-slice/01-CONTEXT.md` — Phase 1 decisions on search_docs behavior, synonyms loading
- `.planning/phases/02-schema-storage/02-CONTEXT.md` — Phase 2 schema (FTS tokenizer, table constraints)

</canonical_refs>

<code_context>
## Existing Code Insights

Codebase context will be gathered during plan-phase research. Phase 1 establishes package structure. Phase 2 establishes schema. Phase 3 creates `retrieval/query.py`, `retrieval/ranker.py`, `retrieval/budget.py` per build guide §13.

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. Build guide and RETR requirements are the complete spec.

</specifics>

<deferred>
## Deferred Ideas

None — infrastructure phase, no discussion.

</deferred>

---

*Phase: 03-retrieval-layer*
*Context gathered: 2026-04-16*
