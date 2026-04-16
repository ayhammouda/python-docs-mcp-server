---
status: all_fixed
phase: 02-schema-storage
fixed_at: 2026-04-15
findings_in_scope: 2
fixed: 2
skipped: 0
iteration: 1
---

# Phase 2: Schema & Storage -- Code Review Fix Report

## Fix Scope

Critical + Warning findings only (default scope).

## Fixes Applied

### WR-01: `executescript()` implicit COMMIT warning -- FIXED

**File:** `src/mcp_server_python_docs/storage/db.py`
**Commit:** fc14d0c

Added a `Warning:` section to `bootstrap_schema()` docstring documenting that `executescript()` issues an implicit `COMMIT` before executing the DDL. Future callers will see this warning and avoid calling the function mid-transaction with uncommitted writes.

### WR-02: Fragile triple-quote state machine -- FIXED

**File:** `tests/test_schema.py`
**Commit:** fc14d0c

Replaced the manual triple-quote counting state machine with `ast`-based string literal detection via a new `_string_literal_lines()` helper. The helper uses `ast.parse()` + `ast.walk()` to reliably identify all lines that are part of string literals (including triple-quoted docstrings, f-strings, raw strings, and any other edge cases). The test now:

1. Parses each `.py` file with `ast.parse()`
2. Walks the AST to collect line ranges for all `ast.Constant` (str) and `ast.JoinedStr` (f-string) nodes
3. Skips any line that is a comment (`#`) or part of a string literal
4. Only flags `~/.cache` occurrences in executable code

## Info Findings (out of scope)

The following info findings were not in fix scope (use `--all` to include):

- **IN-01:** FTS5 DROP without coordinated rebuild -- documented design, not a bug
- **IN-02:** Missing FK indexes in schema.sql -- deferred to Phase 3
- **IN-03:** Hardcoded rowid in test example insert -- minor test hygiene

## Verification

- All 28 tests pass (0 regressions)
- ruff: 0 findings
- pyright: 0 errors, 0 warnings
