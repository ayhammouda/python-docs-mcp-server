# [v0.3.0] docs — refresh public surfaces to the 6-tool surface

> **Confidence:** HIGH · **Wave:** lead · **Slug:** `readme-glama-six-tool-refresh`
> Create with: `gh issue create -F .planning/issues/v0.3.0/02-readme-glama-six-tool-refresh.md -l agent-ready,documentation,priority:P2`
> Branch: `agent/<issue-number>-readme-glama-six-tool-refresh`

## Context

- **Per-issue context file (read first):** [`.planning/agent-context/readme-glama-six-tool-refresh.md`](../../agent-context/readme-glama-six-tool-refresh.md)
- Pipeline: [`AGENT-EXECUTION-PIPELINE.md`](../../../AGENT-EXECUTION-PIPELINE.md)
- Roadmap: [`STRATEGIC-ROADMAP-2026-05-29.md`](../../../STRATEGIC-ROADMAP-2026-05-29.md) §3, §4 (v0.3.0), decision **5.9** (this becomes a release-cycle discipline)
- Tool registration order of truth: `src/mcp_server_python_docs/server.py` (`@mcp.tool` order)

## Goal

Make every public-facing surface consistently describe the six-tool surface
(including `compare_versions`) and codify the refresh as a release-cycle step.

## Acceptance criteria

- [ ] `README.md` `## Tools` section lists exactly six rows, including `compare_versions`, in the same order as the `@mcp.tool` declarations in `server.py` (search_docs, get_docs, lookup_package_docs, list_versions, detect_python_version, compare_versions). Verify: the six tool names appear once each in the table.
- [ ] The stale `MCP%20Registry-v0.1.4` badge in `README.md` (line ~12) is updated to the current published registry version (`0.2.1`) **or** made version-agnostic; no badge advertises a version older than the latest release.
- [ ] `grep -rin 'five tools\|5 tools\|exposes five' README.md glama.json server.json` returns zero hits.
- [ ] `glama.json` `description` is accurate for the current surface and does not contradict the 6-tool README.
- [ ] `.github/RELEASE.md` gains a checklist line establishing decision 5.9: "Refresh README `## Tools`, `glama.json`, and registry/version badges to match the current tool surface." (one line; existing content untouched.)

## Scope boundaries

**In scope:** `README.md` body below the hero (the `## Tools` table, prose tool counts, the registry/version badge), `glama.json` description, and one checklist line in `.github/RELEASE.md`.

**Out of scope (stop and comment if required):**
- The `README.md` **hero section** (everything above the first install code block) — forbidden territory.
- `pyproject.toml` — the PyPI *short* description and `[project]` metadata derive from here and are forbidden. If the short description is stale, **comment, do not edit**.
- `server.json` `version` / package versions — release-managed, not part of this doc refresh.
- The line `all five versions` in `.github/INTEGRATION-TEST.md` — that refers to the **five Python versions** (3.10–3.14), not five tools. Do **not** "fix" it.

## Forbidden-territory reminders (pipeline §2)

- `README.md` hero section — do not touch.
- `pyproject.toml [project]` — do not touch.
- This PR will touch `README.md`, `.github/RELEASE.md`, and `glama.json`, all of which are CODEOWNERS-owned. Expect required maintainer review; that is correct, not a defect.

## Validation commands (pipeline §5)

```bash
uv run ruff check src/ tests/
uv run pyright src/
uv run pytest --tb=short -q
uv run python-docs-mcp-server doctor
# README/metadata consistency is exercised by:
uv run pytest tests/test_packaging.py tests/test_release_metadata.py -q
```

## PR template & recovery

- Use `.github/PULL_REQUEST_TEMPLATE/agent.md`. Under "Why this triggered human review", note: "Touches CODEOWNERS-owned brand/release docs (`README.md`, `.github/RELEASE.md`); opened for review, not auto-merge."
- Blocked? Stop, `WORKING-NOTES.md`, comment per §8.

## Effort estimate

~1 hour.
