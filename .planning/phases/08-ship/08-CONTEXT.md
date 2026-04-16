# Phase 8: Ship - Context

**Gathered:** 2026-04-16
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

v0.1.0 is manually verified end-to-end against both Claude Desktop and Cursor on the target query (`asyncio.TaskGroup`), published to PyPI via GitHub Actions Trusted Publishing with attestations, tagged in git, and README install instructions re-verified end-to-end against the published package.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — SHIP-01/02/06, PKG-05/07 requirements provide complete specifications. Key discretionary items:

- GitHub Actions release workflow structure (trigger on tag push vs manual dispatch)
- PyPI Trusted Publishing configuration (OIDC, environment protection rules)
- Attestation format and signing
- Manual integration test checklist format (documenting exact steps for human operator)

### Human-Required Steps
Phase 8 includes steps that require human action (not automatable by Claude):
- Configure Claude Desktop mcpServers block and verify asyncio.TaskGroup query
- Configure Cursor MCP settings and verify same query
- Trigger GitHub Actions release workflow (or push a tag)
- Verify README instructions on a fresh machine/venv

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Ship
- `.planning/REQUIREMENTS.md` — SHIP-01, SHIP-02, SHIP-06, PKG-05, PKG-07

</canonical_refs>

<code_context>
## Existing Code Insights

Codebase context will be gathered during plan-phase research. By Phase 8, the package is complete — this phase is verification and publishing.

</code_context>

<specifics>
## Specific Ideas

No specific requirements — shipping phase with manual verification steps.

</specifics>

<deferred>
## Deferred Ideas

None — final phase.

</deferred>

---

*Phase: 08-ship*
*Context gathered: 2026-04-16*
