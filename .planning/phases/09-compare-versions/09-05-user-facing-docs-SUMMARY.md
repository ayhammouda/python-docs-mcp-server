---
phase: 09-compare-versions
plan: 05
subsystem: docs
tags: [docs, readme, integration-test, compare_versions, cmpr-01]
requires:
  - 09-04-mcp-tool-wiring (compare_versions registered, six-tool surface live)
provides:
  - User-facing tool list reflecting the six-tool surface (README.md)
  - Manual MCP QA runbook entry for all six tools (INTEGRATION-TEST.md)
affects:
  - README.md
  - .github/INTEGRATION-TEST.md
tech-stack:
  added: []
  patterns:
    - Markdown table row mirrors server.py docstring field names verbatim (signature_delta, note)
key-files:
  created:
    - .planning/phases/09-compare-versions/09-05-user-facing-docs-SUMMARY.md
  modified:
    - README.md
    - .github/INTEGRATION-TEST.md
decisions:
  - "L2 closure: added lookup_package_docs to the INTEGRATION-TEST Test 1 enumeration list (not just compare_versions), closing the pre-existing four-tool gap in one pass."
  - "lookup_package_docs Call-step deferred to a future cleanup; only the enumeration entry was added (scope discipline per L2 option (a))."
  - "MCP Registry v0.1.4 badge left unchanged — it tracks the registry's last-published version and is out of scope for Phase 09."
metrics:
  duration: ~4 minutes
  completed: 2026-05-28
  tasks: 3
  files: 2
---

# Phase 09 Plan 05: User-Facing Docs Summary

Updated README.md and the INTEGRATION-TEST.md manual QA runbook to reflect the now-live six-tool MCP surface, adding `compare_versions` everywhere and closing the pre-existing `lookup_package_docs` enumeration gap (L2). Source code was not touched.

## What Was Done

### Task 1 — README.md six-tool surface (commit 7f90eda)
- Changed `- five read-only MCP tools` to `- six read-only MCP tools` in "What you get".
- Changed `The server currently exposes five MCP tools:` to `... six MCP tools:` in the Tools section.
- Appended a `compare_versions` row to the Tools table after `detect_python_version`. The row uses the corrected field names per cross-AI review: `signature_delta` (M1, not `signature_change`) and includes the `note` delta (M2). Field names mirror the `compare_versions` docstring in `server.py` verbatim.
- Locked positioning hero sentence (line 5) and the v0.1.4 MCP Registry badge were left untouched.

### Task 2 — INTEGRATION-TEST.md Test 1 (commit da6d4c2)
- Replaced the four-tool enumeration checklist with the complete six-tool list in `server.py` registration order: `search_docs`, `get_docs`, `lookup_package_docs`, `list_versions`, `detect_python_version`, `compare_versions`.
- Inserted a `compare_versions` Inspector Call-step after `detect_python_version`: `symbol="asyncio.TaskGroup"`, `v1="3.10"`, `v2="3.11"`, expecting `change="added"` and `new_in="3.11"`, JSON under ~1200 bytes.
- L2 closure: `lookup_package_docs` was added to the enumeration list only. The Call-step for it is deferred to a future cleanup to avoid expanding scope beyond Phase 09.

### Task 3 — Final phase quality gate + issue-#32 hygiene check
Full AGENTS.md "Done Means" gate run fresh, all green:
- `uv run ruff check src/ tests/` → All checks passed.
- `uv run pyright src/` → 0 errors, 0 warnings, 0 informations.
- `uv run pytest --tb=short -q` → 281 passed in 8.12s.
- In-process enumeration → `['compare_versions', 'detect_python_version', 'get_docs', 'list_versions', 'lookup_package_docs', 'search_docs']` (exactly 6, includes `compare_versions`).
- Both docs mention `compare_versions` (`grep -l compare_versions README.md .github/INTEGRATION-TEST.md` lists both).

#### Issue-#32 hygiene check (per cross-AI review / #35 incident retrospective)
Command run:
```
git log --oneline origin/main..HEAD | grep -E 'Closes|Fixes|Resolves' | grep -E '#32'
```
Output: (empty — zero lines)
Match count: `0`

No intermediate commit on this branch contains `Closes #32` / `Fixes #32` / `Resolves #32`. The `Closes #32` keyword belongs in the PR body only; it is safe to open the PR that closes issue #32.

## Deviations from Plan

None — plan executed exactly as written. No Rule 1-4 deviations and no authentication gates encountered.

## Verification

| Check | Result |
|-------|--------|
| README says "six" tools in both prose sites | PASS |
| README Tools table has `compare_versions` row with `signature_delta` + `note`, no `signature_change` | PASS |
| Positioning hero sentence unchanged (`canonical Python stdlib oracle` present) | PASS |
| INTEGRATION-TEST Test 1 enumerates all six tools incl. `lookup_package_docs` (L2) | PASS |
| INTEGRATION-TEST has `compare_versions` Call-step with `change="added"` / `new_in="3.11"` | PASS |
| ruff / pyright / pytest (281) all green | PASS |
| In-process enumeration = exactly 6 tools | PASS |
| Issue-#32 hygiene grep = zero matches | PASS |

## Known Stubs

None. Both files are user-facing documentation wired to the live tool surface; no placeholder data.

## Self-Check: PASSED
- FOUND: README.md (modified, compare_versions present)
- FOUND: .github/INTEGRATION-TEST.md (modified, compare_versions present)
- FOUND: commit 7f90eda (Task 1)
- FOUND: commit da6d4c2 (Task 2)
