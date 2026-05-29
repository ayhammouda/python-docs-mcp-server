# v0.3.0 — Agent-Ready Issue Set

Generated from `STRATEGIC-ROADMAP-2026-05-29.md` §4/§9 and
`AGENT-EXECUTION-PIPELINE.md` §3–§13. Each issue here has a matching per-issue
context file under `.planning/agent-context/<slug>.md` (pipeline §12, decision 5.14).

GitHub issue numbers are filled in below as issues are created (post pre-flight).

## Wave order (by confidence)

| # | Issue | Confidence | Slug | GH # |
|---|-------|-----------|------|------|
| 01 | cache — add zstd codec layer | **HIGH** | `zstd-cache-codec` | [#46](https://github.com/ayhammouda/python-docs-mcp-server/issues/46) |
| 02 | docs — refresh public surfaces to 6-tool surface | **HIGH** | `readme-glama-six-tool-refresh` | [#47](https://github.com/ayhammouda/python-docs-mcp-server/issues/47) |
| 03 | security — PyYAML safe-loader audit | MEDIUM | `pyyaml-safe-loader-audit` | [#48](https://github.com/ayhammouda/python-docs-mcp-server/issues/48) |
| 04 | docs — write ADR-006 (Serialization) | MEDIUM | `adr-006-serialization` | [#49](https://github.com/ayhammouda/python-docs-mcp-server/issues/49) |
| 05 | docs — write ADR-001 (Source Adapters) | MEDIUM | `adr-001-source-adapters` | [#50](https://github.com/ayhammouda/python-docs-mcp-server/issues/50) |
| 06 | ingestion — pin CPython source by SHA | PARTIAL | `cpython-source-sha-pin` | [#51](https://github.com/ayhammouda/python-docs-mcp-server/issues/51) |

> **Live status:** Issues #46–#51 exist on GitHub with topical labels only.
> The `agent-ready` label is **withheld** until you complete the §10 read —
> applying it earlier would falsely signal "passed pre-flight." Apply it per
> issue once you've read it end-to-end.

**Lead four (obvious overnight wins, de-risk the rest):** 01, 02, 03, 04.
ADR-006 (04) leads the ADR work because it unblocks the v0.3.x `format` parameter.
05 and 06 trail; 06 is PARTIAL and **must** carry `🛑 needs-human-review` (it
produces SECURITY.md wording for a human and touches the supply-chain path).

## Explicitly NOT in the agent wave (human-led, roadmap §9.1)

- **30-minute TOON Python port audit** — subjective quality judgment.
- **Empirical token study** — methodology + corpus selection require judgment.
  (An agent *may* later scaffold the harness against a human-written
  `docs/architecture/TOKEN-STUDY-METHODOLOGY.md`, but that spec doesn't exist yet.)

## Pre-flight checklist (pipeline §10) — status

- [x] §9 context files exist on a branch: `AGENTS.md` updated (links pipeline),
      `.github/ISSUE_TEMPLATE/autonomous-agent.yml`, `.github/PULL_REQUEST_TEMPLATE/agent.md`,
      `.github/CODEOWNERS` created. **(Land these on `main` before queueing.)**
- [ ] §5 canonical gate passes on `main` from a clean clone (maintainer to confirm).
- [ ] Each issue read end-to-end by a human and labeled `agent-ready`.
- [x] `🛑 needs-human-review` and `agent-ready` labels created in the repo.
- [x] CODEOWNERS forces review on `pyproject.toml`, `.github/workflows/`, `LICENSE`,
      `README.md`, `.planning/POSITIONING.md`, `schema.sql` (and more — see file).
- [ ] Branch protection on `main` requires ≥1 human approval + "Require review
      from Code Owners" (maintainer to confirm in repo settings).
- [x] At least one issue ≤4h for a confidence-building first run: 02 (~1h), 03 (~1–1.5h).

## Per-issue maintainer pre-reqs

- **01 (zstd):** add `zstandard>=0.23.0` to `pyproject.toml [project].dependencies`
  and run `uv lock` **before** queueing — the agent cannot edit forbidden territory.

## How to create the issues

```bash
gh label create agent-ready --color 0E8A16 --description "Issue passed §10 pre-flight; scoped for an autonomous agent" 2>/dev/null || true
gh label create "🛑 needs-human-review" --color B60205 --description "Agent PR paused at a pipeline §7 trigger; human review required" 2>/dev/null || true

for f in 01 02 03 04 05 06; do
  gh issue create -F .planning/issues/v0.3.0/$f-*.md   # labels are embedded in each file's header note
done
```
