# Phase 11 — detect_python_version v2 (venv-aware)

**Status:** Backlog (post-v0.1.5)
**Type:** Enhancement of existing MCP tool

## Goal

Make `detect_python_version` venv-aware: report the Python version of the *active virtual environment* in the user's project, not just the value from `.python-version` or `python3 --version` on PATH.

## Depends on

- v0.1.5 ships.
- v1 implementation at `src/mcp_server_python_docs/detection.py` (current 3-step detection order: `.python-version` → `python3 --version` → `sys.version_info`).
- v1 has NO dedicated unit test file — backfill `tests/test_detection.py` as part of this phase.

## Requirements

- DETV2-01: Detect active venv via the `VIRTUAL_ENV` env var; if present, read `$VIRTUAL_ENV/pyvenv.cfg` and parse the `version = X.Y[.Z]` key (the standard key written by `python -m venv`). Fall back to `version_info` only if `version` is absent — some tools write that variant.
- DETV2-02: Detect `.venv/` or `venv/` directories in cwd or its ancestors (up to project root).
- DETV2-03: Detect `uv`'s `.venv` and `poetry`'s `.venv` patterns.
- DETV2-04: Preserve v1 fallback chain unchanged below the new venv checks.
- DETV2-05: Return `(major_minor, source)` tuple. **New v2 sources:** `"venv:VIRTUAL_ENV"`, `"venv:.venv"`. **Existing v1 sources (preserved unchanged, see `src/mcp_server_python_docs/detection.py`):** `".python-version file"`, `"python3 in PATH"`, `"server runtime"`. Total: 5 distinct source strings.

## Success criteria

1. Inside an activated venv (3.12) on a 3.13 host: returns `("3.12", "venv:VIRTUAL_ENV")`.
2. With no venv but `.python-version` present: returns `("X.Y", ".python-version file")` — v1 behavior preserved verbatim.
3. With nothing else available: returns `(sys-version, "server runtime")` — v1 fallback preserved verbatim.
4. Backfill: `tests/test_detection.py` covers all five `source` cases (2 new + 3 preserved).
5. No regression in v1 callers (`server.py` MCP tool wrapper signature unchanged; existing source strings unchanged).

## Plans

TBD.

## UI hint

No UI surface; pure MCP tool.

## Out of scope

- conda / mamba environments (separate phase if demand surfaces).
- Cross-platform venv path quirks beyond Unix + Windows (both must be tested).
