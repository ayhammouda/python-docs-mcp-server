---
plan: 08c
title: "Release Tagging and PyPI Publication Instructions"
status: complete
started: 2026-04-16
completed: 2026-04-16
---

## Summary

Appended a complete v0.1.0 Release Checklist to `.github/RELEASE.md` with four ordered sections: pre-release verification (CI, local tests, integration test sign-off, doctor), PyPI Trusted Publishing one-time setup, tag creation and workflow monitoring, and post-release verification (PyPI visibility, attestation, fresh install, README flow, Claude Desktop re-test). All steps are human-executable with checkboxes.

## Self-Check: PASSED

- [x] `.github/RELEASE.md` contains `## v0.1.0 Release Checklist` section
- [x] Checklist includes `git tag -a v0.1.0` command with annotated message
- [x] Checklist includes `git push origin v0.1.0` command
- [x] Checklist includes post-release verification with `uvx mcp-server-python-docs --version`
- [x] Checklist includes PyPI attestation verification step
- [x] Checklist includes fresh install + README flow test
- [x] Checklist includes Claude Desktop re-verification with published package
- [x] Checklist references `.github/INTEGRATION-TEST.md` for pre-release integration tests
- [x] Steps are in correct dependency order (tests before tag, tag before verify)

## Key Files

### Modified
- `.github/RELEASE.md` -- Appended v0.1.0 Release Checklist (92 lines added)

## Deviations

None.
