# Phase 10 — whatsnew_for_version(version)

**Status:** Backlog (post-v0.1.5)
**Type:** New MCP tool

## Goal

Return the official "What's New in Python `<version>`" content as structured sections, scoped by topic — new modules, deprecations, removals, performance, syntax. The current alternative is for the agent to fetch a full HTML page; this tool returns just the requested sections.

## Depends on

- v0.1.5 ships.
- Ingestion already covers `whatsnew/<version>.html` (verify during phase planning).

## Requirements

- WNEW-01: Tool signature: `whatsnew_for_version(version: str, kind: str | None = None, start_index: int = 0, max_sections: int = 20)`. `kind` filters to a single `kind` value (see WNEW-02); `start_index` + `max_sections` paginate the response. Returns `{ "sections": [{title, anchor, body, kind}], "next_start_index": int | None }`.
- WNEW-02: `kind` enum: `new_module | new_feature | deprecation | removal | performance | syntax | other`.
- WNEW-03: Each section's `body` is capped at ~2k tokens. If a single section exceeds the cap, truncate with a clear marker and a `get_docs` hint. Pagination via `start_index` / `next_start_index` round-trips.

## Success criteria

1. `whatsnew_for_version("3.12")` returns ≥10 sections, each with a non-empty body.
2. Each section has a stable anchor matching docs.python.org.
3. Filtering by `kind` is supported (`whatsnew_for_version("3.12", kind="deprecation")`).
4. Missing version returns the same actionable error pattern as `compare_versions`.
5. Integration test in `tests/test_whatsnew.py` covers two representative versions.

## Plans

TBD.

## UI hint

No UI surface; pure MCP tool.

## Out of scope

- Third-party "what's new" guides.
- Cross-version aggregation (use `compare_versions` instead).
