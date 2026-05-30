# ADR-006: Serialization & Wire Format

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** @ayhammouda
- **Roadmap refs:** principles 2.5, 2.7; decisions 5.3, 5.4, 5.5, 5.8

## Context and Problem Statement

The server returns structured data for tools such as `search_docs`,
`list_versions`, and `compare_versions`, while `get_docs` returns the markdown
documentation body that users asked for. The structured tools need an explicit
serializer layer so the project can keep one stable internal result model while
choosing the wire representation independently.

Serialization is not a storage decision and does not change retrieval behavior.
Storage remains SQLite plus markdown. Token economy is empirical, not
architectural, so any non-JSON wire format must earn its place through measured
client behavior rather than architectural preference.

## Decision Drivers

- Preserve backward compatibility for clients that already consume compact JSON.
- Keep tool behavior cloneable by documenting the serializer as a distinct layer.
- Allow empirical token and latency work without coupling it to storage,
  retrieval, or transport.
- Measure the real client path: token cost and latency after client-side rewrap,
  not only raw payload size.
- Keep `get_docs` markdown because markdown is the canonical documentation body,
  not a structured result that needs alternate serialization.

## Considered Options

1. JSON only.
   - Simple, stable, and fully backward-compatible.
   - Leaves no room for an empirically proven smaller structured-tool format in
     v0.3.x.
2. JSON default + `format="toon"` opt-in on structured tools. (chosen)
   - Keeps compact JSON as the default wire format.
   - Allows TOON only as an explicit opt-in and only if the v0.3.0 empirical
     study proves a meaningful win after client-side rewrap, with acceptable
     latency.
   - Applies only to `search_docs`, `list_versions`, and `compare_versions`.
3. TOON as the storage format. (rejected - decision 5.3)
   - Rejected because storage stays SQLite plus markdown.
   - Would mix storage and wire-format concerns, weaken debuggability, and
     reopen a decision already closed by the roadmap.

## Decision Outcome

The locked shape is: compact JSON is the default; `format="toon"` is opt-in and
gated by the v0.3.0 empirical study; the `format` parameter exists on
`search_docs`, `list_versions`, `compare_versions` only; `get_docs` stays
markdown; TOON-as-storage is rejected.

The v0.3.x implementation may expose a `format` parameter for those three
structured tools only after the study in decision 5.8 confirms that any TOON win
holds after client-side rewrap, not just on raw payloads.

### Consequences

**Positive:** Existing clients keep receiving compact JSON unless they opt in to
another format. The serializer layer can evolve independently from retrieval,
storage, cache, and transport. The project has a clear contract to cite when the
v0.3.x `format` work begins.

**Negative / risks:** The TOON path cannot be treated as decided until the
empirical study is complete. Supporting more than one wire format adds testing
and documentation work for every structured tool that opts in. Client-side
rewrap may erase a raw-payload token win, which would make JSON-only the right
outcome.

## Layer Contract (principle 2.7)

- **Inputs:** Structured tool result model plus the chosen wire format.
- **Outputs:** Wire string returned to the MCP client.
- **Invariants:** Serialization is a pure function of the result and chosen
  format; clients that do not opt in see no behavior change.

The serializer is one of the eight documented layers in principle 2.7: source
connector, ingestion, storage, retrieval, budget, serializer, cache, and
transport.

## Links

- STRATEGIC-ROADMAP-2026-05-29.md §2.5, §5.3-§5.5, §5.8
- (future) v0.3.0 TOKEN-STUDY.md
