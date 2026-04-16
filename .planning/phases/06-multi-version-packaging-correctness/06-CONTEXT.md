# Phase 6: Multi-Version & Packaging Correctness - Context

**Gathered:** 2026-04-16
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

`build-index --versions 3.12,3.13` co-ingests both versions into a single index.db, default-version resolution returns 3.13 (doc_sets.is_default), cross-version URI collisions are harmless, and a built wheel installed via uvx or pipx is verifiably self-contained (synonyms.yaml inside the wheel, entry-point works, --version prints 0.1.0).

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — fully pre-specified phase. MVER-01 through MVER-05 and PKG-01 through PKG-04/06 requirements provide complete specifications.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Multi-Version & Packaging
- `python-docs-mcp-server-build-guide.md` §15 — Distribution: uvx, pipx, pyproject.toml entry-point
- `.planning/REQUIREMENTS.md` — MVER-01 through MVER-05, PKG-01 through PKG-04, PKG-06

### Prior Phase
- `.planning/phases/01-foundation-stdio-hygiene-symbol-slice/01-CONTEXT.md` — D-07: Wheel content test for synonyms.yaml established in Phase 1

</canonical_refs>

<code_context>
## Existing Code Insights

Codebase context will be gathered during plan-phase research.

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

*Phase: 06-multi-version-packaging-correctness*
*Context gathered: 2026-04-16*
