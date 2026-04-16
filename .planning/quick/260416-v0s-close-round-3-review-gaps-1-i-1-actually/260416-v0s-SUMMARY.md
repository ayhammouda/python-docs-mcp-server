---
phase: quick
plan: 260416-v0s
status: complete
subsystem: multi
tags: [code-review, round-3, follow-up]
source_review: "Round 3 /superpowers:requesting-code-review against 7f9e84c..75bdd80"
dependency_graph:
  requires: [260416-u2r]
  provides: [clean-after-round-3-review]
  affects:
    - src/mcp_server_python_docs/services/content.py
    - src/mcp_server_python_docs/services/search.py
    - src/mcp_server_python_docs/ingestion/publish.py
tech_stack:
  added: []
  patterns:
    - "Content-text fallback when sections are empty (I-1)"
    - "Call-site kind-gate before classify_query (M-5) in addition to length gate in classify_query (defense-in-depth)"
    - "finalize_for_swap on both success and failure publish paths"
key_files:
  modified:
    - src/mcp_server_python_docs/services/content.py
    - src/mcp_server_python_docs/services/search.py
    - src/mcp_server_python_docs/ingestion/publish.py
    - tests/test_services.py
    - tests/test_publish.py
  appended:
    - .planning/quick/260416-u2r-fix-review-findings-4-important-i-1-get-/260416-u2r-SUMMARY.md (Round 3 Ratifications section)
decisions: []
deviations: []
metrics:
  duration: "~4m 31s"
  tests_before: "243 passed, 3 skipped"
  tests_after: "246 passed, 3 skipped (+3 regression tests)"
  ruff: "clean"
  pyright: "9 pre-existing errors, 0 new"
  completed: "2026-04-16"
note: "Reconstructed from the executor's final report after worktree cleanup removed the original SUMMARY.md (untracked-file edge case)."
---

# Quick 260416-v0s: Close Round 3 Review Gaps Summary

Follow-up to quick task 260416-u2r. Round 3 code review against the 12-commit fix bundle flagged one Important (I-1 test-only, not actually fixed) and three Minor deviations/gaps. This task closes all four.

## Tasks Overview

| # | Finding | Type | Commit | Scope |
|---|---------|------|--------|-------|
| 1 | I-1 | fix | `bc3c674` | `services/content.py` + `tests/test_services.py` |
| 2 | M-5 | fix | `21ebc46` | `services/search.py` + `tests/test_services.py` |
| 3 | finalize on failure | fix | `f1b368b` | `ingestion/publish.py` + `tests/test_publish.py` |
| 4 | I-3 ratification | docs | `8357f38` | `260416-u2r-SUMMARY.md` (append only) |
| 5 | Verification gate | — | — | pytest/ruff/pyright |

## What changed

- **I-1 (Task 1):** `ContentService.get_docs` now includes `content_text` in its documents SELECT and uses it as the fallback when `section_rows` is empty. The prior lock-in test from commit `6a72fe0` was replaced with a test that seeds a doc row with `content_text="hello world"` and zero sections and asserts `GetDocsResult(content="hello world", char_count=11, truncated=False, next_start_index=None)`.
- **M-5 (Task 2):** The `classify_query(...)` call in `services/search.py` is now gated behind `if kind in ("auto", "symbol")`; for `kind="section"`/`"example"`/`"page"` the query type is set directly without touching the symbols table. The length-2 short-circuit inside `classify_query` (from commit `2850e53`) is intentionally preserved as defense-in-depth. New mock-based test asserts `symbol_exists_fn` is not called when `kind="section"`.
- **finalize on failure (Task 3):** `publish_index()` now calls `finalize_for_swap(conn)` on the smoke-test failure branch before returning False, so failed builds leave the same clean sidecar state as successful ones. Test extended to force `run_smoke_tests` to fail and assert no `*-wal`/`*-shm` sidecars remain next to the failed build DB.
- **I-3 ratification (Task 4):** Appended a new `## Round 3 Ratifications` section to the u2r SUMMARY.md documenting that `COLLATE NOCASE` on `qualified_name` was chosen over `normalized_name`: correctness-equivalent (both case-insensitive), index behavior identical (neither column is indexed for this scan pattern), `CREATE INDEX idx_symbols_normalized` deferred to v1.1 if the symbol table grows materially.

## Verification (Task 5)

| Check | Baseline | After |
|---|---|---|
| `uv run pytest -q` | 243 passed, 3 skipped | **246 passed, 3 skipped** (+3 regression tests) |
| `uv run ruff check .` | clean | clean |
| `uv run pyright` | 9 errors, 0 new | 9 errors, 0 new |

## Out of scope (preserved)

- **IN-01** Windows `os.rename` in `rollback()` — untouched, explicitly deferred per REVIEW-FIX.md.
- **`retrieval/query.py`** — untouched. The length-2 short-circuit from the prior round stays as-is.

## Final HEAD

- Before this task: `75bdd80` (docs commit of round 2 bundle)
- Round 3 tip: `8357f38` (after 4 atomic commits)
- After merge: `c2ef99c` (merge-back commit on main)
