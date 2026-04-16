# Phase 5: Services, Tool Polish & Caching - Context

**Gathered:** 2026-04-16
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

Full service layer wiring (SearchService/ContentService/VersionService), registration of get_docs and list_versions as MCP tools with same annotations + _meta hints as search_docs, LRU caching on hot reads, structured per-call stderr observability via service-method decorators, and standalone validate-corpus CLI.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — fully pre-specified phase. Build guide §4 (architecture), §11 (observability), §12 (caching) and SRVR-03/04/07, OPS-01 through OPS-05, PUBL-07 requirements provide complete specifications.

### Carrying Forward from Phase 1
- **D-10 (Phase 1, auto-decided):** Log format is logfmt/key=value on stderr. Phase 5 formalizes this via OPS-02 structured logging requirement.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Service & Observability Design
- `python-docs-mcp-server-build-guide.md` §4 — Architecture: 3-service layer, dependency rule
- `python-docs-mcp-server-build-guide.md` §11 — Observability: per-request logging fields
- `python-docs-mcp-server-build-guide.md` §12 — Caching: LRU with maxsize, process-lifetime scope

### Requirements
- `.planning/REQUIREMENTS.md` — SRVR-03/04/07, OPS-01 through OPS-05, PUBL-07

</canonical_refs>

<code_context>
## Existing Code Insights

Codebase context will be gathered during plan-phase research. Phase 5 wires up `services/search.py`, `services/content.py`, `services/version.py` and registers get_docs/list_versions tools in `server.py`.

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase.

</specifics>

<deferred>
## Deferred Ideas

None — infrastructure phase.

</deferred>

---

*Phase: 05-services-tool-polish-caching*
*Context gathered: 2026-04-16*
