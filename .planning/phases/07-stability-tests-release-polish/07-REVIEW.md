---
status: findings
depth: standard
files_reviewed: 7
finding_counts:
  critical: 2
  warning: 3
  info: 3
  total: 8
reviewed_at: 2026-04-15
phase: "07"
---

# Phase 07 Code Review

## Critical

### CR-01: `Path` undefined in doctor command (NameError at runtime)

**File:** `src/mcp_server_python_docs/__main__.py:424`
**Category:** Bug (runtime crash)

The `doctor()` function uses `Path.home()` at line 424 but `Path` is not imported in the doctor function scope. The doctor function imports `shutil` and `sqlite3` but not `Path` from `pathlib`. The `Path` import at file level is inside the `from pathlib import Path` that appears only in other functions' local imports.

```python
# Line 424 -- Path is not imported in this scope
check_path = Path.home()
```

**Impact:** If `check_path.exists()` is False (cache dir and its parent don't exist), doctor crashes with `NameError: name 'Path' is not defined`. This path is reachable when cache_dir doesn't exist and cache_dir.parent also doesn't exist (unlikely but possible in test envs or containers).

**Fix:** Add `from pathlib import Path` to the doctor function's local imports (line 347-348).

### CR-02: CI workflow will fail -- `uv sync --dev` does not install ruff/pyright

**File:** `.github/workflows/ci.yml:30` and `pyproject.toml:36-42`
**Category:** Bug (CI broken)

The CI runs `uv sync --dev` (line 30) then `uv run ruff check` (line 33) and `uv run pyright` (line 36). However, `ruff` and `pyright` are declared under `[project.optional-dependencies] dev = [...]` (PEP 566 extras), NOT under `[dependency-groups] dev = [...]` (PEP 735).

`uv sync --dev` installs PEP 735 dependency groups. Since there is no `[dependency-groups]` section in pyproject.toml, `--dev` installs nothing extra. The correct command is `uv sync --extra dev`.

**Impact:** Every CI run will fail at the "Run linter" step with `error: Failed to spawn: ruff`.

**Fix:** Either:
- (A) Change CI to `uv sync --extra dev`, or
- (B) Move dev deps from `[project.optional-dependencies]` to `[dependency-groups]`:
  ```toml
  [dependency-groups]
  dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.4", "pyright>=1.1"]
  ```

Option (B) is the modern uv-native approach and what `uv sync --dev` expects.

## Warning

### WR-01: Unused imports in test_stdio_smoke.py

**File:** `tests/test_stdio_smoke.py:17,20,21`
**Category:** Code quality (lint failure)

Three unused imports: `shutil`, `tempfile`, `time`. These were likely leftover from development and will cause ruff F401 violations once the CI lint step is working.

**Fix:** Remove the three unused imports.

### WR-02: 18 line-length violations in conftest.py symbol data

**File:** `tests/conftest.py:59-88`
**Category:** Code quality (lint failure)

The `_STABILITY_SYMBOLS` list has 18 lines exceeding the 100-character limit. These are test data tuples.

**Fix:** Either wrap the long tuples or add `# noqa: E501` comments. Wrapping is preferred for consistency with the project's ruff config.

### WR-03: Smoke tests silently pass when server response is missing

**File:** `tests/test_stdio_smoke.py:173,206,233`
**Category:** Test weakness

Three smoke tests use `if tools_resp is not None:` / `if call_resp is not None:` guards, meaning if the server fails to return the expected response ID, the test passes silently without asserting anything meaningful. The test docstrings claim to verify specific behaviors but the `if` guards make those assertions conditional.

```python
tools_resp = _find_response(responses, 2)
if tools_resp is not None:  # If None, all assertions are skipped
    assert "result" in tools_resp
    ...
```

**Impact:** A broken server that crashes before sending a response would pass these tests. The `test_all_stdout_is_valid_jsonrpc` test partially covers this (it asserts every line is valid JSON-RPC) but doesn't assert that specific response IDs were received.

**Fix:** Change `if tools_resp is not None:` to `assert tools_resp is not None, "No response received for request id 2"`.

## Info

### IN-01: Line-length violation in test_stdio_smoke.py method signature

**File:** `tests/test_stdio_smoke.py:141`
**Category:** Style

One method signature exceeds 100 chars. Minor, but will fail lint.

### IN-02: Redundant subprocess spawns in test_doctor.py

**File:** `tests/test_doctor.py`
**Category:** Performance

Tests 1-5 each spawn a separate subprocess for the doctor command when they could share a single subprocess invocation and assert multiple properties on the result. This adds ~5-10 seconds of test time unnecessarily.

**Impact:** Negligible for 8 tests. Acceptable tradeoff for test isolation and readability.

### IN-03: `time`, `shutil`, `tempfile` imports suggest incomplete cleanup

**File:** `tests/test_stdio_smoke.py`
**Category:** Observation

The unused imports (WR-01) suggest earlier test versions may have used these modules. No functional impact but indicates the file wasn't cleaned up before commit.
