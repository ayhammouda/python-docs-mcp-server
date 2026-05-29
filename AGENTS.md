# Repository Guidance

## Project

`python-docs-mcp-server` is a read-only MCP server for the official Python
standard library documentation. It is built for end users who want precise,
version-aware stdlib answers inside MCP clients such as Claude, Cursor, and
Codex without relying on an external hosted docs API at query time.

The repo's public credibility matters. Prefer changes that make the project
easier to trust, easier to verify, and easier to contribute to over changes
that merely add more AI or MCP setup.

## Autonomous Agent Execution

A portion of this project is executed by autonomous coding agents working
unattended against GitHub issues. If you are such an agent — or are scoping
work for one — **`AGENT-EXECUTION-PIPELINE.md` is mandatory reading before you
touch anything.** It defines the forbidden territory (don't-touch paths), the
required issue structure, the canonical validation gate, the human-review
triggers, and the recovery procedure. The *what and why* lives in
`STRATEGIC-ROADMAP-2026-05-29.md`; the *how, with what guardrails* lives in the
pipeline doc. Agents work on branches only, every PR needs human review, and
auto-merge is forbidden. Per-issue working context lives under
`.planning/agent-context/<issue-slug>.md`.

## Canonical Commands

If `uv` is not installed yet:

```bash
python -m pip install uv
```

Bootstrap the repo:

```bash
uv sync --dev
```

If `uv` is not on your `PATH` after installation, reopen the shell or use
`python -m uv ...` as a fallback.

Core verification commands:

```bash
uv run ruff check src/ tests/
uv run pyright src/
uv run pytest --tb=short -q
```

Build and inspect a local docs index:

```bash
uv run python-docs-mcp-server build-index --versions 3.12,3.13
uv run python-docs-mcp-server doctor
uv run python-docs-mcp-server validate-corpus
```

Package smoke check:

```bash
uv build
```

## MCP Testing Flow

Use this order when validating MCP behavior:

1. Run the automated checks above.
2. Use MCP Inspector for quick local iteration.
3. Confirm real-client behavior with the runbook in `.github/INTEGRATION-TEST.md`.

Client-facing integration and release runbooks live here:

- `.github/INTEGRATION-TEST.md`
- `.github/RELEASE.md`

## Done Means

Before calling work complete:

- relevant lint, typecheck, and test commands have been run fresh
- user-facing docs reflect the current behavior
- MCP-related changes still work in the documented validation flow
- no runtime API/tool surface changes were made unless explicitly requested

## AI and MCP Policy

- Use official documentation first for MCP, OpenAI/Codex, and Python SDK questions.
- Avoid MCP sprawl. Do not add new MCP servers unless they clearly improve this
  project's development or user experience.
- Do not add repo-local custom skills by default. Add one only if a repeated
  workflow is genuinely painful and no strong public pattern already covers it.
- Follow existing test and documentation patterns before inventing new structure.

## Context Hygiene

`.planning/` mixes archival history with forward-facing phase CONTEXTs.

- `.planning/ROADMAP.md` and `.planning/phases/0X-…/0X-CONTEXT.md` are live,
  forward-looking specs — read these first when starting a new phase.
- Anything else in `.planning/` (especially content dated 2026-04 or earlier)
  is archival history. It may help maintainers reconstruct prior context but
  should not drive routine implementation decisions.
- For the source of truth about *what the code does today*, start with:
  - `README.md`
  - `CONTRIBUTING.md`
  - `.github/INTEGRATION-TEST.md`
  - `tests/`
