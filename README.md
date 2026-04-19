# mcp-server-python-docs

A corporate-friendly, read-only, version-aware MCP server for Python standard library documentation, optimized for low-token, section-level retrieval.

It gives Claude, Cursor, and other MCP clients precise answers to Python stdlib questions without dumping whole documentation pages into the context window, without requiring API keys, and without depending on a hosted docs provider at query time.

## Why this exists

General-purpose doc retrieval is often noisy for Python stdlib questions:

- symbol lookups like `asyncio.TaskGroup` need exact resolution
- answers should be version-aware (`3.12` vs `3.13`)
- full-page fetches waste tokens when one section is enough
- official Python docs are the source of truth, but not packaged for MCP out of the box

This server builds a local index from official Python documentation and exposes a small MCP tool surface tuned for high-signal retrieval.

## Why teams like this

- no API keys to provision, rotate, or justify
- official Python docs as the source of truth
- local index, so runtime retrieval does not depend on a third-party hosted docs API
- read-only behavior with a simple security story
- easy to explain in corporate environments where external dependencies raise friction

## What you get

- exact symbol lookup from Python `objects.inv`
- section-level retrieval with truncation and pagination
- local SQLite + FTS5 index, no runtime web scraping
- version-aware results across indexed Python versions
- read-only MCP tools with deterministic behavior

## Quick example

**Prompt**

> What does `asyncio.TaskGroup` do in Python 3.13?

**Typical flow**

1. `search_docs("asyncio.TaskGroup", kind="symbol", version="3.13")`
2. call `get_docs(...)` using the slug and anchor returned by the best hit

**Result**

The model gets the exact symbol match and the relevant documentation section instead of a full-page dump.

## Install

```bash
uvx mcp-server-python-docs
```

Or for a persistent install:

```bash
pipx install mcp-server-python-docs
```

## First Run

After installing, build the documentation index:

```bash
mcp-server-python-docs build-index --versions 3.12,3.13
```

This downloads Python's `objects.inv` symbol inventories, clones CPython source for each version, runs `sphinx-build -b json` to produce structured docs, and writes an SQLite index to your local cache (~200 MB). The build takes 5-15 minutes depending on your machine and network speed.

## Configure Your MCP Client

### Claude Desktop

Add this to your Claude Desktop configuration file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

**Linux:** `~/.config/Claude/claude_desktop_config.json`

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "python-docs": {
      "command": "uvx",
      "args": ["mcp-server-python-docs"]
    }
  }
}
```

Restart Claude Desktop after editing the config file.

### Cursor

Add to your Cursor MCP settings (`.cursor/mcp.json` in your project or global settings):

```json
{
  "mcpServers": {
    "python-docs": {
      "command": "uvx",
      "args": ["mcp-server-python-docs"]
    }
  }
}
```

## Tools

The server currently exposes four MCP tools:

| Tool | Description |
|------|-------------|
| `search_docs` | Search Python stdlib docs by query. Supports symbol lookup (`asyncio.TaskGroup`), module search (`json`), and free-text search. Returns ranked hits with BM25 scoring and snippet excerpts. |
| `get_docs` | Retrieve a specific documentation page or section by slug and optional anchor. Returns markdown content with budget-enforced truncation and pagination. |
| `list_versions` | List all indexed Python versions with their metadata. |
| `detect_python_version` | Detect the user's local Python version and report whether it matches an indexed documentation version. Helpful when `get_docs` defaults to the local runtime version. |

The core docs surface is still intentionally small: search, retrieve, and inspect available versions. `detect_python_version` is a convenience helper for local workflows.

## Positioning

If you're evaluating whether this is useful in practice, the key point is simple:

**this is not a generic web fetcher for Python docs.**
It is a purpose-built MCP server for official Python documentation with exact symbol resolution, version awareness, token-efficient section retrieval, and a cleaner corporate story than API-key-based doc services.

Think of it as an MCP passthrough to the official Python docs, but indexed locally so LLMs can retrieve the right section without hauling entire pages into context.

## Diagnostics

Run the built-in health check to verify your environment:

```bash
mcp-server-python-docs doctor
```

This checks Python version, SQLite FTS5 availability, cache directory, index presence, and free disk space.

## Troubleshooting

### FTS5 unavailable

If you see an error about SQLite FTS5 not being available:

**Linux x86-64:**
```bash
pip install 'mcp-server-python-docs[pysqlite3]'
```

**macOS / Windows / Linux ARM:**
Install Python from [python.org](https://www.python.org/) or use:
```bash
uv python install
```

Python builds from python.org and `uv python install` include FTS5. Some Linux distribution Python packages strip FTS5 from SQLite.

### uvx cache stale

If `uvx mcp-server-python-docs` runs an old version:

```bash
uvx --reinstall mcp-server-python-docs
```

Or clear the uv cache:

```bash
uv cache clean mcp-server-python-docs
```

### Claude Desktop on Windows (MSIX)

The MSIX-packaged version of Claude Desktop on Windows may have restricted PATH access. If `uvx` is not found, specify the full path in your config:

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

Replace `YOU` with your Windows username. Find the exact path with `where uvx` in a terminal.

### Restart after rebuild

After running `build-index` to update the documentation index, you must restart your MCP client (Claude Desktop, Cursor, etc.) to pick up the new index. The server opens the database in read-only mode at startup and does not detect changes to the index file at runtime.

## Support

Tested on macOS and Linux. Windows should work (uses `platformdirs` + `pathlib` for cross-platform paths) but is not verified on every release.

Python 3.12 and 3.13 are supported. When `search_docs` is called without a version, it searches across indexed versions. When `get_docs` is called without a version, it can default to the detected local Python runtime if a matching index exists.

## License

MIT
