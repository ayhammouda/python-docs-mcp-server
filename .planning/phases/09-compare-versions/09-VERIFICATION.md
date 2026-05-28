---
phase: 09-compare-versions
verified: 2026-05-29T00:10:00Z
status: passed
score: 5/5 success criteria + all PLAN must_haves verified
overrides_applied: 0
re_verification: # not applicable — initial verification
  previous_status: none
gaps: []
---

# Phase 09: compare_versions(symbol, v1, v2) Verification Report

**Phase Goal:** Ship the `compare_versions(symbol, v1, v2)` MCP tool — a new tool that diffs a Python stdlib symbol's documentation between two versions (GitHub issue #32).
**Verified:** 2026-05-29T00:10:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (CONTEXT.md success criteria — the contract)

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| 1 | `compare_versions("asyncio.TaskGroup", "3.10", "3.11")` returns a clear diff showing the symbol was newly introduced in 3.11 (`added`/`new_in` marker) | ✓ VERIFIED | `compare.py:202-213` added branch sets `change="added"` + extracts `new_in`. Test `test_compare_added_in_v2` asserts `change=="added"` and `new_in=="3.11"` — passing. |
| 2 | Comparing identical versions returns an empty diff with explicit "no change" marker | ✓ VERIFIED | `compare.py:196-199` returns `change="unchanged"`. Test `test_compare_identical_versions` asserts unchanged + all delta fields None/empty — passing. |
| 3 | Missing-version cases return an actionable error with the indexed-version list | ✓ VERIFIED | `compare.py:180-181` calls `validate_version` first; `version_resolution.py:29` raises `VersionNotFoundError` with available list (MVER-03). Test `test_compare_unknown_version_raises_with_indexed_list` asserts message names `3.99`, `3.10`, `3.11` — passing. |
| 4 | Token cost of a typical diff is under 300 tokens | ✓ VERIFIED | `models.py` emits only non-None delta fields; `_SECTION_DIFF_MAX_CHARS=600`. Test `test_compare_diff_is_token_frugal` asserts `~tokens < 300` and serialized < 1200 bytes — passing (smoke check per L1). |
| 5 | Integration test exercises 3 representative symbols (changed, unchanged, missing) | ✓ VERIFIED | `tests/test_compare_versions.py` covers added/removed/changed/unchanged + both error paths + heuristics + M2 fallback + CR-01/WR-01/WR-02 regressions (21 tests, all passing). |

**Score:** 5/5 success criteria verified.

### CMPR acceptance criteria (CONTEXT.md §Requirements)

| Criterion | Status | Evidence |
| --- | --- | --- |
| CMPR-01: accepts `(symbol, v1, v2)`, returns structured diff (signature, docstring delta, deprecation flag, see-also +/-) | ✓ VERIFIED | `CompareService.compare` returns `CompareVersionsResult` with `signature_delta`, `section_diff`, `deprecated_in`, `see_also_added/removed`. Each has dedicated passing tests. |
| CMPR-02: both versions must be indexed; else actionable error pointing at build-index | ✓ VERIFIED | `validate_version` raises `VersionNotFoundError` listing indexed versions before any symbol work. |
| CMPR-03: JSON-serializable, token-frugal — no full re-printing of unchanged paragraphs | ✓ VERIFIED | Pydantic model `model_dump()`; section_diff truncated to 600 chars with line-boundary marker (WR-02); unchanged-case returns no diff body. |

### Required Artifacts

| Artifact | Expected | Status | Details |
| --- | --- | --- | --- |
| `src/mcp_server_python_docs/services/compare.py` | CompareService + compare() | ✓ VERIFIED | 312 lines; 4 branches, fixed H2 ordering, CR-01/WR-01/WR-02 fixes present. Wired into app_context + server. |
| `src/mcp_server_python_docs/models.py` | CompareVersionsResult model | ✓ VERIFIED | `class CompareVersionsResult(BaseModel)` with all delta fields + `note`, every field has `Field(description=...)`. `signature_delta` (not `signature_change`), advisory description present. |
| `tests/test_compare_versions.py` | Behavioral tests, production-shaped fixture | ✓ VERIFIED | 21 tests passing. Fixture stores EXTENSIONLESS `documents.slug` (`doc_slug = slug[:-5]`, line 145) matching ingestion ground truth — false-confidence masking is resolved. |
| `src/mcp_server_python_docs/app_context.py` | `compare_service` field | ✓ VERIFIED | `compare_service: CompareService` (line 29). |
| `src/mcp_server_python_docs/server.py` | `compare_versions` tool + lifespan wiring | ✓ VERIFIED | `CompareService(db, content_svc)` (line 163), passed to AppContext (line 189), `@mcp.tool def compare_versions` (line 393) delegating to `app_ctx.compare_service.compare`. |
| `tests/test_services.py` | 6-tool registration | ✓ VERIFIED | `test_six_tools_registered` asserts `len(tools) == 6` (line 450/455); schema test inspects `compare_versions` params (line 480-485). |
| `README.md` | six-tool list + compare_versions row | ✓ VERIFIED | "six read-only MCP tools" (line 46), "exposes six MCP tools" (line 180), `compare_versions` table row (line 189). |
| `.github/INTEGRATION-TEST.md` | all six tools + compare_versions call step | ✓ VERIFIED | Test 1 checklist lists all six tools incl `lookup_package_docs` (L2 gap closed) and a `compare_versions` call step (line 53). |
| `09-01-data-shape-spike-SUMMARY.md` | Locked regex patterns | ✓ VERIFIED | `## Locked regex patterns` section; regexes match compare.py verbatim. |

### Key Link Verification

| From | To | Via | Status | Details |
| --- | --- | --- | --- | --- |
| compare.py | cache.create_symbol_cache | import + `self._resolve(symbol, version)` | ✓ WIRED | `create_symbol_cache` returns `Callable[[str,str], CachedSymbol|None]`; `CachedSymbol` has `.uri`/`.anchor`. |
| compare.py | content.ContentService.get_docs | `self._content.get_docs(slug, version, anchor)` | ✓ WIRED | Called in `_section_text`; CR-01 candidate logic present. |
| compare.py | version_resolution.validate_version | import + call | ✓ WIRED | Called first in compare(); raises VersionNotFoundError from its own module. |
| compare.py | observability.log_tool_call | `@log_tool_call("compare_versions")` | ✓ WIRED | Decorator present on `compare` (line 168). |
| compare.py | VersionNotFoundError | (must NOT import — H4) | ✓ VERIFIED | grep confirms NOT imported; ruff F401 clean. |
| server.app_lifespan | CompareService | `CompareService(db, content_svc)` | ✓ WIRED | Line 163. |
| server.create_server | AppContext.compare_service | `app_ctx.compare_service.compare(...)` | ✓ WIRED | Line 407. |
| models.py | server.py | batch import incl CompareVersionsResult | ✓ WIRED | Line 30-37. |

### Data-Flow Trace (Level 4 — CR-01 focus)

| Concern | Finding | Status |
| --- | --- | --- |
| `documents.slug` shape (ingestion ground truth) | `sphinx_json.py:429` `slug = current_page_name` (EXTENSIONLESS); `:439` `doc_uri = f"{current_page_name}.html"`. Symbol URIs carry `.html`. | ✓ confirmed |
| Test fixture slug shape | `test_compare_versions.py:145` `doc_slug = slug[:-5] if slug.endswith(".html")` → stores EXTENSIONLESS slug, mirroring production. Old `.html`-in-both shape that masked CR-01 is gone. | ✓ FLOWING |
| compare `_section_text` resolution | `compare.py:153` tries `(page[:-5], page)` extensionless-first, mirroring `ranker._document_candidates`. Resolves real-index symbols instead of falling into M2 fallback. | ✓ FLOWING |
| CR-01 regression guard | `test_section_text_resolves_extensionless_slug_cr01` resolves `library/json.html#json.dumps` against extensionless slug AND asserts end-to-end `some.old_func` yields `deprecated_in="3.11"`, `note is None` (real metadata, not page-unavailable note). Passing. | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| --- | --- | --- | --- |
| Runtime tool enumeration = 6 incl compare_versions | `create_server(); asyncio.run(mcp.list_tools())` | count=6, names include `compare_versions` | ✓ PASS |
| Compare suite | `pytest tests/test_compare_versions.py tests/test_services.py::TestToolRegistration` | 21 passed | ✓ PASS |
| Deprecation regex version-agnostic | `_extract_deprecated_in("...3.12...")` | returns `3.12` | ✓ PASS |
| Full gate — ruff | `uv run ruff check src/ tests/` | All checks passed | ✓ PASS |
| Full gate — pyright | `uv run pyright src/` | 0 errors, 0 warnings | ✓ PASS |
| Full gate — pytest | `uv run pytest --tb=short -q` | 284 passed in 8.13s | ✓ PASS |

### Code Review Resolution Cross-Check (09-REVIEW.md, commit 0e16b34)

| Finding | Claimed Fix | Codebase Evidence | Status |
| --- | --- | --- | --- |
| CR-01 (blocker): slug-derivation mismatch | extensionless-first candidates + production-shaped fixture | `compare.py:153` + fixture `:145` + regression test | ✓ FIXED |
| WR-01: see-also over-capture | blank-line-bounded window | `compare.py:97-108` + `test_extract_see_also_excludes_unrelated_body_links_wr01` | ✓ FIXED |
| WR-02: mid-line diff truncation | line-boundary truncation + marker | `compare.py:267-284` + `test_section_diff_truncates_on_line_boundary_wr02` | ✓ FIXED |
| WR-03/WR-04 | accepted by-design (2-version semantics; advisory naming) | model descriptions align | ✓ ACCEPTED |
| IN-03: stale `test_create_server_has_three_tools` name | cosmetic, not fixed | name still stale at `test_services.py:411` (subset check, still correct) | ℹ️ INFO |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| --- | --- | --- | --- | --- |
| (none) | — | No TBD/FIXME/XXX/HACK/PLACEHOLDER/stub in compare.py, models.py, or test file | — | — |
| test_services.py | 411 | Stale test name `test_create_server_has_three_tools` (asserts 4-tool subset) | ℹ️ Info | Cosmetic only (IN-03, accepted in review); assertions correct, six-tool count covered by sibling test. Not a blocker. |

### Issue #32 Hygiene

`git log --oneline origin/main..HEAD | grep -iE "Closes|Fixes|Resolves" | grep "#32"` → ZERO matches. Closing keyword reserved for PR body per #35 retrospective. ✓ Clean.

### Human Verification Required

None. All success criteria are programmatically verified via the seeded fixture and runtime tool enumeration. Optional: live-index end-to-end MCP Inspector run per INTEGRATION-TEST.md Test 1 — covered by the documented runbook and not required for goal achievement (the CR-01 fix + production-shaped fixture close the previously-masked real-index gap).

### Gaps Summary

No gaps. The phase goal is achieved: a functional `compare_versions(symbol, v1, v2)` MCP tool is registered as the sixth tool, wired through AppContext and the FastMCP lifespan, returning a JSON-serializable token-frugal `CompareVersionsResult` across all four diff branches. The previously-identified blocker (CR-01 slug mismatch that degraded the tool to presence-only on real indexes) is fixed and guarded by a regression test using a production-shaped extensionless-slug fixture — verified against ingestion ground truth in `sphinx_json.py`. All canonical gates pass (ruff clean, pyright 0 errors, 284 tests).

---

_Verified: 2026-05-29T00:10:00Z_
_Verifier: Claude (gsd-verifier)_
