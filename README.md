# mcp-server-python-docs

A read-only, version-aware MCP server for Python standard library documentation. Gives Claude and other LLM clients precise, section-level answers to Python stdlib questions -- without flooding the context window with entire doc pages.

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

The server exposes three MCP tools:

| Tool | Description |
|------|-------------|
| `search_docs` | Search Python stdlib docs by query. Supports symbol lookup (`asyncio.TaskGroup`), module search (`json`), and free-text search. Returns ranked hits with BM25 scoring and snippet excerpts. |
| `get_docs` | Retrieve a specific documentation section by slug and optional anchor. Returns markdown content with budget-enforced truncation and pagination. |
| `list_versions` | List all indexed Python versions with their metadata. |

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

Python 3.12 and 3.13 are supported. The index ships with documentation for both versions; queries default to 3.13 unless a specific version is requested.

## License

MIT
