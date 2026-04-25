# Contributing to mcp-server-python-docs

Start here for current contributor workflow. You should not need `.planning/`
to set up, test, or validate the repo.

## 1. Install tooling

Install `uv` if you do not already have it:

```bash
python -m pip install uv
```

Then bootstrap the repo:

```bash
uv sync --dev
```

If `uv` is not on your `PATH` after installation, reopen the shell or use
`python -m uv ...` as a fallback.

## 2. Run the standard checks

Use the same commands the CI workflow runs:

```bash
uv run ruff check src/ tests/
uv run pyright src/
uv run pytest --tb=short -q
```

If you are working on retrieval behavior specifically, the curated regression
suite is:

```bash
uv run pytest tests/test_retrieval_regression.py -q
```

## 3. Build a local docs index

The server needs a local SQLite index before runtime validation:

```bash
uv run mcp-server-python-docs build-index --versions 3.12,3.13
uv run mcp-server-python-docs doctor
uv run mcp-server-python-docs validate-corpus
```

`build-index` downloads the symbol inventories, clones CPython docs sources,
runs the Sphinx JSON build, and writes the local cache database.

## 4. Validate MCP behavior

Use this validation order:

1. Run the automated checks.
2. Use MCP Inspector for fast local iteration.
3. Confirm client behavior in Claude Desktop and Cursor.

The detailed manual runbook lives in
[`.github/INTEGRATION-TEST.md`](.github/INTEGRATION-TEST.md).

## 5. Package and release checks

For a local package smoke check:

```bash
uv build
```

For release workflow details, PyPI trusted publishing setup, and the full
release checklist, see [`.github/RELEASE.md`](.github/RELEASE.md).

## Project conventions

- Keep the MCP tool surface small and read-only unless a change is explicitly
  justified.
- Prefer official docs and primary sources over community summaries when
  working on MCP/OpenAI/Python SDK behavior.
- Do not add repo-local custom skills by default.
- Do not treat `.planning/` as live repo truth. It is archival project history.
