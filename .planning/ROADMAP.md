# Roadmap

## Current

- **v0.1.4** — shipped (pre-PyPI; installed via `uvx --from git+…`)
- **v0.1.5** — in progress. Four coordinated workstreams: positioning lockdown ("canonical Python stdlib oracle for AI coding agents — always free, always MIT, token-frugal"); first PyPI publish via Trusted Publishing; benchmark harness against competing docs MCPs; coordinated launch post on a personal blog, dev.to, and Show HN. (Note: the GitHub repo rename to `python-stdlib-mcp` originally planned for v0.1.5 was dropped on 2026-05-14 — the repo and PyPI package keep the `python-docs-mcp-server` name.)

## Backlog (post-v0.1.5)

| Phase | Title | Tool surface | Issue |
|-------|-------|--------------|-------|
| 09 | `compare_versions(symbol, v1, v2)` | New MCP tool | [#32](https://github.com/ayhammouda/python-docs-mcp-server/issues/32) |
| 10 | `whatsnew_for_version(version)` | New MCP tool | [#33](https://github.com/ayhammouda/python-docs-mcp-server/issues/33) |
| 11 | `detect_python_version` v2 (venv-aware) | Existing tool enhancement | [#34](https://github.com/ayhammouda/python-docs-mcp-server/issues/34) |

These are planned, not committed. Phase CONTEXTs at [`phases/09-compare-versions/`](phases/09-compare-versions/09-CONTEXT.md), [`phases/10-whatsnew/`](phases/10-whatsnew/10-CONTEXT.md), [`phases/11-detect-venv/`](phases/11-detect-venv/11-CONTEXT.md). Implementation kickoff requires `/gsd-plan-phase 0X`.

## Historical

Pre-2026-05-14 GSD workflow artifacts (phases 1–8 for v0.1.0) live in maintainers' local worktrees and are intentionally not tracked in this repo — they remain accessible to those who ran the original GSD workflow locally but are not load-bearing for current implementation work. With v0.1.5 the `.planning/` directory becomes a tracked, forward-facing surface for new phase CONTEXTs and roadmap entries.
