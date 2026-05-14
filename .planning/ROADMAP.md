# Roadmap

## Current

- **v0.1.4** — shipped (pre-PyPI; installed via `uvx --from git+…`)
- **v0.1.5** — in progress (PyPI debut, repo rename to `python-stdlib-mcp`, positioning lockdown, benchmark harness, launch post). See [`CHANGE-REQUEST-v0.1.5-launch.md`](../CHANGE-REQUEST-v0.1.5-launch.md) for the full scope.

## Backlog (post-v0.1.5)

| Phase | Title | Tool surface | Issue |
|-------|-------|--------------|-------|
| 09 | `compare_versions(symbol, v1, v2)` | New MCP tool | #TBD-link-after-PR-merges |
| 10 | `whatsnew_for_version(version)` | New MCP tool | #TBD-link-after-PR-merges |
| 11 | `detect_python_version` v2 (venv-aware) | Existing tool enhancement | #TBD-link-after-PR-merges |

These are planned, not committed. Phase CONTEXTs at [`phases/09-compare-versions/`](phases/09-compare-versions/09-CONTEXT.md), [`phases/10-whatsnew/`](phases/10-whatsnew/10-CONTEXT.md), [`phases/11-detect-venv/`](phases/11-detect-venv/11-CONTEXT.md). Implementation kickoff requires `/gsd-plan-phase 0X`.

## Historical

Pre-2026-05-14 GSD workflow artifacts (phases 1–8 for v0.1.0) live in maintainers' local worktrees and are intentionally not tracked in this repo. See the note in `.git/info/exclude` for the prior policy. With v0.1.5 the `.planning/` directory becomes a tracked, forward-facing surface for new phase CONTEXTs and roadmap entries.
