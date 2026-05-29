# [v0.3.0] docs — write ADR-006 (Serialization)

> **Confidence:** MEDIUM · **Wave:** lead · **Slug:** `adr-006-serialization`
> Create with: `gh issue create -F .planning/issues/v0.3.0/04-adr-006-serialization.md -l documentation,priority:P2`
> Branch: `agent/<issue-number>-adr-006-serialization`

## Context

- **Per-issue context file (read first):** [`.planning/agent-context/adr-006-serialization.md`](../../agent-context/adr-006-serialization.md)
- Pipeline: [`AGENT-EXECUTION-PIPELINE.md`](../../../AGENT-EXECUTION-PIPELINE.md)
- Roadmap: [`STRATEGIC-ROADMAP-2026-05-29.md`](../../../STRATEGIC-ROADMAP-2026-05-29.md) — principle **2.5**, **2.7**; decisions **5.3, 5.4, 5.5, 5.8**
- ADR-006 "specifically enables the v0.3.x `format` parameter work" (roadmap §4).

## Goal

Record the already-locked serialization decision as `docs/architecture/ADR-006-serialization.md` so the v0.3.x `format` work has a stable, citable contract.

## Acceptance criteria

- [ ] `docs/architecture/ADR-006-serialization.md` exists and fills **every** section of the template embedded below (no placeholder text left).
- [ ] Status is **Accepted** (decisions 5.4/5.5 are already locked) with date `2026-05-29` and decider `@ayhammouda`.
- [ ] The "Decision Outcome" states verbatim the locked shape: compact **JSON is the default**; `format="toon"` is **opt-in and gated by the v0.3.0 empirical study** (5.4); the `format` parameter exists on **`search_docs`, `list_versions`, `compare_versions` only**; **`get_docs` stays markdown** (5.5); **TOON-as-storage is rejected** (5.3).
- [ ] The "Layer Contract" section names the serializer as one of the eight layers (principle 2.7) and states its inputs (structured tool result model), outputs (wire string), and invariant (serialization is a pure function of the result + chosen format; no behavior change for clients that don't opt in).
- [ ] "Considered Options" includes at least: JSON-only, JSON-default-with-TOON-opt-in (chosen), and TOON-as-storage (rejected, ref 5.3); and notes that the win must hold **after client-side rewrap** (5.8), not just on raw payload.
- [ ] `uv run python-docs-mcp-server doctor` passes (this is a docs-only change; no code touched).

## Scope boundaries

**In scope:** one new ADR markdown file under `docs/architecture/`. Cross-link from the context file's decision log.

**Out of scope (stop and comment if it seems required):**
- **Implementing** the `format` parameter — that is v0.3.x, gated by the study.
- Inventing any serialization decision **not already in the roadmap**. The ADR *records* locked decisions; it does not make new ones. (Doing so is a pipeline §7 trigger — "cites a design choice not in the issue.")
- Editing tool signatures, `models.py`, or `server.py`.

## Forbidden-territory reminders (pipeline §2)

- No tool name/parameter/return-shape changes — this is a writing task only.
- Do not re-open locked decisions 5.3–5.5; cite them.

## Validation commands (pipeline §5)

```bash
uv run ruff check src/ tests/
uv run pyright src/
uv run pytest --tb=short -q
uv run python-docs-mcp-server doctor
```

## ADR template (use exactly this skeleton)

```markdown
# ADR-006: Serialization & Wire Format

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** @ayhammouda
- **Roadmap refs:** principles 2.5, 2.7; decisions 5.3, 5.4, 5.5, 5.8

## Context and Problem Statement
<!-- Why a serializer layer exists; token economy is empirical, not architectural. -->

## Decision Drivers
<!-- Token cost AND latency after client rewrap; backward-compat; cloneability. -->

## Considered Options
1. JSON only.
2. JSON default + `format="toon"` opt-in on structured tools.  (chosen)
3. TOON as the storage format.  (rejected — decision 5.3)

## Decision Outcome
<!-- The locked shape, stated plainly. Include the get_docs=markdown carve-out. -->

### Consequences
**Positive:** ...
**Negative / risks:** ...

## Layer Contract (principle 2.7)
- **Inputs:** ...
- **Outputs:** ...
- **Invariants:** ...

## Links
- STRATEGIC-ROADMAP-2026-05-29.md §2.5, §5.3–5.5, §5.8
- (future) v0.3.0 TOKEN-STUDY.md
```

## PR template & recovery

- Use `.github/PULL_REQUEST_TEMPLATE/agent.md`. Under "Why this approach", note the ADR only records roadmap-locked decisions.
- Ambiguity in what's locked? Stop and comment — do not invent.

## Effort estimate

~2 hours.
