# Reddit launch drafts

Do not post these until the package is published on PyPI and the release smoke
test passes. Until then, use the GitHub `uvx --from git+...` install command.

## r/Python

Title: I built a local MCP server for official Python stdlib docs

I built `python-docs-mcp-server`, a small read-only MCP server that gives AI
coding agents access to the official Python standard library docs.

It runs on Python 3.12+ and indexes docs for Python 3.10 through 3.14.

Why I wanted it:

- exact stdlib symbol lookup via Python `objects.inv`
- version-aware answers for Python 3.10 through 3.14
- section-level retrieval instead of dumping whole pages into context
- local SQLite/FTS index, no runtime web scraping, no API keys
- read-only MCP tools, so the operational/security story is boring

Example use case: ask an agent about `asyncio.TaskGroup` in Python 3.13 and it
can retrieve the exact official docs section instead of guessing from generic web
results.

Repo: https://github.com/ayhammouda/python-docs-mcp-server

Current note: PyPI publishing is still being finalized. For now, test from
source:

```bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git python-docs-mcp-server --version
```

Feedback welcome, especially on retrieval quality, MCP client setup, and which
stdlib docs workflows feel clunky in real use.

## r/LocalLLaMA

> Reminder: do not post until the package is published on PyPI and the release
> smoke test passes (see top-of-file gate).

Title: Local MCP server for official Python docs, built for coding agents

I made a local MCP server for Python standard library documentation. It builds a
local index from the official docs and exposes small read-only tools for search
and section retrieval.

It runs on Python 3.12+ and indexes docs for Python 3.10 through 3.14.

The goal is not to be a universal docs search engine. It is the opposite: a
boring, precise source for Python stdlib questions where version matters and
tokens are limited.

What it does:

- official Python docs only for stdlib retrieval
- Python-version-aware results across 3.10 through 3.14
- exact symbol lookup from `objects.inv`
- local SQLite + FTS5 index
- no API keys or hosted retrieval dependency at query time

Repo: https://github.com/ayhammouda/python-docs-mcp-server

PyPI publishing is not done yet, so don't use the plain `uvx` package command
until that lands. Source install smoke test:

```bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git python-docs-mcp-server --version
```

I'd appreciate practical feedback from people wiring MCP into local coding
workflows.

## r/ClaudeAI

> Reminder: do not post until the package is published on PyPI and the release
> smoke test passes (see top-of-file gate).

Title: Local MCP server that gives Claude precise Python stdlib docs

I built `python-docs-mcp-server`, a small read-only MCP server that drops into
Claude Desktop or Claude Code and gives Claude exact access to the official
Python standard library documentation.

It runs on Python 3.12+ and indexes docs for Python 3.10 through 3.14.

Why I built it:

- Claude is strong on Python but answers can drift between stdlib versions
- generic docs MCP servers pull in noise from broad web sources
- I wanted Claude to retrieve one official stdlib section, not a whole page
- the MCP tools are read-only, so the operational story is boring

How I use it: I ask Claude things like "in Python 3.13, what does
`asyncio.TaskGroup` change vs older asyncio patterns?" and it retrieves the
exact section from the official 3.13 docs instead of guessing from generic
web results.

Repo: https://github.com/ayhammouda/python-docs-mcp-server

PyPI publishing is still being finalized. For now, test from source:

```bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git python-docs-mcp-server --version
```

Feedback welcome on Claude Desktop / Claude Code MCP config, retrieval
quality, and which stdlib lookups feel clunky in real use.
