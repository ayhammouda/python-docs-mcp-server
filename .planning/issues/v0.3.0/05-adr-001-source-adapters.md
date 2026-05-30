# [v0.3.0] docs — write ADR-001 (Source Adapters)

> **Confidence:** MEDIUM · **Wave:** trailing · **Slug:** `adr-001-source-adapters`
> Create with: `gh issue create -F .planning/issues/v0.3.0/05-adr-001-source-adapters.md -l documentation,priority:P2`
> Branch: `agent/<issue-number>-adr-001-source-adapters`

## Context

- **Per-issue context file (read first):** [`.planning/agent-context/adr-001-source-adapters.md`](../../agent-context/adr-001-source-adapters.md)
- Pipeline: [`AGENT-EXECUTION-PIPELINE.md`](../../../AGENT-EXECUTION-PIPELINE.md)
- Roadmap: [`STRATEGIC-ROADMAP-2026-05-29.md`](../../../STRATEGIC-ROADMAP-2026-05-29.md) — principles **2.1, 2.2, 2.7**
- Source-adapter touch-points (to describe, not change): `ingestion/cpython_versions.py`, `ingestion/sphinx_json.py`, `ingestion/inventory.py`, `services/package_docs.py`

## Goal

Record `docs/architecture/ADR-001-source-adapters.md`: the contract for canonical source connectors (CPython docs + PyPI metadata), establishing the layer-contract pattern that makes the architecture cloneable.

## Acceptance criteria

- [ ] `docs/architecture/ADR-001-source-adapters.md` exists and fills **every** section of the template embedded below.
- [ ] Status **Accepted**, date `2026-05-29`, decider `@ayhammouda`.
- [ ] The ADR documents the **two source adapters that exist today**: (1) CPython documentation source — cloned at a pinned ref, built via `sphinx-build -b json` (point at `ingestion/`); (2) PyPI metadata source — `lookup_package_docs` controlled metadata fetch (point at `services/package_docs.py`).
- [ ] It states principle **2.1** (canonical source only — no scraped mirrors) and **2.2** (offline-first at *query* time), and explicitly names the **one documented exception**: `lookup_package_docs` performs a controlled PyPI metadata lookup, which is a build/lookup-time network call, not a docs-query-time call.
- [ ] The "Layer Contract" section specifies the source-connector layer's inputs (version/identifier), outputs (canonical artifacts handed to ingestion), and invariants (pinned, reproducible, no third-party indexers), per principle 2.7.
- [ ] `uv run python-docs-mcp-server doctor` passes (docs-only change).

## Scope boundaries

**In scope:** one new ADR markdown file under `docs/architecture/`.

**Out of scope (stop and comment):** changing any ingestion/service code; inventing source-adapter behavior not present in the code or roadmap; documenting adapters that don't exist yet (e.g. Rust/Go) beyond a one-line "future adopters clone this contract" note.

## Forbidden-territory reminders (pipeline §2)

- No code changes; no schema; no workflow edits.
- The ADR must describe **current** behavior accurately — verify each claim against the cited files before writing it.

## Validation commands (pipeline §5)

Run the canonical four-command gate from `AGENT-EXECUTION-PIPELINE.md` §5. No
change-type-specific additional gates apply (this is a docs-only change).

## ADR template (use exactly this skeleton)

```markdown
# ADR-001: Source Adapters

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** @ayhammouda
- **Roadmap refs:** principles 2.1, 2.2, 2.7

## Context and Problem Statement
## Decision Drivers
## Considered Options
## Decision Outcome
<!-- Canonical source only; pinned, reproducible; PyPI metadata is the one
     controlled network lookup and is not a query-time call. -->
### Consequences
**Positive:** ...
**Negative / risks:** ...
## Layer Contract (principle 2.7)
- **Inputs:** ...
- **Outputs:** ...
- **Invariants:** ...
## Links
- STRATEGIC-ROADMAP-2026-05-29.md §2.1, §2.2, §2.7
```

## PR template & recovery

- Use `.github/PULL_REQUEST_TEMPLATE/agent.md`. Verify claims against the code before asserting them; cite file paths in the ADR.

## Effort estimate

~2 hours.
