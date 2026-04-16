# Phase 1: Foundation & Stdio Hygiene & Symbol Slice - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-16
**Phase:** 01-foundation-stdio-hygiene-symbol-slice
**Areas discussed:** search_docs fallback, Synonyms scope in Phase 1, Schema drift-guard approach

---

## Gray Area Selection

User selected 3 of 4 gray areas for discussion:
- search_docs fallback (selected)
- Log format (NOT selected — auto-decided: logfmt/key=value)
- Synonyms scope in Phase 1 (selected)
- Schema drift-guard approach (selected)

---

## search_docs Fallback Behavior

### Non-symbol query handling

| Option | Description | Selected |
|--------|-------------|----------|
| Empty hits + note | Return SearchDocsResult(hits=[], note='Full-text search arrives in Phase 3...') | ✓ |
| isError: true | Return isError:true with limitation message | |
| LIKE fallback on qualified_name | Crude SQL LIKE over symbols for non-symbol queries | |
| You decide | Claude picks | |

**User's choice:** Empty hits + note (recommended)
**Notes:** Clean, honest, no isError for a normal operational limitation.

### Symbol no-match handling

| Option | Description | Selected |
|--------|-------------|----------|
| Empty hits | SearchDocsResult(hits=[]). Simple empty result. | ✓ |
| isError: true now | Surface SymbolNotFoundError early with fuzzy suggestion | |
| You decide | Claude picks | |

**User's choice:** Empty hits (recommended)
**Notes:** Keep Phase 1 simple. isError for SymbolNotFoundError is Phase 3 (SRVR-08).

### kind parameter handling

| Option | Description | Selected |
|--------|-------------|----------|
| Accept all, behave as symbol | Accept full Literal type, route everything to symbol fast-path, log fallback | ✓ |
| Only accept kind='symbol' | Raise ValueError for non-symbol kinds | |
| You decide | Claude picks | |

**User's choice:** Accept all, behave as symbol (recommended)
**Notes:** Stable schema from day one. No breaking schema changes when Phase 3/5 land.

### Integration test approach

| Option | Description | Selected |
|--------|-------------|----------|
| Stability test style | Structural assertions on asyncio.TaskGroup query. Permanent regression test. | ✓ |
| Smoke test only | Just verify len(hits) >= 1 without specific asserts | |
| You decide | Claude picks | |

**User's choice:** Stability test style (recommended)
**Notes:** Becomes a permanent regression guard across all phases.

---

## Synonyms Scope in Phase 1

| Option | Description | Selected |
|--------|-------------|----------|
| Minimal starter (~10 entries) + eager load | Ship ~10 representative entries with eager load and wheel test | |
| Full 100-200 entries now | Author complete curated table in Phase 1 | ✓ |
| Empty placeholder | Ship empty YAML with schema comment | |
| You decide | Claude picks | |

**User's choice:** Full 100-200 entries now
**Notes:** Upfront curation unblocks all retrieval and content ingestion work downstream. Flat map format, eager load via importlib.resources, wheel content test included.

---

## Schema Drift-Guard Approach

| Option | Description | Selected |
|--------|-------------|----------|
| Env var: UPDATE_SCHEMAS=1 pytest | Test rewrites fixtures when env var set. Zero extra scripts. | ✓ |
| Dedicated script: scripts/update_schemas.py | Standalone script for fixture generation | |
| pytest --update-snapshots flag | Custom conftest CLI flag | |
| You decide | Claude picks | |

**User's choice:** Env var: UPDATE_SCHEMAS=1 pytest (recommended)
**Notes:** Discoverable via test file docstring. Standard pattern.

---

## Claude's Discretion

- Log format: logfmt/key=value (auto-decided — user did not select this area for discussion)
- SIGPIPE handling implementation details
- sphobjinv download caching strategy
- Error message exact wording for missing index and missing FTS5

## Deferred Ideas

None — discussion stayed within phase scope.
