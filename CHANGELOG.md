# Changelog

All notable changes to `python-docs-mcp-server` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.5] — 2026-05-14

### Added

- **PyPI debut.** Install via `uvx python-docs-mcp-server` (no `--from` flag needed). The previous `uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git` path keeps working via the existing GitHub source, but the published wheel is now the canonical install route.
- `CHANGELOG.md` (this file).
- Locked positioning anchor: *"For AI coding agents writing Python, `python-docs-mcp-server` is the canonical Python stdlib oracle: exact symbols, exact sections, exact versions — offline, **always free, always MIT**, token-frugal."* See `.planning/POSITIONING.md` for the verbatim copy and per-surface adaptation rules.
- Forward-facing `.planning/phases/` directory. Three post-v0.1.5 backlog items have on-disk specs:
  - `09-compare-versions` — new MCP tool: `compare_versions(symbol, v1, v2)` structured diff.
  - `10-whatsnew` — new MCP tool: `whatsnew_for_version(version)` section-sliced "What's New" page.
  - `11-detect-venv` — venv-aware `detect_python_version` v2 (reads `VIRTUAL_ENV`, `.venv/`, `pyvenv.cfg`).

### Changed

- README hero rewritten around the canonical-source positioning. The positioning sentence now sits between the package title and the badge row, with a wedge-forward "Built for the moment your agent needs…" descriptive paragraph below the badges.
- `glama.json` `description`, `server.json` `description`, and `pyproject.toml` `description` aligned with the locked positioning. Each surface keeps the three required anchor phrases (canonical Python stdlib oracle / always free, always MIT / token-frugal) while adapting the wording for its display length.
- `AGENTS.md` "Context Hygiene" section now distinguishes `.planning/ROADMAP.md` and `.planning/phases/0X-…/0X-CONTEXT.md` (live, forward-looking specs) from older `.planning/` content (archival history).
- README install instructions, MCP-client config snippets, and validation commands now lead with the PyPI install (`uvx python-docs-mcp-server`). The pre-PyPI `--from git+…` blocks have been removed.

### Removed

- All `<!-- PRE-PYPI -->` fenced regions from `README.md` (11 blocks, 18 GitHub-source install URLs). They were scaffolding for the pre-v0.1.5 window where the only install path was a GitHub source URL.

### Notes

- The PyPI package name and the GitHub repository name both remain `python-docs-mcp-server`. A rename to `python-stdlib-mcp` was planned in the v0.1.5 scope (CR §9.2) but was dropped on 2026-05-14; the historical note is preserved in `.planning/ROADMAP.md`.
- Trusted Publishing on PyPI is wired with Sigstore attestations on every release artifact. The release workflow lives at `.github/workflows/release.yml` and runs on annotated tags matching `v*`.

## [0.1.4] — 2026-05-13

Pre-PyPI release. Installable only via `uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git`. Last release before the PyPI publish.
