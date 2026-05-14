# Phase 09 — compare_versions(symbol, v1, v2)

**Status:** Backlog (post-v0.1.5)
**Type:** New MCP tool

## Goal

Add an MCP tool `compare_versions(symbol, v1, v2)` that returns a structured diff of a stdlib symbol's signature, behavior, and docstring between two Python versions.

## Depends on

- v0.1.5 ships (PyPI debut + repo rename complete).
- Index supports the two versions being compared (handled by `build-index`).

## Requirements

- CMPR-01: Tool accepts `(symbol: str, v1: str, v2: str)`. Returns a structured diff: signature change, docstring delta, deprecation flag, see-also additions/removals.
- CMPR-02: Both versions must already be indexed; if not, tool returns an actionable error pointing at `build-index`.
- CMPR-03: Diff format is JSON-serializable and token-frugal — no full re-printing of unchanged paragraphs.

## Success criteria

1. `compare_versions("asyncio.TaskGroup", "3.11", "3.12")` returns a clear, machine-readable diff highlighting the introduction in 3.11.
2. Comparing identical versions returns an empty diff with an explicit "no change" marker.
3. Missing-version cases return an actionable error with the indexed-version list.
4. Token cost of a typical diff is under 300 tokens (vs ~1500 for fetching both full doc pages).
5. Integration test in `tests/test_compare_versions.py` exercises 3 representative symbols (one changed, one unchanged, one missing).

## Plans

TBD — run `/gsd-plan-phase 09` after this issue is prioritized.

## UI hint

No UI surface; pure MCP tool.

## Out of scope

- Cross-language diffs (Python stdlib only).
- Non-symbol diffs (module-level changes belong in phase 10).
