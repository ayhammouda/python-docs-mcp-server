---
phase: 8
plan: 3
title: "Release Tagging and PyPI Publication Instructions"
wave: 2
depends_on:
  - 08-PLAN-01
  - 08-PLAN-02
files_modified:
  - .github/RELEASE.md
requirements:
  - SHIP-06
  - PKG-07
autonomous: true
---

# Plan 08-03: Release Tagging and PyPI Publication Instructions

<objective>
Document the exact steps to tag v0.1.0, trigger the release workflow, verify the PyPI publication, and re-verify README install instructions against the published package. SHIP-06 and PKG-07 are fulfilled by human execution of these steps after PLAN-01 (workflow) and PLAN-02 (integration tests) are done.
</objective>

## Tasks

<task id="1">
<title>Update RELEASE.md with complete v0.1.0 release checklist</title>

<read_first>
- .github/RELEASE.md
- .github/INTEGRATION-TEST.md
- .github/workflows/release.yml
- pyproject.toml
</read_first>

<action>
Update `.github/RELEASE.md` to append a `## v0.1.0 Release Checklist` section after the existing content. This section is the step-by-step runbook for the first release:

Append the following to the end of `.github/RELEASE.md`:

```markdown

## v0.1.0 Release Checklist

Complete these steps in order. Each step has a checkbox -- do not skip ahead.

### Pre-Release Verification

- [ ] All CI tests green on main: check https://github.com/<owner>/python-docs-mcp-server/actions/workflows/ci.yml
- [ ] Local test suite passes:
  ```bash
  uv run pytest --tb=short -q
  ```
- [ ] Version in `pyproject.toml` is `0.1.0`:
  ```bash
  grep '^version' pyproject.toml
  ```
- [ ] Integration tests from `.github/INTEGRATION-TEST.md` are complete and signed off
- [ ] Doctor subcommand passes:
  ```bash
  mcp-server-python-docs doctor
  ```

### PyPI Trusted Publishing Setup (one-time)

- [ ] PyPI pending publisher configured at https://pypi.org/manage/account/publishing/:
  - PyPI project name: `mcp-server-python-docs`
  - Owner: `<your-github-username>`
  - Repository: `python-docs-mcp-server`
  - Workflow name: `release.yml`
  - Environment name: `pypi`
- [ ] GitHub environment `pypi` created in repo Settings > Environments

### Tag and Release

- [ ] Create the annotated tag:
  ```bash
  git tag -a v0.1.0 -m "Release v0.1.0

  First public release of mcp-server-python-docs.

  A read-only, version-aware MCP retrieval server over Python
  standard library documentation (3.12 + 3.13).

  Installable via: uvx mcp-server-python-docs"
  ```
- [ ] Push the tag to trigger the release workflow:
  ```bash
  git push origin v0.1.0
  ```
- [ ] Monitor the workflow run: https://github.com/<owner>/python-docs-mcp-server/actions/workflows/release.yml
- [ ] Verify all three jobs pass: `build` -> `publish` -> `github-release`

### Post-Release Verification (SHIP-06)

- [ ] Package visible on PyPI: https://pypi.org/project/mcp-server-python-docs/0.1.0/
- [ ] Attestation visible on PyPI package page (look for "Provenance" badge)
- [ ] Fresh install test:
  ```bash
  # Clear any cached version
  uv cache clean mcp-server-python-docs 2>/dev/null || true

  # Install and verify version
  uvx mcp-server-python-docs --version
  # Expected output: 0.1.0
  ```
- [ ] Full README flow test (from a clean environment):
  ```bash
  # Step 1: Install
  uvx mcp-server-python-docs --version
  # Should print 0.1.0

  # Step 2: Build index
  uvx mcp-server-python-docs build-index --versions 3.12,3.13
  # Should complete successfully

  # Step 3: Doctor check
  uvx mcp-server-python-docs doctor
  # All checks should PASS
  ```
- [ ] Claude Desktop test with published package:
  Configure `mcpServers` with `uvx mcp-server-python-docs` and verify
  "what is asyncio.TaskGroup" returns a correct hit

### Release Complete

- [ ] GitHub Release exists with attached artifacts
- [ ] PyPI page shows 0.1.0 with attestation
- [ ] README install instructions verified end-to-end
- [ ] Tag v0.1.0 exists in git

**Release date**: _______________
**Released by**: _______________
```
</action>

<acceptance_criteria>
- `.github/RELEASE.md` contains `## v0.1.0 Release Checklist` section
- Checklist includes `git tag -a v0.1.0` command with annotated message
- Checklist includes `git push origin v0.1.0` command
- Checklist includes post-release verification with `uvx mcp-server-python-docs --version`
- Checklist includes PyPI attestation verification step
- Checklist includes fresh install + README flow test
- Checklist includes Claude Desktop re-verification with published package
- Checklist references `.github/INTEGRATION-TEST.md` for pre-release integration tests
</acceptance_criteria>
</task>

## Verification

<verification>
- [ ] `.github/RELEASE.md` contains complete v0.1.0 release runbook
- [ ] All steps are in correct dependency order (tests before tag, tag before verify)
- [ ] No step requires Claude/automation -- all steps are human-executable
- [ ] Post-release verification covers SHIP-06 (end-to-end README verification)
- [ ] Release workflow reference matches PKG-07 (PyPI publication via workflow)
</verification>

<must_haves>
- SHIP-06: Tag v0.1.0 with end-to-end README verification documented
- PKG-07: PyPI publication steps documented (triggered by tag push to release.yml)
</must_haves>
