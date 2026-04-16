---
status: all_fixed
phase: "06"
findings_in_scope: 4
fixed: 4
skipped: 0
iteration: 1
---

# Phase 6 Code Review Fix Report

## Fixes Applied

### CR-01 [fixed] -- SearchService._resolve_version returns None, skips default-version resolution

**Decision:** Cross-version search is intentional design. When `version=None`, `search_docs` returns hits from ALL versions so LLMs can compare. This is documented as a design decision.

**Changes:**
- Created `src/mcp_server_python_docs/services/version_resolution.py` with shared `validate_version()`, `resolve_default_version()`, `resolve_version_strict()`, and `resolve_version_permissive()` functions
- `SearchService._resolve_version` delegates to `resolve_version_permissive` (None stays None)
- `ContentService._resolve_version` delegates to `resolve_version_strict` (None resolves to default)
- Updated `search_docs` tool description in `server.py` to tell LLMs: "When version is omitted, searches across all versions. Pass the version from each hit's version field to get_docs for consistent results."

**Commit:** `fix(06): extract shared version resolution, document cross-version search`

---

### WR-01 [fixed] -- Duplicated _resolve_version logic across two services

**Combined with CR-01 fix above.** Both services now delegate to shared functions in `version_resolution.py`. Error message casing is now consistent (lowercase "version" in both paths). Maintenance risk eliminated.

**Commit:** Same as CR-01 (single atomic commit for related changes).

---

### WR-02 [fixed] -- test_packaging.py checks 7 deps but pyproject.toml has 8

**Change:** Added `"beautifulsoup4"` to the `required_dep_names` list in `test_required_deps_present`.

**Commit:** `fix(06): add beautifulsoup4 to dep assertion list in test_packaging`

---

### WR-03 [fixed] -- --version flag uses raise SystemExit(0) instead of ctx.exit(0)

**Change:** Replaced `raise SystemExit(0)` with `ctx.exit(0)` in `__main__.py:51`.

**Commit:** `fix(06): use ctx.exit(0) instead of raise SystemExit(0) for --version`

---

## Out of Scope (Info findings, not in fix scope)

- **IR-01:** Test row_factory coupling -- not addressed (info severity)
- **IR-02:** Test redundancy between version flag and installability tests -- not addressed (info severity)

## Verification

All 172 tests pass after fixes. No regressions introduced.
