# Phase 4: Sphinx JSON Ingestion & Atomic-Swap Publishing - Context

**Gathered:** 2026-04-16
**Status:** Ready for planning
**Research flag:** Deeper research recommended before planning — re-verify Sphinx pins, JSON builder status, custom CPython extension serialization, end-to-end build time.

<domain>
## Phase Boundary

`build-index` acquires CPython source, stands up a dedicated venv with branch-pinned Sphinx, invokes `sphinx-build -b json` directly, parses .fjson files with per-document failure isolation, converts HTML to markdown, populates sections_fts/examples_fts/synonyms, then atomically swaps the new index.db with rollback — without crashing a running RO server.

</domain>

<decisions>
## Implementation Decisions

### HTML-to-Markdown Conversion
- **D-01:** Use `markdownify` library for converting Sphinx JSON HTML bodies to markdown (INGR-C-05). Actively maintained, 1.4M+ PyPI downloads/month, better at preserving structure of docstring-style HTML. Add to runtime deps.

### CPython Source Acquisition
- **D-02:** Shallow git clone: `git clone --depth 1 --branch v3.13.12 https://github.com/python/cpython.git` into a temp directory. ~50MB download, gets exact tagged state, simple cleanup via `shutil.rmtree()`. Requires `git` on PATH (universal assumption for developer tooling).

### Claude's Discretion
- Sphinx venv location (temp dir alongside clone vs cached in `~/.cache/mcp-python-docs/venvs/`)
- Smoke test queries for swap validation (PUBL-03 — "basic queries return expected row counts")
- Per-document failure isolation granularity within fjson parsing
- Build progress reporting to stderr during long sphinx-build run
- ingestion_runs table population (started_at, completed_at, artifact_hash, status)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Ingestion Design
- `python-docs-mcp-server-build-guide.md` §8 — Ingestion strategy: objects.inv, Sphinx JSON, CLI commands, atomic swap protocol
- `python-docs-mcp-server-build-guide.md` §9 — Protocol hygiene (stdout discipline during build-index)

### Requirements
- `.planning/REQUIREMENTS.md` — INGR-C-01 through INGR-C-09 (content ingestion), PUBL-01 through PUBL-06 (publishing & swap)

### Research
- `.planning/research/SUMMARY.md` — B4 (CPython Sphinx JSON build with pinned venv + per-doc failure), B5 (reader-handle stale after rename)

### Prior Phases
- `.planning/phases/01-foundation-stdio-hygiene-symbol-slice/01-CONTEXT.md` — Synonyms.yaml (100-200 entries) ships in Phase 1; Phase 4 populates synonyms DB table from it
- `.planning/phases/02-schema-storage/02-CONTEXT.md` — Schema DDL, FTS tokenizer configuration
- `.planning/phases/03-retrieval-layer/03-CONTEXT.md` — Retrieval module that Phase 4's ingested data feeds into

</canonical_refs>

<code_context>
## Existing Code Insights

Codebase context will be gathered during plan-phase research. Phase 4 creates `ingestion/cli.py`, `ingestion/sphinx_json.py`, `ingestion/publish.py` per build guide §13.

</code_context>

<specifics>
## Specific Ideas

No specific requirements beyond what's captured in decisions. The research flag on this phase means plan-phase should run `/gsd-research-phase 4` first to re-verify upstream Sphinx status.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 04-sphinx-json-ingestion-atomic-swap-publishing*
*Context gathered: 2026-04-16*
