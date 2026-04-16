# Phase 1: Foundation & Stdio Hygiene & Symbol Slice - Context

**Gathered:** 2026-04-16
**Status:** Ready for planning

<domain>
## Phase Boundary

A vertical slice of the MCP server: a FastMCP stdio server installable from source, wired with typed lifespan DI, surviving Claude Desktop tool invocation without a single byte of stdout pollution, and returning a real symbol hit for `asyncio.TaskGroup` backed by `sphobjinv`-parsed `objects.inv` data. Phase 1 ships `search_docs` only (get_docs + list_versions land in later phases). The symbol table is the only data source — no sections, no FTS tables, no content ingestion.

</domain>

<decisions>
## Implementation Decisions

### search_docs Fallback Behavior (Phase 1 Only)
- **D-01:** Non-symbol queries (e.g., "how do I parse JSON") return `SearchDocsResult(hits=[], note="Full-text search available after content ingestion. For now, search_docs resolves Python identifiers like asyncio.TaskGroup.")` — empty hits with an informational note, no `isError`.
- **D-02:** Symbol-shaped queries that don't resolve (e.g., `foo.bar`) return empty hits `SearchDocsResult(hits=[])` — no `isError`, no fuzzy suggestion. Simple empty result = no match.
- **D-03:** The `kind` parameter accepts all Literal values (`auto`, `page`, `symbol`, `section`, `example`) per SRVR-02 so the tool schema is stable from day one. All values route to the symbol fast-path in Phase 1. A stderr log line notes the fallback when `kind != "symbol"`.
- **D-04:** Phase 1 integration test is stability-test style: `tests/test_phase1_integration.py` asserts `search_docs("asyncio.TaskGroup").hits[0].uri` contains `"asyncio-task.html"` and `hits[0].kind` is a symbol type. Structural assertions that survive CPython doc revisions. This test becomes a permanent regression guard for all subsequent phases.

### Synonyms Scope
- **D-05:** Phase 1 ships the full curated 100-200 entry `synonyms.yaml` at `src/mcp_server_python_docs/data/synonyms.yaml`. Flat map format: `concept: [term1, term2, ...]` matching build guide §6. This upfront curation unblocks all downstream retrieval and content ingestion work.
- **D-06:** `app_lifespan` loads `synonyms.yaml` eagerly at startup via `importlib.resources` into `AppContext.synonyms: dict[str, list[str]]`. Matches SRVR-11 ("loaded at startup, not per-request") and SRVR-12 ("loaded via importlib.resources").
- **D-07:** Phase 1 includes a wheel content test: build the wheel, unzip, assert `mcp_server_python_docs/data/synonyms.yaml` is present. Addresses research blocker B7 early.

### Schema Drift Guard
- **D-08:** Pydantic schema-snapshot tests commit JSON fixtures to `tests/fixtures/` (e.g., `schema-search_docs-input.json`, `schema-search_docs-output.json`). Tests compare `model.model_json_schema()` output against committed fixtures.
- **D-09:** Fixture update mechanism: when `UPDATE_SCHEMAS=1` env var is set, the schema test rewrites committed fixtures instead of asserting. Discoverable via test file docstring.

### Claude's Discretion
- **Log format:** logfmt/key=value on stderr (not JSON). OPS-02 allows either; Claude picks logfmt for zero-dep greppability. Final format solidified in Phase 5 when OPS requirements land.
- **SIGPIPE handling details:** Claude picks the cleanest approach per HYGN-03 (likely `signal.signal(signal.SIGPIPE, signal.SIG_IGN)` + BrokenPipeError catch on shutdown).
- **sphobjinv download caching:** Claude decides whether `build-index` caches downloaded `objects.inv` files in `~/.cache/mcp-python-docs/downloads/` or re-downloads each run.
- **Error message exact wording:** Claude authors copy-paste stderr messages for missing index (SRVR-10) and missing FTS5 (STOR-08) per the platform-aware guidance in REQUIREMENTS.md.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Architecture & Design
- `python-docs-mcp-server-build-guide.md` — Full consolidated build guide. §4 (architecture), §9 (protocol hygiene), §13 (package structure), §14 (testing strategy) are load-bearing for Phase 1.

### Requirements
- `.planning/REQUIREMENTS.md` — Phase 1 requirements: HYGN-01–06, SRVR-01/02/05/06/09/10/11/12, STOR-06/07/08/10, INGR-I-01–06, CLI-01/03.
- `.planning/PROJECT.md` — Locked tech decisions, constraints, out-of-scope list.

### Research
- `.planning/research/SUMMARY.md` — Research blockers B1–B7 mapped to phases. B3 (os.dup2 fd redirect), B6 (Pydantic schema snapshot), B7 (synonyms.yaml in wheel) are Phase 1.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- No existing code — Phase 1 creates the package from scratch.

### Established Patterns
- No patterns yet — Phase 1 establishes them. Follow build guide §13 package structure.

### Integration Points
- No existing integration points — Phase 1 creates the entry point (`__main__.py`) and server (`server.py`).

</code_context>

<specifics>
## Specific Ideas

No specific requirements beyond what's captured in decisions above. The build guide is the primary reference — Phase 1 executes its Week 1 plan with the decisions captured here.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 01-foundation-stdio-hygiene-symbol-slice*
*Context gathered: 2026-04-16*
