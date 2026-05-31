# Changelog

All notable changes to `python-docs-mcp-server` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-05-31

### Added

- **Retrieved-docs cache compression** — cached `get_docs` payloads now use a
  versioned codec layer with `none`, `zstd`, and reserved dictionary-backed
  `zstd-dict-v1` codec identifiers. Existing uncompressed cache rows remain
  readable, while new rows can store compressed payloads. ([#60](https://github.com/ayhammouda/python-docs-mcp-server/pull/60))
- **Pinned CPython documentation source configuration** — `build-index` now records
  explicit CPython documentation build targets so supported versions are tied to
  auditable upstream tags and Sphinx pins. ([#59](https://github.com/ayhammouda/python-docs-mcp-server/pull/59))
- **Architecture records for source and serialization boundaries** — added
  ADR-001 for canonical source adapters and ADR-006 for the JSON-default
  serialization contract with future TOON work gated by empirical study.
  ([#57](https://github.com/ayhammouda/python-docs-mcp-server/pull/57), [#55](https://github.com/ayhammouda/python-docs-mcp-server/pull/55))
- **Autonomous-agent execution pipeline** — added the issue templates, PR template,
  CODEOWNERS, per-issue planning context, and guardrail docs used to execute the
  v0.3.0 issue queue through branch-based human-reviewed PRs. ([#52](https://github.com/ayhammouda/python-docs-mcp-server/pull/52))

### Changed

- README and Glama metadata now describe the current six-tool public surface,
  including `compare_versions` and retrieved-docs cache behavior. ([#53](https://github.com/ayhammouda/python-docs-mcp-server/pull/53))
- The autonomous-agent pipeline now avoids the previous human-review deadlock
  while keeping branch-only execution, PR review, and no auto-merge as release
  guardrails. ([#54](https://github.com/ayhammouda/python-docs-mcp-server/pull/54))

### Security

- Added `zstandard` as a direct runtime dependency for cache compression.
  ([#58](https://github.com/ayhammouda/python-docs-mcp-server/pull/58))
- Documented the packaged YAML trust boundary and added regression coverage that
  keeps YAML parsing on `yaml.safe_load` only. ([#56](https://github.com/ayhammouda/python-docs-mcp-server/pull/56))

## [0.2.1] — 2026-05-29

### Fixed

- **`build-index` on Python 3.14** — Sphinx's parallel JSON build (`-j auto`)
  raised `_pickle.PicklingError` on Python 3.14, which flipped the default
  `multiprocessing` start method on POSIX from `fork` to `forkserver`
  (`forkserver` must pickle worker tasks, but Sphinx's `ParallelTasks` holds an
  unpicklable local closure). The builder now falls back to a serial build
  (`-j 1`) on 3.14+ while keeping the parallel path on `fork`-default
  interpreters (3.13 and earlier). Server runtime behavior is unchanged.

## [0.2.0] — 2026-05-29

### Added

- **New MCP tool: `compare_versions(symbol, v1, v2)`** (Phase 09). Diffs a Python
  stdlib symbol between two indexed versions and returns a structured result with
  `change=added|removed|changed|unchanged` plus optional `new_in`, `changed_in`,
  `deprecated_in`, `signature_delta` (advisory heuristic), `see_also_added`,
  `see_also_removed`, `section_diff`, and `note` fields. Token-frugal by design —
  emits only changed fields, not full page content. Both versions must be indexed;
  an unknown version raises an actionable error naming the available versions. This
  brings the server to a **six-tool surface**. ([#41](https://github.com/ayhammouda/python-docs-mcp-server/pull/41))

### Security

- Bumped two transitive dependencies to patched releases:
  - `idna` 3.13 → 3.17 — resolves CVE-2026-45409 (ReDoS in `idna.encode()`).
  - `starlette` 1.0.0 → 1.2.0 — resolves PYSEC-2026-161 ("BadHost", a `Host`-header
    auth bypass that explicitly affects MCP servers).
  Both arrive via the `mcp` / `sse-starlette` chain; no direct-dependency or API
  changes. `pip-audit` reports no known vulnerabilities after the bump.

### Changed

- `services/compare.py` extractors simplified — precompiled the four Sphinx-directive
  regexes and collapsed three near-identical `_extract_*` helpers into one.

### Docs

- README tools table and `.github/INTEGRATION-TEST.md` updated to document the full
  six-tool surface including `compare_versions`.
- Added `.github/TEST-STRATEGY.md` — canonical map of test layers, the feature→coverage
  matrix, and known gaps.

## [0.1.6] — 2026-05-14

### Fixed

- **MCP Registry publish** — `server.json` `description` shortened from 152 to 96 characters to comply with MCP Registry's schema constraint (`body.description ≤ 100 chars`). The v0.1.5 release succeeded on PyPI (no such limit) but the `publish-mcp-registry` workflow job failed validation; v0.1.5 therefore never reached MCP Registry. v0.1.6 ships the same wheel content as v0.1.5 with the corrected `server.json` so MCP Registry catches up. All three locked anchor phrases — *canonical Python stdlib oracle*, *always free, always MIT*, *token-frugal* — are preserved in the shortened form per `.planning/POSITIONING.md`'s adapt-for-length contract.

### Notes

- `pyproject.toml` `description` (154 chars) is unchanged; PyPI's 512-char summary cap is unaffected.
- The locked README hero positioning sentence (the long, "use verbatim" form) is unchanged.

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
