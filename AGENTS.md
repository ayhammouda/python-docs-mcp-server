# Repository Guidance

## Project

`mcp-server-python-docs` is a read-only MCP server for the official Python
standard library documentation. It is built for end users who want precise,
version-aware stdlib answers inside MCP clients such as Claude, Cursor, and
Codex without relying on an external hosted docs API at query time.

The repo's public credibility matters. Prefer changes that make the project
easier to trust, easier to verify, and easier to contribute to over changes
that merely add more AI or MCP setup.

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
uv run mcp-server-python-docs build-index --versions 3.12,3.13
uv run mcp-server-python-docs doctor
uv run mcp-server-python-docs validate-corpus
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

Treat `.planning/` as archival project history, not live repo truth.

Start with these files instead:

- `README.md`
- `CONTRIBUTING.md`
- `.github/INTEGRATION-TEST.md`
- `tests/`

The generated planning files may still be useful for maintainers who want the
old GSD workflow context, but they should not drive routine implementation
decisions.
