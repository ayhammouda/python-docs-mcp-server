---
phase: 09-compare-versions
plan: 03
subsystem: services
tags: [compare-versions, diff, regex-extractors, difflib, mcp-service, cross-ai-review]

# Dependency graph
requires:
  - "09-01 (locked extractor regexes: _NEW_IN_RE / _CHANGED_IN_RE / _DEPRECATED_IN_RE / _SEE_ALSO_LINK_RE + fallback policy)"
  - "09-02 (CompareVersionsResult + ChangeKind model contract, including signature_delta and note fields)"
provides:
  - "CompareService class with compare(symbol, v1, v2) -> CompareVersionsResult"
  - "Module-level extractors: _extract_new_in / _extract_changed_in / _extract_deprecated_in / _extract_see_also"
  - "Full behavioral test suite: tests/test_compare_versions.py (12 tests) with a self-contained compare_db fixture"
affects:
  - "src/mcp_server_python_docs/server.py (Plan 04 wires CompareService into the MCP tool surface)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Service composes existing primitives: validate_version + create_symbol_cache + ContentService.get_docs"
    - "Text-derived version signals (no structured signature/deprecation/see-also metadata in the index)"
    - "FIXED branch ordering per cross-AI review H2: validate -> resolve -> both-missing -> identical-versions"
    - "Self-contained inline pytest fixture (compare_db) mirroring multi_version_db; data-table-driven section/symbol seeding"

key-files:
  created:
    - src/mcp_server_python_docs/services/compare.py
    - tests/test_compare_versions.py
  modified: []

key-decisions:
  - "H2: identical-versions ('unchanged') check runs AFTER the both-missing SymbolNotFoundError raise, so compare('does.not.exist','3.11','3.11') raises rather than returning unchanged"
  - "H4: compare.py never imports or names VersionNotFoundError — it propagates from validate_version; the grep gate (count == 0) required rewording docstring/comments to avoid the literal type name"
  - "M1: signature_delta is the first-non-empty-line heuristic, advisory only; tested on both a signature line and a prose-only line change"
  - "M2: PageNotFoundError in the both-present branch returns change='changed' + note (not the prior false-negative 'unchanged')"

requirements-completed: [CMPR-01, CMPR-02, CMPR-03]

# Metrics
duration: ~12min
completed: 2026-05-28
tasks: 3
files_changed: 2
---

# Phase 09 Plan 03: compare-service Summary

Implemented `CompareService.compare(symbol, v1, v2)` — the behavioral core of
Phase 09 — composing `validate_version`, `create_symbol_cache`, and
`ContentService.get_docs` into the four diff branches (added / removed / changed
/ unchanged), the version/symbol error paths, and the see-also / deprecation /
signature-delta text heuristics, with every cross-AI review finding (H2, H3, H4,
M1, M2, M3, L1) closed by a passing test or source-level assertion.

## What Was Built

- **`src/mcp_server_python_docs/services/compare.py`** — `CompareService` with a
  sync, `@log_tool_call("compare_versions")`-decorated `compare` method and four
  module-level extractors using the locked 09-01 regexes. Branch ordering is the
  H2 fix: validate both versions → resolve symbol in both → raise
  `SymbolNotFoundError` if missing in both → only then handle `v1 == v2`.
- **`tests/test_compare_versions.py`** — 12 tests over a self-contained
  `compare_db` fixture (3.10 not-default, 3.11 default) seeded from data tables.

## Tasks Completed

| Task | Name | Commit | Files |
| ---- | ---- | ------ | ----- |
| 1 | Implement CompareService (H2 ordering, signature_delta M1) | `8bed828` | src/mcp_server_python_docs/services/compare.py |
| 2 | Full behavioral test suite (H2/H3/H4/M1/M2/M3/L1) | `471defb` | tests/test_compare_versions.py |
| 3 | Full-suite regression gate | (no file changes — verification only) | — |

## Verification

- `uv run ruff check src/ tests/` — clean.
- `uv run pyright src/` — 0 errors, 0 warnings.
- `uv run pytest --tb=short -q` — **281 passed** (269 prior + 12 new); no regressions.
- `uv run pytest tests/test_compare_versions.py -q` — 12 passed.
- Source greps: `signature_change` count 0 (M1), `VersionNotFoundError` count 0
  in compare.py (H4), `@log_tool_call` present, M2 note text present,
  `signature_delta` present.
- H2 ordering confirmed by line order: `validate_version` (L153-154) →
  `self._resolve` (L157-158) → `raise SymbolNotFoundError` (L164) → `if v1 == v2`
  (L169).
- `test_five_tools_registered` still passes — Plan 03 does not touch
  `server.py`/`app_context.py`, so the 5-tool count is unchanged (Plan 04 owns
  the bump to 6).

## Cross-AI Review Findings Addressed

- **H2** — Identical-versions check moved after the both-missing
  `SymbolNotFoundError` raise. Tests `test_compare_identical_versions_missing_symbol_raises`
  and `test_compare_neither_version_has_symbol` enforce it.
- **H3** — See-also added (`test_compare_see_also_added`), see-also removed
  (`test_compare_see_also_removed`), and deprecation
  (`test_compare_deprecated_in_v2`) each have a dedicated passing test.
- **H4** — `compare.py` neither imports nor names `VersionNotFoundError`; tests
  import it from `..errors`. The grep gate (count == 0) is green.
- **M1** — Field is `signature_delta` (advisory); exercised by both
  `test_compare_changed_signature` (line 1 is a signature) and
  `test_compare_signature_delta_documents_prose_change` (line 1 prose-only).
- **M2** — `PageNotFoundError` in the both-present branch returns
  `change='changed'` + the documented note; covered by
  `test_compare_page_not_available_returns_changed_with_note`.
- **M3** — `test_compare_unknown_version_raises_with_indexed_list` asserts both
  the missing version (`3.99`) and both indexed versions (`3.10`, `3.11`) appear
  in the error message.
- **L1** — Token-frugality test is documented as a regression smoke check; the
  headline 'added' result serializes well under 1200 bytes.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Reworded compare.py docstring/comments to satisfy the H4 grep gate (count == 0)**
- **Found during:** Task 1 verification.
- **Issue:** The plan's H4 acceptance criterion requires
  `grep -c "VersionNotFoundError" src/.../compare.py` to return 0 — i.e. the
  literal string must not appear *at all*. The initial implementation already
  did not import the type, but three docstring/comment lines referenced it by
  name to explain the propagation behavior, so the grep returned 3.
- **Fix:** Reworded those three lines to describe the unknown-version error
  without naming the type (e.g. "validate_version raises the unknown-version
  error from its own module"). No executable behavior changed.
- **Files modified:** src/mcp_server_python_docs/services/compare.py
- **Verification:** `grep -c "VersionNotFoundError" ...` returns 0; ruff +
  pyright still pass.
- **Committed in:** `8bed828` (Task 1 commit).

**Total deviations:** 1 auto-fixed (1 blocking). Cosmetic prose change only; no
scope creep.

## Known Stubs

None. `CompareService` is fully wired against real SQLite reads through
`ContentService`; the signature_delta heuristic is intentionally advisory (M1,
documented in both the model field description and the prose-change test).

## Notes for Downstream Plans

- **Plan 04** can `from mcp_server_python_docs.services.compare import CompareService`
  and instantiate `CompareService(db, content_service)` with no further changes,
  then update `test_five_tools_registered` (5 → 6) when it registers the
  `compare_versions` tool.

## Self-Check: PASSED

- FOUND: src/mcp_server_python_docs/services/compare.py (contains `class CompareService:` and `def compare(self, symbol: str, v1: str, v2: str)`)
- FOUND: tests/test_compare_versions.py (contains `def test_compare_added_in_v2` and the `compare_db` fixture)
- FOUND: commit 8bed828 (Task 1)
- FOUND: commit 471defb (Task 2)
- Task 3 intentionally has no commit (verification-only, no file changes).

---
*Phase: 09-compare-versions*
*Completed: 2026-05-28*
