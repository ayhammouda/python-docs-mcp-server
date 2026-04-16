---
phase: 7
plan: d
title: "README & CI Configuration"
wave: 2
depends_on:
  - 07a
  - 07b
  - 07c
files_modified:
  - README.md
  - .github/workflows/ci.yml
requirements:
  - SHIP-03
  - SHIP-04
  - SHIP-05
  - TEST-06
autonomous: true
---

# Plan 07d: README & CI Configuration

<objective>
Write the README.md with copy-paste mcpServers config snippets for Claude Desktop (macOS/Linux/Windows paths), install section (uvx), first-run section (build-index), and troubleshooting. Also create CI configuration for GitHub Actions that runs the full test suite on macOS + Linux with Python 3.12 and 3.13.
</objective>

## Tasks

<task id="1">
<title>Write comprehensive README.md</title>

<read_first>
- README.md
- pyproject.toml
- src/mcp_server_python_docs/__main__.py
- python-docs-mcp-server-build-guide.md (lines 550-580, distribution section)
- .planning/REQUIREMENTS.md (SHIP-03, SHIP-04, SHIP-05 definitions)
</read_first>

<action>
Replace the placeholder `README.md` with full documentation. The exact content structure:

```markdown
# mcp-server-python-docs

A read-only, version-aware MCP server for Python standard library documentation. Gives Claude and other LLM clients precise, section-level answers to Python stdlib questions — without flooding the context window with entire doc pages.

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
```

Key requirements fulfilled:
- SHIP-03: Copy-paste `mcpServers` config for Claude Desktop with macOS/Linux/Windows paths
- SHIP-04: Install section (uvx), first-run section (build-index --versions 3.12,3.13), troubleshooting covering FTS5, uvx cache, MSIX on Windows, restart-after-rebuild
- SHIP-05: Support section with "Tested on macOS and Linux; Windows should work... but is not verified on every release"
</action>

<acceptance_criteria>
- `README.md` contains `## Install` section with `uvx mcp-server-python-docs`
- `README.md` contains `## First Run` section with `build-index --versions 3.12,3.13`
- `README.md` contains `## Configure Your MCP Client` section
- `README.md` contains `mcpServers` JSON snippet for Claude Desktop
- `README.md` contains config file paths for macOS, Linux, and Windows
- `README.md` contains `## Troubleshooting` section
- `README.md` troubleshooting covers "FTS5 unavailable" with platform-specific instructions
- `README.md` troubleshooting covers "uvx cache stale" with `--reinstall` command
- `README.md` troubleshooting covers "Claude Desktop on Windows (MSIX)" with full path example
- `README.md` troubleshooting covers "Restart after rebuild"
- `README.md` contains `## Support` section with "Tested on macOS and Linux; Windows should work (uses platformdirs + pathlib) but is not verified on every release"
- `README.md` contains `## Diagnostics` section mentioning `doctor` command
</acceptance_criteria>
</task>

<task id="2">
<title>Create GitHub Actions CI configuration</title>

<read_first>
- pyproject.toml
- tests/conftest.py
</read_first>

<action>
Create `.github/workflows/ci.yml` with a GitHub Actions workflow that runs the full test suite on macOS + Linux with Python 3.12 and 3.13.

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        python-version: ["3.12", "3.13"]

    runs-on: ${{ matrix.os }}
    name: Test (Python ${{ matrix.python-version }}, ${{ matrix.os }})

    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python ${{ matrix.python-version }}
        run: uv python install ${{ matrix.python-version }}

      - name: Install dependencies
        run: uv sync --dev

      - name: Run linter
        run: uv run ruff check src/ tests/

      - name: Run type checker
        run: uv run pyright src/

      - name: Run tests
        run: uv run pytest --tb=short -q

      - name: Verify wheel contents
        run: |
          uv build
          python -c "
          import zipfile, glob, sys
          wheels = glob.glob('dist/*.whl')
          if not wheels:
              print('No wheel found')
              sys.exit(1)
          with zipfile.ZipFile(wheels[0]) as zf:
              names = zf.namelist()
              if not any('synonyms.yaml' in n for n in names):
                  print('synonyms.yaml not found in wheel')
                  sys.exit(1)
              print('Wheel contents OK')
          "
```

Key CI features:
- 2x2 matrix: (ubuntu-latest, macos-latest) x (Python 3.12, 3.13) = 4 jobs
- Uses `astral-sh/setup-uv@v4` for uv installation
- Runs ruff lint, pyright type check, pytest, and wheel content verification
- `fail-fast: false` so all matrix combinations run even if one fails
- Windows is NOT in the matrix (best-effort, not gate-blocking per TEST-06)
</action>

<acceptance_criteria>
- `.github/workflows/ci.yml` exists
- CI matrix includes `ubuntu-latest` and `macos-latest`
- CI matrix includes Python `3.12` and `3.13`
- CI runs `ruff check`, `pyright`, `pytest`, and wheel content check
- CI uses `uv sync --dev` for dependency installation
- Windows is NOT in the matrix (per TEST-06: best-effort only)
- YAML is valid (no syntax errors)
</acceptance_criteria>
</task>

## Verification

```bash
# Verify README has all required sections
grep -c "## Install" README.md
grep -c "## First Run" README.md
grep -c "mcpServers" README.md
grep -c "## Troubleshooting" README.md
grep -c "FTS5" README.md
grep -c "MSIX" README.md
grep -c "Restart after rebuild" README.md
grep -c "## Support" README.md
grep "not verified on every release" README.md

# Verify CI config
cat .github/workflows/ci.yml | head -30
grep "ubuntu-latest" .github/workflows/ci.yml
grep "macos-latest" .github/workflows/ci.yml
grep "3.12" .github/workflows/ci.yml
grep "3.13" .github/workflows/ci.yml

# Validate YAML syntax
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

<must_haves>
- README has copy-paste mcpServers config with macOS/Linux/Windows paths
- README has install section with uvx
- README has first-run section with build-index --versions 3.12,3.13
- README troubleshooting covers: FTS5, uvx stale cache, MSIX Windows, restart-after-rebuild
- README Support section: "Tested on macOS and Linux; Windows should work... not verified on every release"
- CI runs on macOS + Linux, Python 3.12 + 3.13
- CI runs linter, type checker, tests, and wheel check
</must_haves>
