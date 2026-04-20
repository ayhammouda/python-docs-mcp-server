# Manual MCP QA Runbook

Use this document for manual MCP validation during development and before a
release. The goal is to verify real client behavior after the automated test
suite passes.

Release-specific sign-off still lives in [`.github/RELEASE.md`](RELEASE.md).

## Prerequisites

- CI or local checks are green:
  - `uv run ruff check src/ tests/`
  - `uv run pyright src/`
  - `uv run pytest --tb=short -q`
- Local index build completed:
  - `uv run mcp-server-python-docs build-index --versions 3.12,3.13`
- Doctor passes:
  - `uv run mcp-server-python-docs doctor`
- If `uv` is not on `PATH`, use `python -m uv ...` instead

## Test 1: MCP Inspector quick loop

Use Inspector for fast local iteration before checking real clients.

### Start Inspector

```bash
npx @modelcontextprotocol/inspector uv --directory . run mcp-server-python-docs
```

### Verify

- [ ] Connect successfully over stdio
- [ ] Confirm the tool list includes:
  - `search_docs`
  - `get_docs`
  - `list_versions`
  - `detect_python_version`
- [ ] Call `search_docs` with query `asyncio.TaskGroup`, `kind="symbol"`, `version="3.13"`
  - Expected: exact symbol hit with `library/asyncio-task.html`
- [ ] Call `get_docs` for the returned slug and anchor
  - Expected: section-level documentation, not an unrelated page dump
- [ ] Call `list_versions`
  - Expected: indexed versions appear with the configured default version
- [ ] Call `detect_python_version`
  - Expected: returns local interpreter information without breaking the session
- [ ] Observe no protocol corruption or unexplained disconnects in Inspector

## Test 2: Claude Desktop integration

### Setup

1. Open Claude Desktop settings
2. Add this server config:

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

3. Fully restart Claude Desktop
4. Verify the MCP server appears in the chat UI

### Checks

- [ ] Ask: `what is asyncio.TaskGroup`
  - Expected: response uses stdlib documentation, not only model prior knowledge
- [ ] Ask: `how do I use pathlib.Path.glob`
  - Expected: response cites the right docs section
- [ ] Ask: `search for json parsing in Python`
  - Expected: response surfaces `json` docs results
- [ ] Check the Claude developer console
  - Expected: no protocol errors or repeated reconnect loops

## Test 3: Cursor integration

### Setup

1. Open Cursor MCP settings
2. Add a server:
   - Name: `python-docs`
   - Command: `uvx`
   - Args: `mcp-server-python-docs`
3. Confirm the server shows as connected

### Checks

- [ ] Ask: `what is asyncio.TaskGroup`
  - Expected: MCP tool usage is visible and the answer references the right docs
- [ ] Ask: `show me the docs for collections.OrderedDict`
  - Expected: response includes the relevant documentation section
- [ ] Ask a second or third follow-up query
  - Expected: the server stays connected across multiple calls

## Test 4: Fresh install verification

Use this when validating the published package or a clean local environment.

On Windows, close the MCP client before rebuilding if the live index file is
locked.

### Checks

- [ ] `uvx mcp-server-python-docs --version`
  - Expected: prints the current package version
- [ ] `uvx mcp-server-python-docs build-index --versions 3.12,3.13`
  - Expected: index build completes successfully
- [ ] `uvx mcp-server-python-docs doctor`
  - Expected: all required checks pass
- [ ] Follow the README from scratch
  - Expected: a new user can get to a working client configuration without using `.planning/`

## Evidence log

| Test | Pass/Fail | Tester | Date | Notes |
|------|-----------|--------|------|-------|
| Inspector quick loop | | | | |
| Claude Desktop | | | | |
| Cursor | | | | |
| Fresh install | | | | |
