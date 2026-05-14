# python-docs-mcp-server

<!-- mcp-name: io.github.ayhammouda/python-docs-mcp-server -->

**For AI coding agents writing Python, `python-docs-mcp-server` is the canonical Python stdlib oracle: exact symbols, exact sections, exact versions — offline, *always free, always MIT*, token-frugal.**

[![CI](https://github.com/ayhammouda/python-docs-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/ayhammouda/python-docs-mcp-server/actions/workflows/ci.yml)
[![Security Audit](https://github.com/ayhammouda/python-docs-mcp-server/actions/workflows/security.yml/badge.svg)](https://github.com/ayhammouda/python-docs-mcp-server/actions/workflows/security.yml)
[![CodeQL](https://github.com/ayhammouda/python-docs-mcp-server/actions/workflows/codeql.yml/badge.svg)](https://github.com/ayhammouda/python-docs-mcp-server/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/ayhammouda/python-docs-mcp-server/badge)](https://scorecard.dev/viewer/?uri=github.com/ayhammouda/python-docs-mcp-server)
[![python-docs-mcp-server MCP server](https://glama.ai/mcp/servers/ayhammouda/python-docs-mcp-server/badges/score.svg)](https://glama.ai/mcp/servers/ayhammouda/python-docs-mcp-server)
[![MCP Registry](https://img.shields.io/badge/MCP%20Registry-v0.1.4-0f766e)](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.ayhammouda%2Fpython-docs-mcp-server)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![No API Keys](https://img.shields.io/badge/API%20keys-none-success)](#why-use-it)
[![Official Python Docs](https://img.shields.io/badge/source-official%20python%20docs-informational)](https://docs.python.org/3/)

Built for the moment your agent needs `asyncio.TaskGroup` signatures, `pathlib.Path` semantics, or what changed in 3.12 — *not* a web fetch, *not* a hosted API, *not* a vector store hallucinating section anchors. Just an indexed slice of `docs.python.org`, returned by symbol or by query, scoped to the version you actually ship on.

## Why this exists

Generic docs retrieval is a rough fit for Python stdlib questions:

- `asyncio.TaskGroup` should resolve to the actual symbol, not a fuzzy page hit
- Python version matters (`3.12` and `3.13` do not always say the same thing)
- fetching a whole page burns tokens when one section answers the question
- the official docs are canonical, but they do not ship as an MCP server

This server indexes the official docs locally and exposes a small set of MCP
tools for lookup and section retrieval.

## Why use it

- no API keys to manage
- queries run against a local index, not a hosted docs API
- results come from the official Python docs
- the server is read-only at runtime
- fewer dependencies to review in strict environments

## What you get

- symbol lookup through Python `objects.inv`
- page and section retrieval with truncation and pagination
- a local SQLite + FTS5 index; no runtime web scraping
- results for each Python version you index
- five read-only MCP tools

## Quick example

**Prompt**

> What does `asyncio.TaskGroup` do in Python 3.13?

**Typical flow**

1. `search_docs("asyncio.TaskGroup", kind="symbol", version="3.13")`
2. Call `get_docs(...)` using the slug and anchor returned by the best hit

**Result**

The model gets the matching symbol and the relevant docs section, not a
full-page dump.

## 30-second demo

Ask your MCP client:

> In Python 3.13, how should I use `asyncio.TaskGroup` and what changed from older asyncio patterns?

If setup is working, the client should use `search_docs` for the exact symbol,
then `get_docs` for the matching section. Instead of generic web results or an
entire docs page, it gets official stdlib text for the requested Python version,
trimmed to the section that matters.

## Install

Run directly with `uvx`:

```bash
uvx python-docs-mcp-server --version
```

Or install it once with `pipx`:

```bash
pipx install python-docs-mcp-server
```

---

If `uv` is installed but the `uv` command is not on your `PATH`, reopen your
shell or use `python -m uv ...` as a fallback for local contributor commands.

## First run

Build the local documentation index:

```bash
uvx python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14
```

If you installed the package persistently, you can drop the `uvx` prefix:

```bash
python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14
```

The first build downloads Python's `objects.inv` files, clones CPython docs
sources, runs `sphinx-build -b json`, and writes an SQLite index to your local
cache. It can take several minutes.

## Configure your MCP client

### Claude Desktop

Add this to your Claude Desktop configuration file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

**Linux:** `~/.config/Claude/claude_desktop_config.json`

**Windows:** `%APPDATA%\\Claude\\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "python-docs": {
      "command": "uvx",
      "args": ["python-docs-mcp-server"]
    }
  }
}
```

Restart Claude Desktop after editing the config file.

### Cursor

Add this to your Cursor MCP settings (`.cursor/mcp.json` in your project or
global settings):

```json
{
  "mcpServers": {
    "python-docs": {
      "command": "uvx",
      "args": ["python-docs-mcp-server"]
    }
  }
}
```

### Codex

Add this to `.codex/config.toml`:

```toml
[mcp_servers.python-docs]
command = "uvx"
args = ["python-docs-mcp-server"]
```

## Quality checks

- CI runs `ruff`, `pyright`, and `pytest` on macOS and Linux for Python 3.12
  and 3.13
- subprocess-based stdio and smoke tests cover the MCP protocol pipe
- packaging tests check the wheel contents and CLI entry points
- retrieval regression tests cover exact symbol hits, version behavior,
  missing symbols, truncation, and local-version defaults
- manual MCP QA lives in
  [`.github/INTEGRATION-TEST.md`](.github/INTEGRATION-TEST.md), with MCP
  Inspector for local checks and Claude/Cursor for real-client checks

Contributor commands and validation steps live in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Tools

The server currently exposes five MCP tools:

| Tool | Description |
|------|-------------|
| `search_docs` | Search Python stdlib docs by query. Supports symbol lookup (`asyncio.TaskGroup`), module search (`json`), and free-text search. Returns ranked hits with BM25 scoring and snippet excerpts. |
| `get_docs` | Retrieve a specific documentation page or section by slug and optional anchor. Returns markdown content with budget-enforced truncation and pagination. Retrieved results are cached on disk by Python docs version and request identity. |
| `lookup_package_docs` | Look up official PyPI package metadata and return package-declared documentation/homepage/source URLs. This is a controlled PyPI metadata lookup, not generic web search. |
| `list_versions` | List all indexed Python versions with metadata. |
| `detect_python_version` | Detect the user's local Python version and report whether that version has been indexed. |

## Why not Context7 or generic docs retrieval?

Use this server when you want precise local Python docs retrieval rather than
broad web search:

- official Python docs, not scraped mirrors or summaries
- exact symbol resolution from `objects.inv`
- version-aware results for Python 3.10 through 3.14
- section retrieval instead of full-page dumps
- PyPI-declared docs, homepage, and source links through `lookup_package_docs`
- local read-only runtime with no API keys

Use Context7 or a generic docs fetcher for third-party libraries, arbitrary web
pages, or framework research. This server is not a universal docs search engine;
it is a focused stdlib retrieval tool for AI coding agents.

## Retrieved docs cache

`get_docs` responses are cached across MCP client/server restarts in the
platform cache directory:

```text
<platform cache dir>/mcp-python-docs/retrieved-docs-cache.sqlite3
```

The cache stores completed `get_docs` results for the resolved Python docs
version plus request identity (`slug`, optional `anchor`, `max_chars`, and
`start_index`). Cache misses use the normal local index retrieval path and then
write the result.

Cache entries are also scoped to a fingerprint of the local `index.db` file
(path, size, and modification timestamp). If you rebuild or replace the local
docs index, older entries are ignored automatically. Deleting
`retrieved-docs-cache.sqlite3` is safe; it removes cached retrieval results, not
the docs index.

## PyPI package docs lookup

`lookup_package_docs` queries the official PyPI JSON API documented at
`https://docs.pypi.org/api/json/` (`GET /pypi/<project>/json`) and returns only
sources declared in that package's PyPI metadata: the PyPI project URL,
`docs_url`, `home_page`, and allowlisted `project_urls` labels such as
Documentation, Homepage, Source, and Repository.

The tool makes the trust boundary explicit with
`trust_boundary="pypi-declared-metadata"`. It does not crawl pages, perform web
search, or silently fall back to unofficial community mirrors.

## Diagnostics

Check the local environment:

```bash
uvx python-docs-mcp-server doctor
```

This checks the runtime Python version, SQLite FTS5, cache/index paths, disk
space, and the `venv`/`ensurepip` support needed by `build-index`.

Validate an existing index:

```bash
uvx python-docs-mcp-server validate-corpus
```

## Troubleshooting

### FTS5 unavailable

If your Python build does not include SQLite FTS5:

**Linux x86-64**

Linux x86-64 users can install the optional bundled SQLite package:

```bash
pip install 'python-docs-mcp-server[pysqlite3]'
```

**macOS / Windows / Linux ARM**

Install Python from [python.org](https://www.python.org/) or use:

```bash
uv python install
```

### Missing `pythonX.Y-venv` on Debian/Ubuntu

If `doctor` says build venv support is unavailable, install the venv package
for the same Python minor version that runs the server:

```bash
sudo apt install python3.12-venv
```

Adjust `3.12` to match the version shown by `doctor`. Without this package,
`build-index` cannot create the disposable Sphinx environment it uses to build
JSON documentation content.

### Migrating from the pre-rename CLI

Earlier development snapshots of this project used the PyPI name
`mcp-server-python-docs`. The published PyPI project is
`python-docs-mcp-server`. If your MCP client config still references
the old name via `uvx`, you will see a `Package not found` error,
because `uvx` resolves projects by PyPI name.

Change your config `args` from:

```json
"args": ["mcp-server-python-docs"]
```

to:

```json
"args": ["python-docs-mcp-server"]
```

The wheel still installs a legacy `mcp-server-python-docs` console
script for users who already have the package installed and invoke
the binary by name on `$PATH`. That script is an alias and will be
removed in a future release.

### `uvx` cache stale

If `uvx python-docs-mcp-server` runs an old version:

```bash
uvx --reinstall python-docs-mcp-server
```

Or clear the uv cache:

```bash
uv cache clean python-docs-mcp-server
```

### Claude Desktop on Windows (MSIX)

The MSIX-packaged version of Claude Desktop on Windows may have restricted PATH
access. If `uvx` is not found, specify the full path in your config:

```json
{
  "mcpServers": {
    "python-docs": {
      "command": "C:\\Users\\YOU\\.local\\bin\\uvx.exe",
      "args": ["python-docs-mcp-server"]
    }
  }
}
```

Replace `YOU` with your Windows username. Find the exact path with `where uvx`.

### Restart after rebuild

After running `build-index`, restart your MCP client so it picks up the new
database file. The server opens the index read-only on startup and does not
reload it while running.

On Windows, close the MCP client before rebuilding if the index file is locked.

## Contributor workflow

For contributor setup and verification:

- [`CONTRIBUTING.md`](CONTRIBUTING.md)
- [`.github/INTEGRATION-TEST.md`](.github/INTEGRATION-TEST.md)
- [`.github/RELEASE.md`](.github/RELEASE.md)

## Support

Tested on macOS and Linux. Windows should work, but it is not verified on
every release.

The server requires Python 3.12+ to run. Its generated documentation corpus
covers Python documentation versions 3.10 through 3.14.

## License

MIT
