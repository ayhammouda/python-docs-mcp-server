---
plan: 08b
title: "Manual Integration Test Checklists for Claude Desktop and Cursor"
status: complete
started: 2026-04-16
completed: 2026-04-16
---

## Summary

Created `.github/INTEGRATION-TEST.md` with step-by-step manual integration test checklists for Claude Desktop (SHIP-01), Cursor (SHIP-02), and fresh install verification (SHIP-06 partial). Each test includes setup instructions, specific test queries (including the canonical `asyncio.TaskGroup` query), expected results, and a sign-off table for release approval.

## Self-Check: PASSED

- [x] `.github/INTEGRATION-TEST.md` exists
- [x] File contains Claude Desktop integration test with `mcpServers` JSON config
- [x] File contains Cursor integration test with setup steps
- [x] File contains fresh install verification section
- [x] File includes "what is asyncio.TaskGroup" query for both Claude Desktop and Cursor
- [x] File includes checkbox items for each test step
- [x] File includes sign-off table
- [x] Claude Desktop config uses `"command": "uvx", "args": ["mcp-server-python-docs"]`

## Key Files

### Created
- `.github/INTEGRATION-TEST.md` -- Manual integration test checklists (11 test steps)

## Deviations

None.
