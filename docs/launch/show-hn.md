# Show HN launch draft

Use the pre-PyPI draft for private feedback only. Submit the post-PyPI draft to
HN only after PyPI publishing is complete and the release smoke test passes.

## Pre-PyPI feedback draft

### Title

Feedback: Local MCP server for official Python standard library docs

### Post

Hi HN -- I built `python-docs-mcp-server`, a read-only MCP server that gives AI
coding agents precise access to the official Python standard library docs.

It runs on Python 3.12+ and indexes docs for Python 3.10 through 3.14.

The motivation: generic docs retrieval is often noisy for stdlib questions.
Python answers are sensitive to exact symbols and versions, and agents do not
need a whole docs page when one section answers the question.

What it does:

- builds a local SQLite/FTS index from official Python docs
- resolves exact symbols from Python `objects.inv`
- retrieves section-level docs with truncation and pagination
- supports version-aware lookup across Python 3.10 through 3.14
- runs locally, read-only, with no API keys

It is intentionally narrow. Use Context7 or generic retrieval for broad
third-party docs and web research. Use this when you want official stdlib docs
with less token waste.

Repo: https://github.com/ayhammouda/python-docs-mcp-server

PyPI publishing is still pending. Until that is finished, test from GitHub:

```bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git python-docs-mcp-server --version
```

I'd love feedback on the MCP interface, retrieval output, and whether the local
indexing flow is clear enough.

## Post-PyPI Show HN draft

### Title

Show HN: Local MCP server for official Python standard library docs

### Post

Hi HN -- I built `python-docs-mcp-server`, a read-only MCP server that gives AI
coding agents precise access to the official Python standard library docs.

It runs on Python 3.12+ and indexes docs for Python 3.10 through 3.14.

The motivation: generic docs retrieval is often noisy for stdlib questions.
Python answers are sensitive to exact symbols and versions, and agents do not
need a whole docs page when one section answers the question.

What it does:

- builds a local SQLite/FTS index from official Python docs
- resolves exact symbols from Python `objects.inv`
- retrieves section-level docs with truncation and pagination
- supports version-aware lookup across Python 3.10 through 3.14
- runs locally, read-only, with no API keys

Repo: https://github.com/ayhammouda/python-docs-mcp-server

First run after the PyPI release:

```bash
uvx python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14
```

Then wire `uvx python-docs-mcp-server` into your MCP client and try the README's
30-second demo. I'd love feedback on the MCP interface, retrieval output, and
whether the local indexing flow is clear enough.
