# Phase 7: Stability Tests & Release Polish - Context

**Gathered:** 2026-04-16
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

~20 structural stability tests that survive CPython doc revisions, a `doctor` CLI subcommand for first-run diagnostics, and a README with copy-paste mcpServers snippets + install/first-run/troubleshooting sections. Phase 7 makes Phase 8's integration testing actionable — when something breaks, reports point to a fix.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — content and structure are fully specified by TEST-01 through TEST-06, CLI-02, SHIP-03 through SHIP-05 requirements. Key discretionary items:

- Stability test selection: which ~20 structural assertions best cover the stdlib surface area while surviving doc revisions
- doctor subcommand probe order and output format (PASS/FAIL per probe)
- README prose style and troubleshooting section depth
- CI configuration (GitHub Actions matrix: macOS + Linux, Python 3.12 + 3.13)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Testing & Release
- `python-docs-mcp-server-build-guide.md` §14 — Testing strategy: unit, storage, ingestion, tool, smoke, stability
- `python-docs-mcp-server-build-guide.md` §15 — Distribution: uvx, Claude Desktop/Cursor config snippets
- `.planning/REQUIREMENTS.md` — TEST-01 through TEST-06, CLI-02, SHIP-03 through SHIP-05

</canonical_refs>

<code_context>
## Existing Code Insights

Codebase context will be gathered during plan-phase research. By Phase 7, the full package is functional — this phase adds the test pyramid, doctor CLI, and README.

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

*Phase: 07-stability-tests-release-polish*
*Context gathered: 2026-04-16*
