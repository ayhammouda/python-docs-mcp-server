# mcp-server-python-docs

<!-- mcp-name: io.github.ayhammouda/python-docs-mcp-server -->

[![CI](https://github.com/ayhammouda/python-docs-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/ayhammouda/python-docs-mcp-server/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![No API Keys](https://img.shields.io/badge/API%20keys-none-success)](#why-teams-like-this)
[![Official Python Docs](https://img.shields.io/badge/source-official%20python%20docs-informational)](https://docs.python.org/3/)

A read-only, version-aware MCP server for Python standard library
documentation, optimized for low-token, section-level retrieval.

It gives Claude, Cursor, Codex, and other MCP clients precise stdlib answers
without dumping whole documentation pages into the context window, without API
keys, and without depending on a hosted docs provider at query time.

## Why this exists

General-purpose docs retrieval is often noisy for Python stdlib questions:

- symbol lookups like `asyncio.TaskGroup` need exact resolution
- answers should be version-aware (`3.12` vs `3.13`)
- full-page fetches waste tokens when one section is enough
- official Python docs are the source of truth, but they are not packaged for
  MCP out of the box

This server builds a local index from the official Python documentation and
exposes a small MCP tool surface tuned for high-signal retrieval.

## Why teams like this

- no API keys to provision, rotate, or justify
- official Python docs are the source of truth
- local index, so runtime retrieval does not depend on a third-party hosted API
- read-only behavior with a simple security story
- easy to explain in environments where external dependencies raise friction

## What you get

- exact symbol lookup from Python `objects.inv`
- section-level retrieval with truncation and pagination
- local SQLite + FTS5 index with no runtime web scraping
- version-aware results across indexed Python versions
- a deliberately small, read-only MCP tool surface

## Quick example

**Prompt**

> What does `asyncio.TaskGroup` do in Python 3.13?

**Typical flow**

1. `search_docs("asyncio.TaskGroup", kind="symbol", version="3.13")`
2. Call `get_docs(...)` using the slug and anchor returned by the best hit

**Result**

The model gets the exact symbol match and the relevant documentation section
instead of a full-page dump.

## 30-second demo

Ask your MCP client:

> In Python 3.13, how should I use `asyncio.TaskGroup` and what changed from older asyncio patterns?

The agent should use `search_docs` for the exact symbol, then `get_docs` for the
matching section. Instead of generic web results or an entire docs page, it gets
the official stdlib text for the requested Python version, trimmed to the useful
section.

Local source smoke test until the PyPI package is published:

<!-- PRE-PYPI: replace this temporary GitHub source command after the first PyPI publish -->
```bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git mcp-server-python-docs --version
```
<!-- /PRE-PYPI -->

## Install

Until the first PyPI release is published, run from GitHub:

<!-- PRE-PYPI: replace this temporary GitHub source command after the first PyPI publish -->
```bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git mcp-server-python-docs --version
```
<!-- /PRE-PYPI -->

After PyPI publishing is complete, the package will also run directly with
`uvx`:

```bash
uvx mcp-server-python-docs --version
```

Or install it persistently:

```bash
pipx install mcp-server-python-docs
```

If `uv` is installed but the `uv` command is not on your `PATH`, reopen your
shell or use `python -m uv ...` as a fallback for local contributor commands.

## First run

Build the local documentation index:

<!-- PRE-PYPI: replace this temporary GitHub source command after the first PyPI publish -->
```bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git mcp-server-python-docs build-index --versions 3.10,3.11,3.12,3.13,3.14
```
<!-- /PRE-PYPI -->

After PyPI publishing, `uvx mcp-server-python-docs build-index ...` is enough.

If you installed the package persistently, you can drop the `uvx` prefix:

```bash
mcp-server-python-docs build-index --versions 3.10,3.11,3.12,3.13,3.14
```

This downloads Python's `objects.inv` files, clones CPython docs sources, runs
`sphinx-build -b json`, and writes an SQLite index to your local cache. Expect
the first build to take several minutes.

## Configure your MCP client

### Claude Desktop

Add this to your Claude Desktop configuration file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

**Linux:** `~/.config/Claude/claude_desktop_config.json`

**Windows:** `%APPDATA%\\Claude\\claude_desktop_config.json`

<!-- PRE-PYPI: replace this temporary GitHub source command after the first PyPI publish -->
```json
{
  "mcpServers": {
    "python-docs": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/ayhammouda/python-docs-mcp-server.git",
        "mcp-server-python-docs"
      ]
    }
  }
}
```
<!-- /PRE-PYPI -->

Restart Claude Desktop after editing the config file.

### Cursor

Add this to your Cursor MCP settings (`.cursor/mcp.json` in your project or
global settings):

<!-- PRE-PYPI: replace this temporary GitHub source command after the first PyPI publish -->
```json
{
  "mcpServers": {
    "python-docs": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/ayhammouda/python-docs-mcp-server.git",
        "mcp-server-python-docs"
      ]
    }
  }
}
```
<!-- /PRE-PYPI -->

### Codex

Add this to `.codex/config.toml`:

<!-- PRE-PYPI: replace this temporary GitHub source command after the first PyPI publish -->
```toml
[mcp_servers.python-docs]
command = "uvx"
args = ["--from", "git+https://github.com/ayhammouda/python-docs-mcp-server.git", "mcp-server-python-docs"]
```
<!-- /PRE-PYPI -->

## How quality is verified

The repo makes quality visible with reproducible checks instead of relying on
marketing claims.

- CI runs `ruff`, `pyright`, and `pytest` on macOS and Linux for Python 3.12
  and 3.13
- subprocess-based stdio hygiene and smoke tests protect the MCP protocol pipe
- packaging tests verify the wheel contents and CLI entry points
- curated retrieval regression tests cover exact symbol hits, version behavior,
  missing symbols, truncation, and local-version defaults
- manual MCP QA is documented in
  [`.github/INTEGRATION-TEST.md`](.github/INTEGRATION-TEST.md), with MCP
  Inspector as the fast-feedback loop and Claude/Cursor as real-client checks

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
| `detect_python_version` | Detect the user's local Python version and report whether it matches an indexed documentation version. |

## Why not Context7 or generic docs retrieval?

Use this server when the question is about Python's standard library and you
want the boring answer that is actually correct:

- official Python docs, not scraped mirrors or mixed-source summaries
- exact symbol resolution from `objects.inv`
- Python-version-aware results across 3.10 through 3.14
- token-efficient section retrieval instead of full-page dumps
- local, read-only runtime behavior with no API keys

Use Context7 or a generic docs fetcher when you need broader third-party library
coverage, arbitrary web pages, or cross-framework research. This server is not a
universal docs search engine. It is a precise stdlib retrieval tool for AI
coding agents.

## Retrieved docs cache

`get_docs` responses are cached across MCP client/server restarts in the
platform cache directory:

```text
<platform cache dir>/mcp-python-docs/retrieved-docs-cache.sqlite3
```

The cache stores completed `get_docs` results, including page/section content,
for the resolved Python docs version plus request identity (`slug`, optional
`anchor`, `max_chars`, and `start_index`). Cache misses fall back to the normal
local index retrieval path and then write the retrieved result.

Cache entries are also scoped to a fingerprint of the local `index.db` file
(path, size, and modification timestamp). If you rebuild or replace the local
docs index, older entries are ignored automatically instead of being returned
for the new index generation. Deleting `retrieved-docs-cache.sqlite3` is safe;
it only removes cached retrieval results, not the docs index.

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

Before PyPI publishing, run `doctor` from the GitHub source package:

<!-- PRE-PYPI: replace this temporary GitHub source command after the first PyPI publish -->
```bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git mcp-server-python-docs doctor
```
<!-- /PRE-PYPI -->

After PyPI publishing:

```bash
uvx mcp-server-python-docs doctor
```

This checks the runtime Python version, SQLite FTS5, cache/index paths, disk
space, and whether the current interpreter has the `venv`/`ensurepip` support
needed by `build-index`.

Validate an existing index.

Before PyPI publishing:

<!-- PRE-PYPI: replace this temporary GitHub source command after the first PyPI publish -->
```bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git mcp-server-python-docs validate-corpus
```
<!-- /PRE-PYPI -->

After PyPI publishing:

```bash
uvx mcp-server-python-docs validate-corpus
```

## Troubleshooting

### FTS5 unavailable

If you see an error about SQLite FTS5 not being available:

**Linux x86-64**

After PyPI publishing, Linux x86-64 users can install the optional bundled
SQLite package:

```bash
pip install 'mcp-server-python-docs[pysqlite3]'
```

**macOS / Windows / Linux ARM**

Install Python from [python.org](https://www.python.org/) or use:

```bash
uv python install
```

### Missing `pythonX.Y-venv` on Debian/Ubuntu

If `doctor` reports that build venv support is unavailable, install the venv
package for the same Python minor version that runs the server:

```bash
sudo apt install python3.12-venv
```

Adjust `3.12` to match the version shown by `doctor`. Without this package,
`build-index` cannot create the disposable Sphinx environment it uses to build
JSON documentation content.

### `uvx` cache stale

After PyPI publishing, if `uvx mcp-server-python-docs` runs an old version:

```bash
uvx --reinstall mcp-server-python-docs
```

Or clear the uv cache:

```bash
uv cache clean mcp-server-python-docs
```

### Claude Desktop on Windows (MSIX)

The MSIX-packaged version of Claude Desktop on Windows may have restricted PATH
access. If `uvx` is not found, specify the full path in your config:

```json
{
  "mcpServers": {
    "python-docs": {
      "command": "C:\\Users\\YOU\\.local\\bin\\uvx.exe",
      "args": ["mcp-server-python-docs"]
    }
  }
}
```

Replace `YOU` with your Windows username. Find the exact path with `where uvx`.

### Restart after rebuild

After running `build-index`, restart your MCP client so it picks up the new
database file. The server opens the index read-only at startup and does not
hot-reload an updated database.

On Windows, close the MCP client before rebuilding if the index file is locked.

## Contributor workflow

For contributor setup and verification:

- [`CONTRIBUTING.md`](CONTRIBUTING.md)
- [`.github/INTEGRATION-TEST.md`](.github/INTEGRATION-TEST.md)
- [`.github/RELEASE.md`](.github/RELEASE.md)

## Support

Tested on macOS and Linux. Windows should work, but it is not verified on
every release.

Python documentation versions 3.10 through 3.14 are currently supported.

The server requires Python 3.12+ to run. Its generated documentation corpus can
still index Python documentation versions 3.10 through 3.14.

## License

MIT
