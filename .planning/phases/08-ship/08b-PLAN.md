---
phase: 8
plan: 2
title: "Manual Integration Test Checklists for Claude Desktop and Cursor"
wave: 1
depends_on: []
files_modified:
  - .github/INTEGRATION-TEST.md
requirements:
  - SHIP-01
  - SHIP-02
autonomous: true
---

# Plan 08-02: Manual Integration Test Checklists for Claude Desktop and Cursor

<objective>
Create a manual integration test checklist that a human operator follows to verify the MCP server works end-to-end with Claude Desktop and Cursor, covering SHIP-01 and SHIP-02.
</objective>

## Tasks

<task id="1">
<title>Create integration test checklist document</title>

<read_first>
- README.md
- pyproject.toml
</read_first>

<action>
Create `.github/INTEGRATION-TEST.md` with the following content:

```markdown
# Integration Test Checklist

Manual verification steps for mcp-server-python-docs v0.1.0.
These tests require human execution -- they cannot be automated.

## Prerequisites

- [ ] All CI tests pass on main branch
- [ ] `mcp-server-python-docs doctor` reports all checks PASS
- [ ] Index is built: `mcp-server-python-docs build-index --versions 3.12,3.13`

## Test 1: Claude Desktop Integration (SHIP-01)

### Setup

1. Open Claude Desktop settings (Developer > Edit Config or `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS)
2. Add the following to `mcpServers`:
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
3. Restart Claude Desktop
4. Verify the MCP server icon appears in the chat input area

### Test Steps

- [ ] **T1.1**: Ask Claude: "what is asyncio.TaskGroup"
  - **Expected**: Response references `asyncio.TaskGroup` with a URI containing `library/asyncio-task.html`
  - **Expected**: Response includes symbol information (not just generic LLM knowledge)
  - **Actual result**: _______________

- [ ] **T1.2**: Ask Claude: "how do I use pathlib.Path.glob"
  - **Expected**: Response references `pathlib` documentation with relevant section content
  - **Actual result**: _______________

- [ ] **T1.3**: Ask Claude: "search for json parsing in Python"
  - **Expected**: Response includes hits from the `json` module documentation
  - **Actual result**: _______________

- [ ] **T1.4**: Verify no errors in Claude Desktop developer console
  - **Expected**: No MCP protocol errors or connection drops
  - **Actual result**: _______________

### Teardown

- Remove the `python-docs` entry from `mcpServers` (or keep for ongoing use)

## Test 2: Cursor Integration (SHIP-02)

### Setup

1. Open Cursor Settings > MCP
2. Add a new MCP server:
   - **Name**: python-docs
   - **Command**: `uvx`
   - **Args**: `mcp-server-python-docs`
3. Verify the server shows as connected (green indicator)

### Test Steps

- [ ] **T2.1**: In a chat or Composer session, ask: "what is asyncio.TaskGroup"
  - **Expected**: Response references `asyncio.TaskGroup` with documentation content
  - **Expected**: The MCP tool call is visible in the chat
  - **Actual result**: _______________

- [ ] **T2.2**: Ask: "show me the docs for collections.OrderedDict"
  - **Expected**: Response includes `collections.OrderedDict` documentation
  - **Actual result**: _______________

- [ ] **T2.3**: Verify the server stays connected across multiple queries
  - **Expected**: No disconnections or "server not responding" errors
  - **Actual result**: _______________

### Teardown

- Remove or disable the python-docs MCP server in Cursor settings (or keep)

## Test 3: Fresh Install Verification (SHIP-06 partial)

### Setup

1. Create a throwaway virtualenv or use a machine without the package:
   ```bash
   # Option A: Fresh venv
   uv venv /tmp/test-install && source /tmp/test-install/bin/activate

   # Option B: Use uvx (isolated by default)
   # No setup needed -- uvx creates its own isolated env
   ```

### Test Steps

- [ ] **T3.1**: Install from PyPI (after package is published):
  ```bash
  uvx mcp-server-python-docs --version
  ```
  - **Expected**: Prints `0.1.0`
  - **Actual result**: _______________

- [ ] **T3.2**: Build the index:
  ```bash
  uvx mcp-server-python-docs build-index --versions 3.12,3.13
  ```
  - **Expected**: Downloads objects.inv files, builds index, prints success message
  - **Actual result**: _______________

- [ ] **T3.3**: Run doctor:
  ```bash
  uvx mcp-server-python-docs doctor
  ```
  - **Expected**: All checks PASS
  - **Actual result**: _______________

- [ ] **T3.4**: Verify the full README install flow works end-to-end
  - **Expected**: Following README instructions from scratch produces a working server
  - **Actual result**: _______________

### Teardown

```bash
# Clean up throwaway venv if used
rm -rf /tmp/test-install
```

## Sign-Off

| Test | Pass/Fail | Tester | Date |
|------|-----------|--------|------|
| T1: Claude Desktop | | | |
| T2: Cursor | | | |
| T3: Fresh Install | | | |

**Release approved**: [ ] Yes / [ ] No -- needs fixes

**Notes**:
```

This document is the single artifact that covers SHIP-01, SHIP-02, and the verification portion of SHIP-06.
</action>

<acceptance_criteria>
- `.github/INTEGRATION-TEST.md` exists
- File contains Claude Desktop integration test section with `mcpServers` JSON config
- File contains Cursor integration test section with setup steps
- File contains fresh install verification section
- File includes the specific query "what is asyncio.TaskGroup" for both Claude Desktop and Cursor
- File includes checkbox items for each test step
- File includes a sign-off table
- Claude Desktop config uses `"command": "uvx", "args": ["mcp-server-python-docs"]`
</acceptance_criteria>
</task>

## Verification

<verification>
- [ ] `.github/INTEGRATION-TEST.md` covers SHIP-01 (Claude Desktop manual test)
- [ ] `.github/INTEGRATION-TEST.md` covers SHIP-02 (Cursor manual test)
- [ ] `.github/INTEGRATION-TEST.md` covers SHIP-06 partial (fresh install verification)
- [ ] All test steps have checkboxes and expected results
</verification>

<must_haves>
- SHIP-01: Manual integration test checklist for Claude Desktop
- SHIP-02: Manual integration test checklist for Cursor
</must_haves>
