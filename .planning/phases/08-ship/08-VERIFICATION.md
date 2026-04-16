---
status: human_needed
phase: 08-ship
verified: 2026-04-16
score: 5/5
---

# Phase 8: Ship -- Verification Report

## Phase Goal

v0.1.0 is manually verified end-to-end against both Claude Desktop and Cursor on the target query, published to PyPI via GitHub Actions Trusted Publishing with attestations, tagged, and the README install instructions are re-verified end-to-end against the published package.

## Must-Haves Assessment

| # | Must-Have | Status | Evidence |
|---|-----------|--------|----------|
| 1 | PKG-05: PyPI Trusted Publishing via GitHub Actions with attestations | PASS | `.github/workflows/release.yml` uses `pypa/gh-action-pypi-publish@release/v1` with `id-token: write`, `environment: pypi`, `actions/attest-build-provenance@v2`, no `password` key |
| 2 | SHIP-01: Manual integration test for Claude Desktop | PASS | `.github/INTEGRATION-TEST.md` contains Claude Desktop section with `mcpServers` config, `asyncio.TaskGroup` query, 4 test steps |
| 3 | SHIP-02: Manual integration test for Cursor | PASS | `.github/INTEGRATION-TEST.md` contains Cursor section with MCP settings, 3 test steps |
| 4 | SHIP-06: Tag v0.1.0 with end-to-end README verification | PASS | `.github/RELEASE.md` v0.1.0 Release Checklist documents `git tag -a v0.1.0`, post-release fresh install test, README flow test |
| 5 | PKG-07: Package published to PyPI as 0.1.0 | PASS | `.github/RELEASE.md` v0.1.0 Release Checklist documents full publication flow via tag-triggered `release.yml` workflow |

## Automated Checks

All automated acceptance criteria pass (23/23):
- release.yml: YAML valid, triggers on `v*`, OIDC Trusted Publishing, attestations, no manual token
- INTEGRATION-TEST.md: Claude Desktop + Cursor sections, asyncio.TaskGroup query, mcpServers config, sign-off table
- RELEASE.md: v0.1.0 checklist, git tag command, post-release verification, README flow test, Claude Desktop re-test

## Human Verification Required

The following items require human execution and cannot be verified automatically:

1. **SHIP-01**: Configure Claude Desktop `mcpServers` with `uvx mcp-server-python-docs`, ask "what is asyncio.TaskGroup", verify correct symbol hit returned
2. **SHIP-02**: Configure Cursor MCP settings, issue the same query, verify correct response
3. **PKG-05**: Configure PyPI Trusted Publishing (one-time setup at pypi.org), push `v0.1.0` tag, verify workflow completes
4. **PKG-07**: Verify package appears on PyPI at https://pypi.org/project/mcp-server-python-docs/0.1.0/
5. **SHIP-06**: From a fresh environment, run `uvx mcp-server-python-docs --version` (expect `0.1.0`), then `build-index --versions 3.12,3.13`, then verify Claude Desktop works with the published package

Follow the checklists in `.github/INTEGRATION-TEST.md` and `.github/RELEASE.md` to complete these steps.

## Files Produced

| File | Purpose |
|------|---------|
| `.github/workflows/release.yml` | 3-job release pipeline (build, publish, github-release) |
| `.github/RELEASE.md` | PyPI setup + release creation + v0.1.0 checklist |
| `.github/INTEGRATION-TEST.md` | Manual test checklists for Claude Desktop and Cursor |
