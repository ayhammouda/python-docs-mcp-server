# Roadmap

## Shipped

- **v0.1.4** — pre-PyPI; installed via `uvx --from git+…`.
- **v0.1.5** — first PyPI release. Outcomes: positioning lockdown ("canonical Python stdlib oracle for AI coding agents — always free, always MIT, token-frugal"); PyPI publish via Trusted Publishing; benchmark harness against competing docs MCPs; coordinated launch post (personal blog, dev.to, Show HN). The GitHub repo rename to `python-stdlib-mcp` was dropped on 2026-05-14 — the repo and PyPI package keep the `python-docs-mcp-server` name.
- **v0.1.6** — MCP Registry hotfix: trimmed `server.json` description to ≤100 chars ([#30](https://github.com/ayhammouda/python-docs-mcp-server/pull/30)) so the package validates against the MCP Registry schema. Follow-up: RELEASE.md generalized to be version-agnostic ([#35](https://github.com/ayhammouda/python-docs-mcp-server/pull/35), [#36](https://github.com/ayhammouda/python-docs-mcp-server/pull/36)) so the next release follows the documented flow without one-time scaffolding.

## Backlog (post-v0.1.6)

Triaged 2026-05-26 — all three phases remain in scope. Phase 09 is up next.

| Phase | Title | Tool surface | Issue | Status |
|-------|-------|--------------|-------|--------|
| 09 | `compare_versions(symbol, v1, v2)` | New MCP tool | [#32](https://github.com/ayhammouda/python-docs-mcp-server/issues/32) | **In progress** — 3/5 plans |
| 10 | `whatsnew_for_version(version)` | New MCP tool | [#33](https://github.com/ayhammouda/python-docs-mcp-server/issues/33) | Backlog |
| 11 | `detect_python_version` v2 (venv-aware) | Existing tool enhancement | [#34](https://github.com/ayhammouda/python-docs-mcp-server/issues/34) | Backlog |

Phase CONTEXTs at [`phases/09-compare-versions/`](phases/09-compare-versions/09-CONTEXT.md), [`phases/10-whatsnew/`](phases/10-whatsnew/10-CONTEXT.md), [`phases/11-detect-venv/`](phases/11-detect-venv/11-CONTEXT.md). Implementation kickoff: `/gsd-plan-phase 0X`.

## Historical

Pre-2026-05-14 GSD workflow artifacts (phases 1–8 for v0.1.0) live in maintainers' local worktrees and are intentionally not tracked in this repo — they remain accessible to those who ran the original GSD workflow locally but are not load-bearing for current implementation work. With v0.1.5 the `.planning/` directory became a tracked, forward-facing surface for new phase CONTEXTs and roadmap entries.
