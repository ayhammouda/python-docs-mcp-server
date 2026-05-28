---
phase: 09-compare-versions
plan: 02
subsystem: models
tags: [pydantic, compare-versions, outputschema, mcp-tool-contract]
requires:
  - "09-01 (data-shape spike: locked _NEW_IN_RE / _CHANGED_IN_RE / _DEPRECATED_IN_RE extractor names that populate the optional fields)"
provides:
  - "CompareVersionsResult Pydantic BaseModel (the typed compare_versions tool contract)"
  - "ChangeKind = Literal['added','removed','changed','unchanged'] discriminator alias"
affects:
  - "src/mcp_server_python_docs/services/compare.py (Plan 03 returns this model)"
  - "src/mcp_server_python_docs/server.py (Plan 04 imports + wires the tool)"
tech-stack:
  added: []
  patterns:
    - "BaseModel + per-field Field(description=...) (matches existing SearchDocsResult / PackageDocsResult)"
    - "Optional delta fields default to None / default_factory=list so the JSON diff shape is uniform across all four change cases"
key-files:
  created: []
  modified:
    - "src/mcp_server_python_docs/models.py"
decisions:
  - "signature_delta named per cross-AI review M1 (NOT signature_change); description marks it advisory/best-effort, not authoritative"
  - "note: str | None added per cross-AI review M2 so the service can flag partial-data states without forcing a wrong change category"
  - "No CompareVersionsInput model — server.py uses standalone Annotated[..., Field(...)] param aliases (matches PackageDocsInput removal audit)"
metrics:
  duration: ~6m
  completed: 2026-05-28
  tasks: 2
  files_changed: 1
---

# Phase 09 Plan 02: result-models Summary

Added the `CompareVersionsResult` Pydantic model and `ChangeKind` literal to
`models.py` as the typed wire contract for the upcoming `compare_versions` MCP
tool, applying the cross-AI review renames (`signature_delta`) and the new
partial-data `note` field — locking the diff shape before Plan 03 implements the
service.

## What Was Built

- **`ChangeKind`** literal alias: `Literal["added", "removed", "changed", "unchanged"]` — the diff discriminator.
- **`CompareVersionsResult(BaseModel)`** with:
  - Required (always populated): `symbol`, `v1`, `v2`, `change`.
  - Optional deltas (None / empty list when not applicable to the case):
    `new_in`, `removed_in`, `changed_in`, `deprecated_in`, `signature_delta`,
    `see_also_added`, `see_also_removed`, `section_diff`, `note`.
- Every field carries an explicit `Field(description=...)` so FastMCP's
  auto-derived `outputSchema` is informative to MCP clients.
- None-valued optionals serialize as `null` (verified via `model_dump_json`),
  giving a predictable diff shape across all four `change` cases.

## Tasks Completed

| Task | Name | Commit | Files |
| ---- | ---- | ------ | ----- |
| 1 | Add CompareVersionsResult model + ChangeKind literal | `bdcde46` | src/mcp_server_python_docs/models.py |
| 2 | Token-frugality smoke check (measurement only) | (no commit — no file changes) | — |

## Verification

- `uv run ruff check src/ tests/` — clean.
- `uv run pyright src/` — 0 errors, 0 warnings.
- `uv run pytest --tb=short -q` — 269 passed (no regressions to the existing 5-tool suite).
- Model construct + serialize asserts pass; invalid `change='wat'` raises `pydantic.ValidationError`.
- `grep -c "signature_change" src/mcp_server_python_docs/models.py` returns 0 (M1 rename confirmed).
- Token-frugality smoke check: `added=266B (~66tok) changed=818B (~204tok)` — both strictly under the 300-token budget (CMPR-03). The "changed" case used a 500-char `section_diff` plus populated `signature_delta` and `note`, confirming the model accepts both new fields together.

## Cross-AI Review Findings Addressed

- **M1** — `signature_change` renamed to `signature_delta`; description explicitly marks it advisory ("MAY be a docstring change or prose change rather than a true signature change — treat as advisory, not authoritative"). No occurrence of `signature_change` remains in the file.
- **M2** — Added `note: str | None = None` so the service (Plan 03) can honestly flag partial-data states (e.g. docs page not fetchable for one/both versions) without forcing the result into the wrong `change` category.
- **L1** — Documented the `len(json.dumps(...)) // 4` byte proxy as a regression smoke check, not a token guarantee. Real token assertions live in Plan 03's test suite.

## Deviations from Plan

None — plan executed exactly as written. Task 2 is a measurement-only task and
correctly produced no source/test changes (Plan 03 owns the test suite).

## Known Stubs

None. The model is a typed contract with no data sources to wire in this plan;
Plan 03 (CompareService) populates instances.

## Notes for Downstream Plans

- **Plan 03** can `from mcp_server_python_docs.models import CompareVersionsResult, ChangeKind` and return instances from `CompareService.compare(...)`, populating `signature_delta` and `note` directly.
- **Plan 04** can add `CompareVersionsResult` to the existing batch import in `server.py` — no new module-level state was added to `models.py`, so there is no circular-import risk.
- The review's HIGH finding about `test_five_tools_registered` (`assert len(tools) == 5`) is a Plan 04 concern and is untouched here; this plan adds no tool registration.

## Self-Check: PASSED

- FOUND: src/mcp_server_python_docs/models.py (contains `class CompareVersionsResult(BaseModel):` and `ChangeKind = Literal[...]`)
- FOUND: commit bdcde46 (Task 1)
- Task 2 intentionally has no commit (measurement only, no file changes) — consistent with the plan.
